"""验证聊天翻译的扫描、响应轮询和报告节流。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT


PERFORMANCE_SCENARIOS = r'''
local json_core = dofile([[SCAN_CORE_PATH]])
local core = dofile([[TRANSLATE_CORE_PATH]])

local function make_message(slot, body)
    return {
        widget_slot = slot,
        event_slot = slot,
        owner_id = 17,
        body = body,
        proof = {},
    }
end

local function new_env(options)
    local env = {
        now = 0,
        clock_throw = false,
        heartbeat_dead = false,
        messages = {},
        slot_status = {},
        read_slots = {},
        scan_plan_calls = 0,
        responses = {},
        read_count = 0,
        submit_calls = {},
        response_calls = {},
        response_times = {},
        apply_calls = {},
        cancel_calls = {},
        output_times = {},
        outputs = {},
        apply_status = "called_confirmed",
        apply_throw = false,
    }
    local adapter = {
        now_ms = function()
            if env.clock_throw then error("PRIVATE_CLOCK") end
            return env.now
        end,
        heartbeat = function()
            if env.heartbeat_dead then return nil end
            return string.format("HD2CT1 %d\n", env.now)
        end,
        read_slot = function(slot)
            env.read_count = env.read_count + 1
            env.read_slots[#env.read_slots + 1] = slot
            if env.slot_status[slot] then return env.slot_status[slot] end
            local message = env.messages[slot]
            if message then return "ok", message end
            return "empty"
        end,
        submit = function(token, body)
            env.submit_calls[#env.submit_calls + 1] = {token = token, body = body}
            return true
        end,
        response = function(token)
            env.response_calls[#env.response_calls + 1] = token
            local times = env.response_times[token]
            if not times then times = {}; env.response_times[token] = times end
            times[#times + 1] = env.now
            return env.responses[token]
        end,
        apply = function(message, text)
            env.apply_calls[#env.apply_calls + 1] = {
                slot = message.widget_slot,
                text = text,
            }
            if env.apply_throw then error("PRIVATE_APPLY_FAILURE") end
            return env.apply_status
        end,
        cancel = function(token)
            env.cancel_calls[#env.cancel_calls + 1] = token
            return true
        end,
        output = function(manifest)
            env.output_times[#env.output_times + 1] = env.now
            env.outputs[#env.outputs + 1] = json_core.encode_json(manifest)
        end,
    }
    if type(options) == "table" and type(options.scan_plan) == "function" then
        adapter.scan_plan = function()
            env.scan_plan_calls = env.scan_plan_calls + 1
            return options.scan_plan(env)
        end
    end
    local opts = {target_verified = true, session_id = "performance_session"}
    for key, value in pairs(options or {}) do opts[key] = value end
    env.state = core.new(adapter, opts)
    return env
end

local function step_at(env, now)
    env.now = now
    return core.step(env.state)
end

local function seed_pending(env, count)
    for slot = 0, count - 1 do
        local token = "performance_session_" .. tostring(slot + 1)
        local message = make_message(slot, "private pending " .. tostring(slot))
        env.messages[slot] = message
        local item = {
            token = token,
            message = message,
            source_body = message.body,
            created_ms = env.now,
            translation = nil,
            display_text = nil,
            cancel_called = false,
        }
        env.state.pending[#env.state.pending + 1] = item
        env.state.pending_by_slot[slot] = item
        env.state.seen[slot] = {
            owner_id = message.owner_id,
            event_slot = message.event_slot,
            body = message.body,
        }
    end
end

local frequency = {}
for _, fps in ipairs({60, 144, 240}) do
    local env = new_env()
    step_at(env, 0)
    for frame = 1, fps do
        step_at(env, math.floor(frame * 1000 / fps))
    end
    frequency[#frequency + 1] = {
        fps = fps,
        reads = env.read_count,
        batches = env.read_count / core.MAX_SLOTS_PER_STEP,
        responses = #env.response_calls,
        applies = #env.apply_calls,
    }
end

local gated_idle = new_env()
local reference_idle = new_env()
local gated_core_calls = 0
local last_dispatch_ms
for now = 0, 10000, 10 do
    step_at(reference_idle, now)
    gated_idle.now = now
    local should_run, clock_error = core.should_step(gated_idle.state, now, last_dispatch_ms)
    if clock_error then error("unexpected clock error in idle gate") end
    if should_run then
        core.step(gated_idle.state)
        gated_core_calls = gated_core_calls + 1
    end
    last_dispatch_ms = now
end
local bad_clock_run, bad_clock_reason = core.should_step(gated_idle.state, -1, last_dispatch_ms)
local nonfinite_clock_run, nonfinite_clock_reason = core.should_step(gated_idle.state, math.huge, last_dispatch_ms)
local reversed_clock_run, reversed_clock_reason = core.should_step(
    gated_idle.state, last_dispatch_ms - 1, last_dispatch_ms)
local reports_match = #gated_idle.output_times == #reference_idle.output_times
for index = 1, math.min(#gated_idle.output_times, #reference_idle.output_times) do
    if gated_idle.output_times[index] ~= reference_idle.output_times[index] then reports_match = false end
end

local wrapper_original_calls = 0
local wrapper_probe_calls = 0
local wrapper_order = {}
local original_update = function(kind)
    wrapper_original_calls = wrapper_original_calls + 1
    wrapper_order[#wrapper_order + 1] = "original"
    if kind == "raise" then error("PRIVATE_UPDATE_EXCEPTION") end
    if kind == "zero" then return end
    return "first", nil, "third", nil
end
local wrapped_update = core.wrap_update_after(original_update, function()
    wrapper_probe_calls = wrapper_probe_calls + 1
    wrapper_order[#wrapper_order + 1] = "probe"
    if wrapper_probe_calls == 2 then error("PRIVATE_PROBE_EXCEPTION") end
    return false
end)
local function capture_returns(...)
    return {n = select("#", ...), ...}
end
local wrapper_first = capture_returns(wrapped_update("values"))
local wrapper_second = capture_returns(wrapped_update("values"))
local original_error_propagated = not pcall(wrapped_update, "raise")
local wrapper_zero = capture_returns(wrapped_update("zero"))

local latency = new_env({scan_plan = function() return {owner_id = 17, slots = {63}} end})
latency.messages[63] = make_message(63, "old baseline message")
step_at(latency, 0)
for now = 200, 3000, 200 do step_at(latency, now) end
local baseline_at_ms = latency.now
local baseline_remaining = core.manifest(latency.state).baseline_remaining
local baseline_reads = latency.read_count
local preexisting_submits = #latency.submit_calls
latency.messages[63] = make_message(63, "new message after baseline")
for now = 3200, 6200, 200 do
    step_at(latency, now)
    if #latency.submit_calls > 0 then break end
end
local discovery_at_ms = latency.now
local discovery_token = latency.submit_calls[1] and latency.submit_calls[1].token or nil
step_at(latency, discovery_at_ms + 1)
local response_calls_after_first = #latency.response_calls
local apply_calls_after_first = #latency.apply_calls
latency.responses[discovery_token] = "OK\n已处理"
step_at(latency, discovery_at_ms + 199)
local response_calls_before_due = #latency.response_calls
local apply_calls_before_due = #latency.apply_calls
step_at(latency, discovery_at_ms + 201)

local planned_empty = new_env({scan_plan = function(env)
    return {owner_id = 17, slots = env.scan_plan_slots}
end})
planned_empty.scan_plan_slots = {}
for slot = 0, 63 do planned_empty.scan_plan_slots[#planned_empty.scan_plan_slots + 1] = slot end
step_at(planned_empty, 0)
for now = 200, 3000, 200 do step_at(planned_empty, now) end
local planned_baseline_reads = planned_empty.read_count
local planned_frame_reads = {}
step_at(planned_empty, 3200)
planned_frame_reads[#planned_frame_reads + 1] = planned_empty.read_count - planned_baseline_reads
for frame = 1, 15 do
    local reads_before = planned_empty.read_count
    step_at(planned_empty, 3200 + frame * 10)
    planned_frame_reads[#planned_frame_reads + 1] = planned_empty.read_count - reads_before
end
local planned_total_reads = planned_empty.read_count - planned_baseline_reads
local planned_all_batches_bounded = true
for _, count in ipairs(planned_frame_reads) do
    if count ~= core.MAX_SLOTS_PER_STEP then planned_all_batches_bounded = false end
end
local before_next_plan = planned_empty.read_count
step_at(planned_empty, 3400)
local no_catchup_reads = planned_empty.read_count == before_next_plan
local next_plan_due = core.should_step(planned_empty.state, 4200, 3400)

local burst = new_env({scan_plan = function()
    return {owner_id = 17, slots = {10, 11, 12}}
end})
step_at(burst, 0)
for now = 200, 3000, 200 do step_at(burst, now) end
burst.messages[10] = make_message(10, "burst message one")
burst.messages[11] = make_message(11, "burst message two")
burst.messages[12] = make_message(12, "burst message three")
local burst_read_start = #burst.read_slots
step_at(burst, 3200)
local burst_first_frame_submits = #burst.submit_calls
local burst_gate_continues = core.should_step(burst.state, 3210, 3200)
step_at(burst, 3210)
local burst_second_frame_submits = #burst.submit_calls - burst_first_frame_submits
step_at(burst, 3220)
local burst_order_valid = #burst.submit_calls == 3
    and burst.read_slots[burst_read_start + 1] == 10
    and burst.read_slots[burst_read_start + 2] == 11
    and burst.read_slots[burst_read_start + 3] == 12

local capacity = new_env({scan_plan = function()
    return {owner_id = 17, slots = {5}}
end})
step_at(capacity, 0)
for now = 200, 3000, 200 do step_at(capacity, now) end
seed_pending(capacity, 32)
capacity.slot_status[5] = "deferred"
local capacity_reads_before = capacity.read_count
step_at(capacity, 3200)
local full_queue_preserved = capacity.read_count == capacity_reads_before
    and capacity.state.scan_plan_active and capacity.state.scan_plan_index == 1
capacity.responses["performance_session_2"] = "OK\ncapacity freed"
step_at(capacity, 3210)
local deferred_preserved = capacity.state.scan_plan_active and capacity.state.scan_plan_index == 1
capacity.slot_status[5] = nil
step_at(capacity, 3220)
local deferred_retried = capacity.read_slots[#capacity.read_slots - 1] == 5
    and capacity.read_slots[#capacity.read_slots] == 5
    and not capacity.state.scan_plan_active

local owner_change = new_env({scan_plan = function()
    return {owner_id = 18, reset_owner = true, slots = {}}
end})
step_at(owner_change, 0)
for now = 200, 3000, 200 do step_at(owner_change, now) end
owner_change.state.scan_owner_id = 17
seed_pending(owner_change, 1)
owner_change.messages[0] = make_message(0, "new owner baseline")
owner_change.messages[0].owner_id = 18
owner_change.state.attempted_writes[40] = {owner_id = 17, event_slot = 40, body = "old attempted"}
step_at(owner_change, 3200)

local owner_discovered = new_env({scan_plan = function()
    return {owner_id = 19, reset_owner = true, slots = {}}
end})
step_at(owner_discovered, 0)
for now = 200, 3000, 200 do step_at(owner_discovered, now) end
step_at(owner_discovered, 3200)

local fallback_hint = new_env({scan_plan = function(env)
    if env.scan_plan_calls == 1 then error("PRIVATE_HINT_EXCEPTION") end
    return {owner_id = 17, slots = {63}}
end})
fallback_hint.messages[63] = make_message(63, "fallback baseline")
step_at(fallback_hint, 0)
for now = 200, 3000, 200 do step_at(fallback_hint, now) end
fallback_hint.messages[63] = make_message(63, "fallback after hint error")
step_at(fallback_hint, 3200)
local fallback_first_scan_submits = #fallback_hint.submit_calls
step_at(fallback_hint, 4200)

local stalled = new_env()
step_at(stalled, 0)
local reads_before_stall = stalled.read_count
step_at(stalled, 5000)
local reads_after_stall = stalled.read_count
step_at(stalled, 5001)
local reads_after_short_frame = stalled.read_count

local pending = new_env()
step_at(pending, 0)
for now = 200, 3000, 200 do step_at(pending, now) end
seed_pending(pending, 32)
local pending_gate_last = 3000
local pending_gate_always_ran = true
for frame = 1, 32 do
    local now = 3000 + frame * 50
    pending.now = now
    local should_run, clock_error = core.should_step(pending.state, now, pending_gate_last)
    if clock_error or not should_run then
        pending_gate_always_ran = false
    else
        core.step(pending.state)
    end
    pending_gate_last = now
end
local first_pass_calls = #pending.response_calls
pending.responses = {}
for slot = 0, 31 do
    pending.responses["performance_session_" .. tostring(slot + 1)] = "OK\ntranslated"
end
for frame = 33, 96 do step_at(pending, 3000 + frame * 50) end
local first_poll_times = {}
local intervals_valid = true
local all_applied = true
for slot = 0, 31 do
    local token = "performance_session_" .. tostring(slot + 1)
    local times = pending.response_times[token] or {}
    first_poll_times[#first_poll_times + 1] = times[1] or -1
    if not times[1] or not times[2] or times[2] - times[1] < core.RESPONSE_POLL_MS then
        intervals_valid = false
    end
end
for _, item in ipairs(pending.state.pending) do
    if item.display_text ~= nil then all_applied = false end
end

local expires = new_env({scan_interval_ms = 0})
step_at(expires, 0)
for now = 1, 15 do step_at(expires, now) end
seed_pending(expires, 1)
local expires_created_ms = expires.now
step_at(expires, expires_created_ms + 59999)
local expired_before_ttl = #expires.cancel_calls
step_at(expires, expires_created_ms + 60000)

local reconnect = new_env()
step_at(reconnect, 0)
seed_pending(reconnect, 1)
reconnect.heartbeat_dead = true
step_at(reconnect, 200)
local inactive_pending = #reconnect.state.pending
local inactive_baseline_remaining = core.manifest(reconnect.state).baseline_remaining
local cancelled_on_disconnect = #reconnect.cancel_calls
reconnect.heartbeat_dead = false
step_at(reconnect, 400)
local recovery_remaining = core.manifest(reconnect.state).baseline_remaining

local report = new_env()
report.messages[0] = make_message(0, "PRIVATE_REPORT_BODY")
step_at(report, 0)
for now = 200, 5000, 200 do step_at(report, now) end
local reports_before_error = #report.outputs
report.clock_throw = true
step_at(report, 5001)

local invalid_options = new_env({scan_interval_ms = -1, response_poll_ms = 1.5})

-- 任意失败结果只消费一次写回；持续扫描同一消息不能重新提交，槽位换新消息仍可翻译。
local failed_writes = {}
for _, fps in ipairs({60, 144, 240}) do
    for _, status in ipairs({"deferred", "stale", "read_failed", "capacity",
        "called_unconfirmed", "disabled", "unexpected", "exception"}) do
        local env = new_env()
        for now = 0, 3000, 200 do step_at(env, now) end
        seed_pending(env, 1)
        env.responses["performance_session_1"] = "OK\ntranslated"
        env.apply_status = status
        env.apply_throw = status == "exception"
        for frame = 1, fps do step_at(env, 3000 + math.floor(frame * 1000 / fps)) end
        -- 全环扫描期间槽位读取失败，再恢复原消息，不能触发间接重试。
        env.slot_status[0] = "read_failed"
        for now = 4200, 7400, 200 do step_at(env, now) end
        env.slot_status[0] = nil
        for now = 7600, 11000, 200 do step_at(env, now) end
        local attempts = #env.apply_calls
        local cancelled = #env.cancel_calls
        local remaining = #env.state.pending
        local resubmitted = #env.submit_calls
        env.apply_throw = false
        env.apply_status = "called_confirmed"
        env.messages[0] = make_message(0, "new message after failed write")
        env.state.next_slot = 0
        -- seed_pending直接占用_1；真实新提交须使用下一个token。
        env.state.token_counter = 1
        local next_scan = math.max(11200, env.state.next_scan_ms or 11200)
        step_at(env, next_scan)
        local token = env.submit_calls[1] and env.submit_calls[1].token
        if token then env.responses[token] = "OK\nnew translation" end
        step_at(env, next_scan + 1)
        failed_writes[#failed_writes + 1] = {
            fps = fps, status = status, attempts = attempts, cancelled = cancelled,
            remaining = remaining, resubmitted = resubmitted,
            new_submits = #env.submit_calls, final_applies = #env.apply_calls,
        }
    end
end

RESULT = json_core.encode_json({
    failed_writes = failed_writes,
    defaults = {
        scan_interval_ms = core.SCAN_INTERVAL_MS,
        response_poll_ms = core.RESPONSE_POLL_MS,
        invalid_scan_interval_ms = invalid_options.state.scan_interval_ms,
        invalid_response_poll_ms = invalid_options.state.response_poll_ms,
    },
    frequency = frequency,
    idle_gate = {
        core_calls = gated_core_calls,
        frames = 1001,
        reads_match = gated_idle.read_count == reference_idle.read_count,
        heartbeat_checks_match = gated_idle.state.counters.heartbeat_checks
            == reference_idle.state.counters.heartbeat_checks,
        reports_match = reports_match,
        output_count = #gated_idle.output_times,
        first_report_ms = gated_idle.output_times[1],
        last_report_ms = gated_idle.output_times[#gated_idle.output_times],
        invalid_clock_run = bad_clock_run,
        invalid_clock_reason = bad_clock_reason,
        nonfinite_clock_run = nonfinite_clock_run,
        nonfinite_clock_reason = nonfinite_clock_reason,
        reversed_clock_run = reversed_clock_run,
        reversed_clock_reason = reversed_clock_reason,
    },
    update_wrapper = {
        original_calls = wrapper_original_calls,
        probe_calls = wrapper_probe_calls,
        first_returns = wrapper_first.n,
        first_values_match = wrapper_first[1] == "first" and wrapper_first[2] == nil
            and wrapper_first[3] == "third" and wrapper_first[4] == nil,
        second_returns = wrapper_second.n,
        second_values_match = wrapper_second[1] == "first" and wrapper_second[2] == nil
            and wrapper_second[3] == "third" and wrapper_second[4] == nil,
        zero_returns = wrapper_zero.n,
        original_error_propagated = original_error_propagated,
        original_before_probe = wrapper_order[1] == "original" and wrapper_order[2] == "probe",
    },
    scan_latency = {
        baseline_at_ms = baseline_at_ms,
        baseline_remaining = baseline_remaining,
        baseline_reads = baseline_reads,
        preexisting_submits = preexisting_submits,
        discovery_at_ms = discovery_at_ms,
        delay_ms = discovery_at_ms - baseline_at_ms,
        response_calls_after_first = response_calls_after_first,
        response_calls_before_due = response_calls_before_due,
        apply_calls_after_first = apply_calls_after_first,
        apply_calls_before_due = apply_calls_before_due,
        final_response_calls = #latency.response_calls,
        final_apply_calls = #latency.apply_calls,
        final_status = core.manifest(latency.state).status,
    },
    scan_plans = {
        planned_baseline_reads = planned_baseline_reads,
        planned_total_reads = planned_total_reads,
        planned_frame_reads = planned_frame_reads,
        planned_calls_after_exhaustion = planned_empty.scan_plan_calls,
        no_catchup_reads = no_catchup_reads,
        next_plan_due = next_plan_due,
        burst_submits = #burst.submit_calls,
        burst_first_frame_submits = burst_first_frame_submits,
        burst_second_frame_submits = burst_second_frame_submits,
        burst_order_valid = burst_order_valid,
        burst_gate_continues = burst_gate_continues,
        full_queue_preserved = full_queue_preserved,
        deferred_preserved = deferred_preserved,
        deferred_retried = deferred_retried,
        capacity_last_slots = {capacity.read_slots[#capacity.read_slots - 1], capacity.read_slots[#capacity.read_slots]},
        capacity_pending = #capacity.state.pending,
        capacity_plan_active = capacity.state.scan_plan_active,
        capacity_plan_index = capacity.state.scan_plan_index,
        owner_pending = #owner_change.state.pending,
        owner_cancelled = #owner_change.cancel_calls,
        owner_id = owner_change.state.scan_owner_id,
        owner_baseline_remaining = core.manifest(owner_change.state).baseline_remaining,
        attempted_write_preserved = owner_change.state.attempted_writes[40] ~= nil,
        unknown_owner_id = owner_discovered.state.scan_owner_id,
        unknown_owner_baseline_remaining = core.manifest(owner_discovered.state).baseline_remaining,
        unknown_owner_baseline_active = owner_discovered.state.baseline_active,
        fallback_first_scan_submits = fallback_first_scan_submits,
        fallback_final_submits = #fallback_hint.submit_calls,
        fallback_hint_calls = fallback_hint.scan_plan_calls,
        fallback_next_slot = fallback_hint.state.next_slot,
    },
    stall = {
        first_batch_reads = reads_before_stall,
        stalled_reads = reads_after_stall - reads_before_stall,
        short_frame_reads = reads_after_short_frame - reads_after_stall,
    },
    pending_fairness = {
        first_pass_calls = first_pass_calls,
        response_calls = #pending.response_calls,
        applied = #pending.apply_calls,
        pending = #pending.state.pending,
        first_poll_times = first_poll_times,
        intervals_valid = intervals_valid,
        all_applied = all_applied,
        gate_always_ran = pending_gate_always_ran,
    },
    expiration = {
        before_ttl_cancels = expired_before_ttl,
        after_ttl_cancels = #expires.cancel_calls,
        pending = #expires.state.pending,
        expired = core.manifest(expires.state).counters.expired,
    },
    reconnect = {
        pending_while_inactive = inactive_pending,
        baseline_remaining_while_inactive = inactive_baseline_remaining,
        cancelled_on_disconnect = cancelled_on_disconnect,
        baseline_remaining_on_recovery = recovery_remaining,
    },
    report = {
        output_times = report.output_times,
        reports_before_error = reports_before_error,
        final_output = report.outputs[#report.outputs],
        final_steps = core.manifest(report.state).counters.steps,
        output_count = #report.outputs,
        private_body_leaked = table.concat(report.outputs, "\n"):find("PRIVATE_REPORT_BODY", 1, true) ~= nil,
    },
})
'''


class ChatTranslatePerformanceTests(unittest.TestCase):
    """用LuaJIT mock验证默认节奏，不调用游戏或真实服务。"""

    @classmethod
    def setUpClass(cls) -> None:
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行性能状态机测试")
        cls.lua = LuaJIT(LUA_DLL)

    @classmethod
    def scenarios(cls) -> dict:
        script = PERFORMANCE_SCENARIOS.replace(
            "SCAN_CORE_PATH", (ROOT / "game" / "chat_probe_core.lua").as_posix()
        ).replace(
            "TRANSLATE_CORE_PATH", (ROOT / "game" / "chat_translate_core.lua").as_posix()
        )
        return json.loads(cls.lua.run(script))

    def test_default_scan_rate_and_full_ring_latency(self) -> None:
        result = self.scenarios()
        self.assertEqual(result["defaults"]["scan_interval_ms"], 1000)
        self.assertEqual(result["defaults"]["response_poll_ms"], 200)
        self.assertEqual(result["defaults"]["invalid_scan_interval_ms"], 1000)
        self.assertEqual(result["defaults"]["invalid_response_poll_ms"], 200)
        idle_gate = result["idle_gate"]
        self.assertEqual(idle_gate["core_calls"], 51, "idle frames should enter the core only at 5 Hz")
        self.assertTrue(idle_gate["reads_match"], "idle gate changed the scan read schedule")
        self.assertTrue(idle_gate["heartbeat_checks_match"], "idle gate changed heartbeat polling")
        self.assertTrue(idle_gate["reports_match"], "idle gate changed report timing")
        self.assertEqual(idle_gate["output_count"], 3)
        self.assertEqual(idle_gate["first_report_ms"], 0)
        self.assertEqual(idle_gate["last_report_ms"], 10000)
        self.assertFalse(idle_gate["invalid_clock_run"])
        self.assertEqual(idle_gate["invalid_clock_reason"], "invalid_clock")
        self.assertFalse(idle_gate["nonfinite_clock_run"])
        self.assertEqual(idle_gate["nonfinite_clock_reason"], "invalid_clock")
        self.assertFalse(idle_gate["reversed_clock_run"])
        self.assertEqual(idle_gate["reversed_clock_reason"], "clock_reversed")

        wrapper = result["update_wrapper"]
        self.assertEqual(wrapper["original_calls"], 4, "the original update must run on every frame")
        self.assertEqual(wrapper["probe_calls"], 2, "a probe error should stop only future probe calls")
        self.assertEqual(wrapper["first_returns"], 4)
        self.assertTrue(wrapper["first_values_match"], "multiple returns or trailing nil were lost")
        self.assertEqual(wrapper["second_returns"], 4)
        self.assertTrue(wrapper["second_values_match"], "probe exception changed original returns")
        self.assertEqual(wrapper["zero_returns"], 0, "zero returns were not preserved")
        self.assertTrue(wrapper["original_error_propagated"], "original update errors must propagate")
        self.assertTrue(wrapper["original_before_probe"], "probe ran before the original update")
        for sample in result["frequency"]:
            with self.subTest(fps=sample["fps"]):
                self.assertLessEqual(sample["batches"], 6)
                self.assertLessEqual(sample["reads"], 24)
                self.assertEqual(sample["responses"], 0)
                self.assertEqual(sample["applies"], 0)
        latency = result["scan_latency"]
        self.assertEqual(latency["baseline_remaining"], 0)
        self.assertEqual(latency["baseline_reads"], 64)
        self.assertLessEqual(latency["baseline_at_ms"], 3200)
        self.assertEqual(latency["preexisting_submits"], 0)
        self.assertLessEqual(latency["delay_ms"], 3200)

        plans = result["scan_plans"]
        self.assertEqual(plans["planned_baseline_reads"], 64)
        self.assertEqual(plans["planned_total_reads"], 64)
        self.assertEqual(plans["planned_frame_reads"], [4] * 16,
            "a bounded scan plan must consume at most four slots per frame")
        self.assertEqual(plans["planned_calls_after_exhaustion"], 1)
        self.assertTrue(plans["no_catchup_reads"], "scan-plan completion caused a catch-up scan")
        self.assertTrue(plans["next_plan_due"], "the next regular scan missed its one-second deadline")
        self.assertEqual(plans["burst_submits"], 3,
            "new messages in one plan must survive one-submit-per-frame throttling")
        self.assertEqual(plans["burst_first_frame_submits"], 1)
        self.assertEqual(plans["burst_second_frame_submits"], 1)
        self.assertTrue(plans["burst_order_valid"], "plan candidates were dropped or reordered")
        self.assertTrue(plans["burst_gate_continues"], "remaining plan candidates were gated until next cycle")
        self.assertTrue(plans["full_queue_preserved"], "a full pending queue consumed a planned slot")
        self.assertTrue(plans["deferred_preserved"], "a deferred planned read advanced the cursor")
        self.assertTrue(plans["deferred_retried"], "a deferred planned slot was not retried")
        self.assertEqual(plans["owner_pending"], 0, "owner change retained old-session pending work")
        self.assertEqual(plans["owner_cancelled"], 1)
        self.assertEqual(plans["owner_id"], 18)
        self.assertEqual(plans["owner_baseline_remaining"], 60,
            "owner change must start a fresh 64-slot baseline")
        self.assertTrue(plans["attempted_write_preserved"], "owner reset discarded write-once protection")
        self.assertEqual(plans["unknown_owner_id"], 19)
        self.assertEqual(plans["unknown_owner_baseline_remaining"], 60,
            "an explicit owner change must restart baseline even if the old scan was empty")
        self.assertTrue(plans["unknown_owner_baseline_active"])
        self.assertEqual(plans["fallback_first_scan_submits"], 0,
            "a failed hint must fall back to the legacy round-robin slots")
        self.assertEqual(plans["fallback_final_submits"], 1,
            "a failed hint permanently disabled later scan plans")
        self.assertEqual(plans["fallback_hint_calls"], 2)

    def test_response_intervals_stall_expiry_and_reconnect(self) -> None:
        result = self.scenarios()
        latency = result["scan_latency"]
        self.assertEqual(latency["response_calls_after_first"], 1)
        self.assertEqual(latency["response_calls_before_due"], 1)
        self.assertEqual(latency["apply_calls_after_first"], 0)
        self.assertEqual(latency["apply_calls_before_due"], 0)
        self.assertEqual(latency["final_response_calls"], 2)
        self.assertEqual(latency["final_apply_calls"], 1)
        self.assertEqual(latency["final_status"], "ready")

        stall = result["stall"]
        self.assertEqual(stall["stalled_reads"], 4, "a long frame should run one scan batch")
        self.assertEqual(stall["short_frame_reads"], 0, "a short frame should not catch up scanning")

        pending = result["pending_fairness"]
        self.assertEqual(pending["first_pass_calls"], 32, "one slow token must not block the others")
        self.assertTrue(pending["gate_always_ran"], "pending work was deferred by the idle gate")
        self.assertTrue(pending["intervals_valid"], "a token was polled again before 200 ms")
        self.assertEqual(pending["applied"], 32)
        self.assertEqual(pending["pending"], 0)

        expiration = result["expiration"]
        self.assertEqual(expiration["before_ttl_cancels"], 0)
        self.assertEqual(expiration["after_ttl_cancels"], 1)
        self.assertEqual(expiration["pending"], 0)
        self.assertEqual(expiration["expired"], 1)

        reconnect = result["reconnect"]
        self.assertEqual(reconnect["pending_while_inactive"], 0)
        self.assertEqual(reconnect["baseline_remaining_while_inactive"], 64)
        self.assertEqual(reconnect["cancelled_on_disconnect"], 1)
        self.assertEqual(reconnect["baseline_remaining_on_recovery"], 60)

        for sample in result["failed_writes"]:
            with self.subTest(fps=sample["fps"], status=sample["status"]):
                self.assertEqual(sample["attempts"], 1)
                self.assertEqual(sample["cancelled"], 1)
                self.assertEqual(sample["remaining"], 0)
                self.assertEqual(sample["resubmitted"], 0)
                self.assertEqual(sample["new_submits"], 1)
                self.assertEqual(sample["final_applies"], 2)

    def test_reports_keep_five_second_minimum_and_force_terminal_state(self) -> None:
        result = self.scenarios()["report"]
        self.assertEqual(result["reports_before_error"], 2)
        self.assertEqual(result["output_count"], 3, "clock failure should publish a terminal report")
        self.assertEqual(result["output_times"][:2], [0, 5000])
        final = json.loads(result["final_output"])
        self.assertEqual(final["status"], "stopped")
        self.assertEqual(final["code"], "clock_error")
        self.assertEqual(final["counters"]["steps"], result["final_steps"])
        self.assertFalse(result["private_body_leaked"])

if __name__ == "__main__":
    unittest.main()
