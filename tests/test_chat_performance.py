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

local latency = new_env()
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
for frame = 1, 32 do step_at(pending, 3000 + frame * 50) end
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
        step_at(env, 11200)
        local token = env.submit_calls[1] and env.submit_calls[1].token
        if token then env.responses[token] = "OK\nnew translation" end
        step_at(env, 11201)
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
        self.assertEqual(result["defaults"]["scan_interval_ms"], 200)
        self.assertEqual(result["defaults"]["response_poll_ms"], 200)
        self.assertEqual(result["defaults"]["invalid_scan_interval_ms"], 200)
        self.assertEqual(result["defaults"]["invalid_response_poll_ms"], 200)
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

    def test_failed_writes_are_terminal_at_all_frame_rates(self) -> None:
        for sample in self.scenarios()["failed_writes"]:
            with self.subTest(fps=sample["fps"], status=sample["status"]):
                self.assertEqual(sample["attempts"], 1)
                self.assertEqual(sample["cancelled"], 1)
                self.assertEqual(sample["remaining"], 0)
                self.assertEqual(sample["resubmitted"], 0)
                self.assertEqual(sample["new_submits"], 1)
                self.assertEqual(sample["final_applies"], 2)


if __name__ == "__main__":
    unittest.main()
