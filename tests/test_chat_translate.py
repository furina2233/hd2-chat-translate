"""聊天翻译核心的 LuaJIT mock 回归。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT


CORE_SCENARIOS = r'''
local json_core = dofile([[SCAN_CORE_PATH]])
local core = dofile([[TRANSLATE_CORE_PATH]])

local function make_message(slot, body, event_slot, owner_id)
    return {
        widget_slot = slot,
        event_slot = event_slot or slot,
        owner_id = owner_id or 6,
        body = body,
        proof = {private = "PRIVATE_ADAPTER_PROOF"},
    }
end

local function new_env()
    local env = {
        now = 0,
        heartbeat_age = 0,
        messages = {},
        filtered_slots = {},
        deferred_slots = {},
        responses = {},
        submit_accept = true,
        submit_throw = false,
        response_throw = false,
        apply_throw = false,
        submit_calls = {},
        response_calls = {},
        apply_calls = {},
        cancel_calls = {},
        output_calls = {},
        read_calls = {},
        applied = 0,
        apply_status = "called_confirmed",
        apply_status_sequence = {},
        max_reads_one_step = 0,
        max_applies_one_step = 0,
        max_submits_one_step = 0,
    }
    local adapter = {
        now_ms = function() return env.now end,
        heartbeat = function()
            if env.heartbeat_age == false then return nil end
            return string.format("HD2CT1 %d\n", env.now - env.heartbeat_age)
        end,
        read_slot = function(slot)
            env.read_calls[#env.read_calls + 1] = slot
            if env.deferred_slots[slot] then return "deferred" end
            if env.filtered_slots[slot] then return "filtered_event" end
            local message = env.messages[slot]
            if message then return "ok", message end
            return "empty"
        end,
        submit = function(token, body)
            env.submit_calls[#env.submit_calls + 1] = {token = token, body = body}
            if env.submit_throw then error("PRIVATE_SUBMIT_SECRET") end
            return env.submit_accept
        end,
        response = function(token)
            env.response_calls[#env.response_calls + 1] = token
            if env.response_throw then error("PRIVATE_RESPONSE_SECRET") end
            return env.responses[token]
        end,
        apply = function(message, text)
            env.applied = env.applied + 1
            env.apply_calls[#env.apply_calls + 1] = {
                body = message.body,
                text = text,
                slot = message.widget_slot,
            }
            if env.apply_throw then error("PRIVATE_APPLY_SECRET") end
            if #env.apply_status_sequence > 0 then
                return table.remove(env.apply_status_sequence, 1)
            end
            return env.apply_status
        end,
        cancel = function(token)
            env.cancel_calls[#env.cancel_calls + 1] = token
            return true
        end,
        output = function(manifest)
            env.output_calls[#env.output_calls + 1] = json_core.encode_json(manifest)
        end,
    }
    env.state = core.new(adapter, {target_verified = true, session_id = "test_session",
        scan_interval_ms = 0, response_poll_ms = 0})
    return env
end

local function step(env, delta)
    env.now = env.now + (delta or 1)
    local reads_before = #env.read_calls
    local submits_before = #env.submit_calls
    local applies_before = #env.apply_calls
    local done = core.step(env.state)
    env.max_reads_one_step = math.max(env.max_reads_one_step, #env.read_calls - reads_before)
    env.max_submits_one_step = math.max(env.max_submits_one_step, #env.submit_calls - submits_before)
    env.max_applies_one_step = math.max(env.max_applies_one_step, #env.apply_calls - applies_before)
    return done
end

local function finish_baseline(env)
    for _ = 1, 16 do step(env) end
    assert(core.manifest(env.state).baseline_remaining == 0, "64-slot baseline did not finish")
end

local function counters(env)
    return core.manifest(env.state).counters
end

local function one_unverified_case()
    local calls = {heartbeat = 0, reads = 0}
    local adapter = {
        now_ms = function() return 0 end,
        heartbeat = function() calls.heartbeat = calls.heartbeat + 1; return "HD2CT1 0\n" end,
        read_slot = function() calls.reads = calls.reads + 1; return "empty" end,
        submit = function() return true end,
        response = function() return nil end,
        apply = function() return "called_confirmed" end,
        cancel = function() return true end,
    }
    local state = core.new(adapter, {target_verified = false, session_id = "test_session",
        scan_interval_ms = 0, response_poll_ms = 0})
    core.step(state)
    return {manifest = core.manifest(state), heartbeat = calls.heartbeat, reads = calls.reads}
end

local function one_baseline_and_chinese_case()
    local env = new_env()
    env.messages[0] = make_message(0, "已存在的旧消息", 4, 77)
    finish_baseline(env)
    local after_baseline = #env.submit_calls
    env.messages[0] = make_message(0, "你好，队友", 5, 77)
    step(env)
    local token = env.submit_calls[1] and env.submit_calls[1].token
    assert(token, "Chinese source text was not submitted for model detection")
    env.responses[token] = "OK\n你好，队友"
    step(env)
    for _ = 1, 17 do step(env) end
    local manifest = core.manifest(env.state)
    return {
        after_baseline = after_baseline,
        submits = #env.submit_calls,
        applies = #env.apply_calls,
        submitted_body = env.submit_calls[1] and env.submit_calls[1].body or "",
        cancels = #env.cancel_calls,
        unchanged = manifest.counters.translation_unchanged,
        duplicates = manifest.counters.duplicates,
        manifest_json = json_core.encode_json(manifest),
        output_json = table.concat(env.output_calls, "\n"),
    }
end

local function one_apply_and_stale_case()
    local env = new_env()
    finish_baseline(env)
    local source = "Move to extraction\n\"A1\""
    env.messages[0] = make_message(0, source, 2, 6)
    step(env)
    local first_token = env.submit_calls[1].token
    env.responses[first_token] = "OK\n前往撤离点"
    step(env)
    local applied_once = #env.apply_calls
    for _ = 1, 17 do step(env) end

    local changed = new_env()
    finish_baseline(changed)
    changed.messages[0] = make_message(0, "Original chat", 2, 6)
    step(changed)
    local changed_token = changed.submit_calls[1].token
    changed.responses[changed_token] = "OK\n原始聊天"
    changed.apply_status = "stale"
    step(changed)
    for _ = 1, 17 do step(changed) end
    return {
        apply_count = #env.apply_calls,
        apply_count_after_first = applied_once,
        submitted_count = #env.submit_calls,
        source_body = source,
        submitted_body = env.submit_calls[1] and env.submit_calls[1].body or "",
        translated_text = env.apply_calls[1] and env.apply_calls[1].text,
        stale_count = counters(changed).apply_stale,
        stale_cancel_count = #changed.cancel_calls,
        stale_body = changed.apply_calls[1] and changed.apply_calls[1].body,
    }
end

local function one_deferred_apply_retry_case()
    local env = new_env()
    finish_baseline(env)
    local source = "Hold position\n\"B2\""
    local expected = source .. core.DISPLAY_SEPARATOR .. "保持阵地"
    env.messages[0] = make_message(0, source, 3, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.responses[token] = "OK\n保持阵地"
    env.apply_status_sequence = {"deferred", "called_confirmed"}
    step(env)
    local after_defer = env.apply_calls[1] and env.apply_calls[1].text or ""
    step(env)
    return {
        apply_count = #env.apply_calls,
        first_text = after_defer,
        second_text = env.apply_calls[2] and env.apply_calls[2].text or "",
        expected_text = expected,
        response_count = #env.response_calls,
        submitted_count = #env.submit_calls,
        submitted_body = env.submit_calls[1] and env.submit_calls[1].body or "",
        source_body = source,
    }
end

local function one_filtered_event_case()
    local pending_env = new_env()
    finish_baseline(pending_env)
    pending_env.messages[0] = make_message(0, "Pending player message", 0, 6)
    step(pending_env)
    local token = pending_env.submit_calls[1].token
    pending_env.filtered_slots[0] = true
    pending_env.state.next_slot = 0
    step(pending_env)
    local pending_manifest = core.manifest(pending_env.state)

    local baseline_env = new_env()
    baseline_env.filtered_slots[7] = true
    finish_baseline(baseline_env)
    local baseline_manifest = core.manifest(baseline_env.state)
    return {
        pending_count = pending_manifest.pending_count,
        pending_submitted = #pending_env.submit_calls,
        pending_cancel_count = #pending_env.cancel_calls,
        pending_cancel_token = pending_env.cancel_calls[1] or "",
        expected_token = token,
        pending_filtered = pending_manifest.counters.slot_event_filtered,
        pending_invalid = pending_manifest.counters.invalid_messages,
        baseline_submitted = #baseline_env.submit_calls,
        baseline_filtered = baseline_manifest.counters.slot_event_filtered,
        baseline_invalid = baseline_manifest.counters.invalid_messages,
    }
end

local function one_capacity_ttl_case()
    local env = new_env()
    finish_baseline(env)
    for slot = 0, 63 do env.messages[slot] = make_message(slot, "msg-" .. slot, slot, 6) end
    local max_submits_one_step = 0
    local prior_submits = #env.submit_calls
    for _ = 1, 32 do
        step(env)
        max_submits_one_step = math.max(max_submits_one_step, #env.submit_calls - prior_submits)
        prior_submits = #env.submit_calls
    end
    local full_manifest = core.manifest(env.state)

    local expired = new_env()
    finish_baseline(expired)
    expired.messages[0] = make_message(0, "Wait for orders", 0, 6)
    step(expired)
    local token = expired.submit_calls[1].token
    expired.now = expired.now + core.PENDING_TTL_MS
    step(expired)
    return {
        pending = full_manifest.pending_count,
        submitted = full_manifest.counters.submitted,
        queue_full = full_manifest.counters.queue_full,
        max_submits_one_step = max_submits_one_step,
        max_reads_one_step = env.max_reads_one_step,
        max_applies_one_step = env.max_applies_one_step,
        expired_pending = core.manifest(expired.state).pending_count,
        expired_count = counters(expired).expired,
        expired_cancel_count = #expired.cancel_calls,
        expired_token = expired.cancel_calls[1] or "",
        expected_token = token,
    }
end

local function one_deferred_and_submit_failure_case()
    local env = new_env()
    finish_baseline(env)
    env.messages[0] = make_message(0, "Hold position", 3, 6)
    step(env)
    local token = env.submit_calls[1].token
    for _ = 1, 15 do step(env) end
    assert(env.state.next_slot == 0)
    env.deferred_slots[0] = true
    step(env)
    local after_defer = core.manifest(env.state)
    local retained = env.state.pending_by_slot[0] ~= nil and env.state.seen[0] ~= nil
    local next_slot_after_defer = env.state.next_slot
    local cancel_after_defer = #env.cancel_calls
    env.deferred_slots[0] = nil
    env.responses[token] = "OK\n保持阵地"
    step(env)

    local reject = new_env()
    finish_baseline(reject)
    reject.submit_accept = false
    for slot = 0, 3 do reject.messages[slot] = make_message(slot, "retry-" .. slot, slot, 6) end
    local before = #reject.submit_calls
    step(reject)
    local attempts_first = #reject.submit_calls - before
    before = #reject.submit_calls
    step(reject)
    local attempts_second = #reject.submit_calls - before
    local rejected_manifest = core.manifest(reject.state)

    local dedupe = new_env()
    finish_baseline(dedupe)
    dedupe.submit_accept = false
    dedupe.messages[0] = make_message(0, "one failed source", 0, 6)
    step(dedupe)
    for _ = 1, 16 do step(dedupe) end
    return {
        pending_after_defer = after_defer.pending_count,
        defer_counter = after_defer.counters.slot_deferred,
        retained_after_defer = retained,
        cursor_after_defer = next_slot_after_defer,
        cancel_after_defer = cancel_after_defer,
        apply_after_resume = #env.apply_calls,
        apply_text_after_resume = env.apply_calls[1] and env.apply_calls[1].text,
        attempts_first = attempts_first,
        attempts_second = attempts_second,
        failed_submit_count = counters(reject).submit_failures,
        failed_submitted_count = rejected_manifest.counters.submitted,
        failed_error_displays = rejected_manifest.counters.error_displays_ready,
        failed_apply_count = #reject.apply_calls,
        failed_apply_text = reject.apply_calls[1] and reject.apply_calls[1].text or "",
        failed_cancel_count = #reject.cancel_calls,
        failed_response_count = #reject.response_calls,
        failed_adapter_errors = rejected_manifest.counters.adapter_errors,
        dedupe_submit_count = #dedupe.submit_calls,
        dedupe_apply_count = #dedupe.apply_calls,
        dedupe_duplicate_count = counters(dedupe).duplicates,
    }
end

local function one_inactive_heartbeat_case()
    local env = new_env()
    finish_baseline(env)
    env.messages[0] = make_message(0, "Pending before bridge stops", 1, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.heartbeat_age = false
    env.now = env.now + 200
    local reads_before_drop = #env.read_calls
    step(env)
    return {
        pending = core.manifest(env.state).pending_count,
        heartbeat_active = core.manifest(env.state).heartbeat_active,
        cancel_count = #env.cancel_calls,
        cancelled = env.cancel_calls[1] or "",
        expected = token,
        reads_after_drop = #env.read_calls - reads_before_drop,
    }
end

local function run_response_case(text, source)
    local env = new_env()
    finish_baseline(env)
    source = source or "source"
    env.messages[0] = make_message(0, source, 0, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.responses[token] = "OK\n" .. text
    step(env)
    local manifest = core.manifest(env.state)
    return {
        applied_bytes = env.apply_calls[1] and #env.apply_calls[1].text or 0,
        applied_text = env.apply_calls[1] and env.apply_calls[1].text or "",
        applied = #env.apply_calls,
        submitted_body = env.submit_calls[1] and env.submit_calls[1].body or "",
        invalid = manifest.counters.response_invalid,
        unchanged = manifest.counters.translation_unchanged,
        error_displays = manifest.counters.error_displays_ready,
        translations_ready = manifest.counters.translations_ready,
    }
end

local function run_error_response_case(code, source_override)
    local env = new_env()
    finish_baseline(env)
    local source = source_override or "Original player message"
    env.messages[0] = make_message(0, source, 0, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.responses[token] = "ERR\n" .. code
    step(env)
    local manifest = core.manifest(env.state)
    return {
        source = source,
        text = env.apply_calls[1] and env.apply_calls[1].text or "",
        report = table.concat(env.output_calls, "\n"),
        translation_errors = manifest.counters.translation_errors,
        response_invalid = manifest.counters.response_invalid,
        response_errors = manifest.counters.response_errors,
        translations_ready = manifest.counters.translations_ready,
        translation_unchanged = manifest.counters.translation_unchanged,
        error_displays_ready = manifest.counters.error_displays_ready,
        apply_count = #env.apply_calls,
    }
end

local function one_error_message_case()
    return {
        http400 = run_error_response_case("HTTP_400"),
        http422 = run_error_response_case("HTTP_422"),
        http401 = run_error_response_case("HTTP_401"),
        http403 = run_error_response_case("HTTP_403"),
        http404 = run_error_response_case("HTTP_404"),
        http408 = run_error_response_case("HTTP_408"),
        http429 = run_error_response_case("HTTP_429"),
        http503 = run_error_response_case("HTTP_503"),
        http504 = run_error_response_case("HTTP_504"),
        http599 = run_error_response_case("HTTP_599"),
        http600 = run_error_response_case("HTTP_600"),
        network = run_error_response_case("NETWORK"),
        timeout = run_error_response_case("TIMEOUT"),
        expired = run_error_response_case("EXPIRED"),
        bad_response = run_error_response_case("BAD_RESPONSE"),
        response_too_large = run_error_response_case("RESPONSE_TOO_LARGE"),
        rate_limited = run_error_response_case("RATE_LIMITED"),
        backoff = run_error_response_case("BACKOFF"),
        invalid_url = run_error_response_case("INVALID_URL"),
        invalid_config = run_error_response_case("INVALID_CONFIG"),
        internal = run_error_response_case("INTERNAL"),
        response_exception = run_error_response_case("RESPONSE_EXCEPTION"),
        cancelled = run_error_response_case("CANCELLED"),
        submit_failed = run_error_response_case("SUBMIT_FAILED"),
        submit_exception = run_error_response_case("SUBMIT_EXCEPTION"),
        same_as_source = run_error_response_case("NETWORK", "网络连接失败"),
        unknown_private = run_error_response_case("PRIVATE_SECRET_API_KEY"),
    }
end

local function one_exception_case()
    local submit = new_env()
    finish_baseline(submit)
    submit.submit_throw = true
    submit.messages[0] = make_message(0, "submit source", 0, 6)
    step(submit)
    step(submit)

    local response = new_env()
    finish_baseline(response)
    response.messages[0] = make_message(0, "response source", 0, 6)
    step(response)
    response.response_throw = true
    step(response)

    local apply = new_env()
    finish_baseline(apply)
    apply.messages[0] = make_message(0, "apply source", 0, 6)
    step(apply)
    local token = apply.submit_calls[1].token
    apply.responses[token] = "OK\n应用目标译文"
    apply.apply_throw = true
    step(apply)
    return {
        submit_text = submit.apply_calls[1] and submit.apply_calls[1].text or "",
        submit_failures = counters(submit).submit_failures,
        submit_count = #submit.submit_calls,
        submit_applied = #submit.apply_calls,
        submit_cancels = #submit.cancel_calls,
        submit_responses = #submit.response_calls,
        submit_adapter_errors = counters(submit).adapter_errors,
        submit_error_displays = counters(submit).error_displays_ready,
        submit_report = table.concat(submit.output_calls, "\n"),
        response_text = response.apply_calls[1] and response.apply_calls[1].text or "",
        response_errors = counters(response).response_errors,
        response_error_displays = counters(response).error_displays_ready,
        response_applied = #response.apply_calls,
        response_report = table.concat(response.output_calls, "\n"),
        apply_errors = counters(apply).apply_errors,
        apply_report = table.concat(apply.output_calls, "\n"),
    }
end

local function one_response_bounds_case()
    local valid_max = run_response_case(string.rep("T", 16384))
    local maximum_display = run_response_case(string.rep("T", 16384), string.rep("S", 1023))
    local too_long = run_response_case(string.rep("T", 16385))
    local nul = run_response_case("first" .. string.char(0) .. "last")
    local malformed = run_response_case(string.char(0xf0, 0x80, 0x80, 0x80))
    return {valid_max = valid_max, maximum_display = maximum_display,
        too_long = too_long, nul = nul, malformed = malformed}
end

local function one_apply_heartbeat_gate_case()
    local env = new_env()
    finish_baseline(env)
    env.messages[0] = make_message(0, "Translate this once", 2, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.responses[token] = "OK\n只翻译一次"
    -- step入口使用最近心跳缓存；apply前的强制心跳应发现文件已删除。
    env.heartbeat_age = false
    step(env)
    return {
        applies = #env.apply_calls,
        pending = core.manifest(env.state).pending_count,
        heartbeat_active = core.manifest(env.state).heartbeat_active,
        cancel_count = #env.cancel_calls,
        cancelled = env.cancel_calls[1] or "",
        expected = token,
    }
end

local function one_stale_heartbeat_case()
    local env = new_env()
    finish_baseline(env)
    env.messages[0] = make_message(0, "Old heartbeat", 3, 6)
    step(env)
    env.heartbeat_age = 3001
    env.now = env.now + 3000
    step(env)
    return {
        pending = core.manifest(env.state).pending_count,
        active = core.manifest(env.state).heartbeat_active,
        stale_count = counters(env).heartbeat_stale,
        cancel_count = #env.cancel_calls,
    }
end

local function run_heartbeat_clock_case(mode, stamp)
    local now_calls, read_calls = 0, 0
    local output = {}
    local adapter = {
        now_ms = function()
            now_calls = now_calls + 1
            if now_calls == 1 then return 1000 end
            if mode == "reverse" then return 999 end
            if mode == "throw" then error("PRIVATE_CLOCK_ERROR") end
            return 1001
        end,
        heartbeat = function() return string.format("HD2CT1 %d\n", stamp) end,
        read_slot = function()
            read_calls = read_calls + 1
            if mode == "advance" and read_calls == 65 then
                return "ok", {widget_slot = 0, event_slot = 4, owner_id = 6,
                    body = "time-base source", proof = {private = true}}
            end
            return "empty"
        end,
        submit = function() return true end,
        response = function() return nil end,
        apply = function() return "called_confirmed" end,
        cancel = function() return true end,
        output = function(manifest) output[#output + 1] = json_core.encode_json(manifest) end,
    }
    local state = core.new(adapter, {target_verified = true, session_id = "clock_session",
        scan_interval_ms = 0, response_poll_ms = 0})
    core.step(state)
    local first_manifest = core.manifest(state)
    local first_step_reads = read_calls
    if mode == "advance" then
        for _ = 1, 15 do core.step(state) end
        core.step(state)
    end
    return {
        manifest = first_manifest,
        read_calls = read_calls,
        first_step_reads = first_step_reads,
        last_now_ms = state.last_now_ms,
        pending_created_ms = state.pending[1] and state.pending[1].created_ms or -1,
        output_json = table.concat(output, "\n"),
    }
end

local function one_heartbeat_clock_race_case()
    return {
        advanced = run_heartbeat_clock_case("advance", 1001),
        future = run_heartbeat_clock_case("future", 1002),
        reversed = run_heartbeat_clock_case("reverse", 1001),
        errored = run_heartbeat_clock_case("throw", 1001),
    }
end

RESULT = json_core.encode_json({
    display_contract = {
        separator = core.DISPLAY_SEPARATOR,
        max_display_bytes = core.MAX_DISPLAY_BYTES,
    },
    validation = {
        ascii = core.valid_text("Move now", 1023),
        chinese = core.valid_text("撤离点集合", 1023),
        boundary = core.valid_text(string.rep("A", 1023), 1023),
        over_boundary = core.valid_text(string.rep("A", 1024), 1023),
        embedded_nul = core.valid_text("first" .. string.char(0) .. "last", 1023),
        overlong = core.valid_text(string.char(0xc0, 0xaf), 1023),
        surrogate = core.valid_text(string.char(0xed, 0xa0, 0x80), 1023),
        truncated = core.valid_text(string.char(0xe2, 0x82), 1023),
        oversized_translation = core.valid_text(string.rep("A", 16385), 16384),
    },
    unverified = one_unverified_case(),
    baseline_chinese = one_baseline_and_chinese_case(),
    apply_stale = one_apply_and_stale_case(),
    deferred_apply_retry = one_deferred_apply_retry_case(),
    filtered_event = one_filtered_event_case(),
    capacity_ttl = one_capacity_ttl_case(),
    deferred_submit_failure = one_deferred_and_submit_failure_case(),
    heartbeat_stop = one_inactive_heartbeat_case(),
    heartbeat_apply_gate = one_apply_heartbeat_gate_case(),
    heartbeat_stale = one_stale_heartbeat_case(),
    heartbeat_clock_race = one_heartbeat_clock_race_case(),
    response_bounds = one_response_bounds_case(),
    error_messages = one_error_message_case(),
    exceptions = one_exception_case(),
})
'''


class ChatTranslateCoreTests(unittest.TestCase):
    """在本机LuaJIT进程中验证无游戏DLL依赖的纯状态机。"""

    @classmethod
    def setUpClass(cls) -> None:
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行桥接核心mock")
        cls.lua = LuaJIT(LUA_DLL)

    def _scenarios(self) -> dict:
        script = CORE_SCENARIOS.replace(
            "SCAN_CORE_PATH", (ROOT / "game" / "chat_probe_core.lua").as_posix()
        ).replace(
            "TRANSLATE_CORE_PATH", (ROOT / "game" / "chat_translate_core.lua").as_posix()
        )
        return json.loads(self.lua.run(script))

    def test_utf8_and_size_validation_rejects_ambiguous_text(self) -> None:
        scenarios = self._scenarios()
        cases = scenarios["validation"]
        self.assertTrue(cases["ascii"])
        self.assertTrue(cases["chinese"])
        self.assertTrue(cases["boundary"])
        for invalid in ("over_boundary", "embedded_nul", "overlong", "surrogate", "truncated", "oversized_translation"):
            with self.subTest(invalid=invalid):
                self.assertFalse(cases[invalid])
        self.assertEqual(scenarios["display_contract"]["separator"], "\n译文：")
        self.assertEqual(scenarios["display_contract"]["max_display_bytes"], 17417)

    def test_build_gate_baselines_old_chat_and_preserves_unchanged_chinese(self) -> None:
        result = self._scenarios()
        gated = result["unverified"]
        self.assertEqual(gated["manifest"]["status"], "target_unverified")
        self.assertEqual(gated["heartbeat"], 0)
        self.assertEqual(gated["reads"], 0)

        baseline = result["baseline_chinese"]
        self.assertEqual(baseline["after_baseline"], 0, "preexisting chat was submitted")
        self.assertEqual(baseline["submits"], 1, "Chinese source was not sent through model detection")
        self.assertEqual(baseline["submitted_body"], "你好，队友", "model input should remain the unformatted source")
        self.assertEqual(baseline["applies"], 0, "unchanged Chinese text reached native apply")
        self.assertEqual(baseline["unchanged"], 1)
        self.assertEqual(baseline["cancels"], 1)
        self.assertGreaterEqual(baseline["duplicates"], 1)
        for private in ("你好，队友", "PRIVATE_ADAPTER_PROOF"):
            self.assertNotIn(private, baseline["manifest_json"])
            self.assertNotIn(private, baseline["output_json"])

    def test_translated_result_applies_once_and_stale_apply_is_terminal(self) -> None:
        result = self._scenarios()["apply_stale"]
        self.assertEqual(result["apply_count_after_first"], 1)
        self.assertEqual(result["apply_count"], 1, "stable slot was applied more than once")
        self.assertEqual(result["submitted_count"], 1, "same body was submitted again after apply")
        expected_source = 'Move to extraction\n"A1"'
        self.assertEqual(result["source_body"], expected_source)
        self.assertEqual(result["submitted_body"], expected_source, "model input should preserve raw newlines and quotes")
        self.assertEqual(result["translated_text"], expected_source + "\n译文：前往撤离点")
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["stale_cancel_count"], 1)
        self.assertEqual(result["stale_body"], "Original chat")

    def test_deferred_apply_reuses_the_single_preformatted_display_text(self) -> None:
        result = self._scenarios()["deferred_apply_retry"]
        self.assertEqual(result["apply_count"], 2)
        self.assertEqual(result["first_text"], result["expected_text"])
        self.assertEqual(result["second_text"], result["expected_text"])
        self.assertEqual(result["response_count"], 1, "deferred apply should reuse the parsed response")
        self.assertEqual(result["submitted_count"], 1, "same identity should not be resubmitted")
        self.assertEqual(result["submitted_body"], result["source_body"], "submit should receive the unformatted source")

    def test_filtered_event_is_counted_without_submit_and_cancels_owned_pending(self) -> None:
        result = self._scenarios()["filtered_event"]
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["pending_submitted"], 1)
        self.assertEqual(result["pending_cancel_count"], 1)
        self.assertEqual(result["pending_cancel_token"], result["expected_token"])
        self.assertEqual(result["pending_filtered"], 1)
        self.assertEqual(result["pending_invalid"], 0)
        self.assertEqual(result["baseline_submitted"], 0)
        self.assertEqual(result["baseline_filtered"], 1)
        self.assertEqual(result["baseline_invalid"], 0)

    def test_pending_queue_cap_and_expiry_cancel_the_owned_request(self) -> None:
        result = self._scenarios()["capacity_ttl"]
        self.assertEqual(result["pending"], 32)
        self.assertEqual(result["submitted"], 32)
        self.assertEqual(result["max_submits_one_step"], 1)
        self.assertLessEqual(result["max_reads_one_step"], 4)
        self.assertLessEqual(result["max_applies_one_step"], 1)
        self.assertGreater(result["queue_full"], 0)
        self.assertEqual(result["expired_pending"], 0)
        self.assertEqual(result["expired_count"], 1)
        self.assertEqual(result["expired_cancel_count"], 1)
        self.assertEqual(result["expired_token"], result["expected_token"])

    def test_deferred_reads_preserve_pending_and_retry_while_submit_failures_are_throttled(self) -> None:
        result = self._scenarios()["deferred_submit_failure"]
        self.assertEqual(result["pending_after_defer"], 1)
        self.assertEqual(result["defer_counter"], 1)
        self.assertTrue(result["retained_after_defer"])
        self.assertEqual(result["cursor_after_defer"], 0)
        self.assertEqual(result["cancel_after_defer"], 0)
        self.assertEqual(result["apply_after_resume"], 1)
        self.assertEqual(result["apply_text_after_resume"], "Hold position\n译文：保持阵地")
        self.assertEqual(result["attempts_first"], 1)
        self.assertEqual(result["attempts_second"], 0)
        self.assertEqual(result["failed_submit_count"], 1)
        self.assertEqual(result["failed_submitted_count"], 0)
        self.assertEqual(result["failed_error_displays"], 1)
        self.assertEqual(result["failed_apply_count"], 1)
        self.assertEqual(result["failed_apply_text"], "retry-0\n译文：翻译请求未能提交")
        self.assertEqual(result["failed_cancel_count"], 0, "a rejected submit does not own a provider token")
        self.assertEqual(result["failed_response_count"], 0, "a local submit error must not poll the provider")
        self.assertEqual(result["failed_adapter_errors"], 0, "a normal submit rejection is not an exception")
        self.assertEqual(result["dedupe_submit_count"], 1, "same identity retried a failed submit")
        self.assertEqual(result["dedupe_apply_count"], 1)
        self.assertGreaterEqual(result["dedupe_duplicate_count"], 1)

    def test_lost_heartbeat_clears_only_the_pending_bridge_request(self) -> None:
        result = self._scenarios()["heartbeat_stop"]
        self.assertEqual(result["pending"], 0)
        self.assertFalse(result["heartbeat_active"])
        self.assertEqual(result["cancel_count"], 1)
        self.assertEqual(result["cancelled"], result["expected"])
        self.assertEqual(result["reads_after_drop"], 0)

        apply_gate = self._scenarios()["heartbeat_apply_gate"]
        self.assertEqual(apply_gate["applies"], 0, "native apply passed after its forced heartbeat check failed")
        self.assertEqual(apply_gate["pending"], 0)
        self.assertFalse(apply_gate["heartbeat_active"])
        self.assertEqual(apply_gate["cancel_count"], 1)
        self.assertEqual(apply_gate["cancelled"], apply_gate["expected"])

        stale = self._scenarios()["heartbeat_stale"]
        self.assertEqual(stale["pending"], 0)
        self.assertFalse(stale["active"])
        self.assertEqual(stale["stale_count"], 1)
        self.assertEqual(stale["cancel_count"], 1)

    def test_heartbeat_freshness_uses_clock_sampled_after_file_read(self) -> None:
        result = self._scenarios()["heartbeat_clock_race"]
        advanced = result["advanced"]
        self.assertTrue(advanced["manifest"]["heartbeat_active"])
        self.assertEqual(advanced["manifest"]["status"], "baseline")
        self.assertEqual(advanced["manifest"]["baseline_remaining"], 60)
        self.assertEqual(advanced["first_step_reads"], 4)
        self.assertEqual(advanced["last_now_ms"], 1001)
        self.assertEqual(advanced["pending_created_ms"], 1001,
                         "request TTL should start from the post-heartbeat monotonic sample")

        future = result["future"]
        self.assertFalse(future["manifest"]["heartbeat_active"])
        self.assertEqual(future["manifest"]["status"], "inactive")
        self.assertEqual(future["manifest"]["counters"]["heartbeat_stale"], 1)
        self.assertEqual(future["read_calls"], 0)

        for key, code in (("reversed", "clock_reversed"), ("errored", "clock_error")):
            with self.subTest(clock_failure=key):
                failed = result[key]
                self.assertTrue(failed["manifest"]["done"])
                self.assertEqual(failed["manifest"]["status"], "stopped")
                self.assertEqual(failed["manifest"]["code"], code)
                self.assertNotIn("PRIVATE_CLOCK_ERROR", failed["output_json"])

    def test_response_payload_accepts_exact_utf8_limit_and_rejects_invalid_or_oversized(self) -> None:
        cases = self._scenarios()["response_bounds"]
        self.assertEqual(cases["valid_max"]["applied"], 1)
        self.assertEqual(cases["valid_max"]["applied_bytes"], 16400)
        maximum = cases["maximum_display"]
        expected_source = "S" * 1023
        expected_translation = "T" * 16384
        self.assertEqual(maximum["applied"], 1)
        self.assertEqual(maximum["applied_bytes"], 17417)
        self.assertEqual(maximum["applied_text"], expected_source + "\n译文：" + expected_translation)
        self.assertEqual(maximum["submitted_body"], expected_source)
        self.assertEqual(cases["valid_max"]["error_displays"], 0)
        self.assertEqual(cases["valid_max"]["translations_ready"], 1)
        for label in ("too_long", "nul", "malformed"):
            with self.subTest(response=label):
                self.assertEqual(cases[label]["applied"], 1)
                self.assertEqual(cases[label]["invalid"], 1)
                self.assertEqual(cases[label]["error_displays"], 1)
                self.assertEqual(cases[label]["translations_ready"], 0)
                self.assertEqual(cases[label]["applied_text"], "source\n译文：返回内容无效")

    def test_error_codes_produce_only_short_whitelisted_chinese_messages(self) -> None:
        cases = self._scenarios()["error_messages"]
        expected_messages = {
            "http400": "请求参数有误",
            "http422": "请求参数有误",
            "http401": "API 密钥无效",
            "http403": "无权使用此接口",
            "http404": "接口或模型不存在",
            "http408": "请求超时",
            "http429": "请求太频繁，请稍后再试",
            "http503": "服务暂时不可用",
            "http504": "请求超时",
            "http599": "服务暂时不可用",
            "http600": "翻译失败，请稍后重试",
            "network": "网络连接失败",
            "timeout": "请求超时",
            "expired": "请求超时",
            "bad_response": "返回内容无效",
            "response_too_large": "返回内容过长",
            "rate_limited": "请求太频繁，请稍后再试",
            "backoff": "请稍后重试",
            "invalid_url": "接口地址无效",
            "invalid_config": "模型配置无效",
            "internal": "翻译服务异常",
            "response_exception": "翻译服务异常",
            "cancelled": "请求已取消",
            "submit_failed": "翻译请求未能提交",
            "submit_exception": "翻译服务异常",
        }
        for code, phrase in expected_messages.items():
            with self.subTest(code=code):
                result = cases[code]
                self.assertEqual(result["apply_count"], 1)
                self.assertEqual(result["text"], result["source"] + "\n译文：" + phrase)
                self.assertEqual(result["translation_errors"], 1)
                self.assertEqual(result["response_invalid"], 0)
                self.assertEqual(result["translations_ready"], 0)
                self.assertEqual(result["error_displays_ready"], 1)
        unknown = cases["unknown_private"]
        self.assertEqual(unknown["text"], unknown["source"] + "\n译文：翻译失败，请稍后重试")
        self.assertNotIn("PRIVATE_SECRET_API_KEY", unknown["text"] + unknown["report"])
        same_as_source = cases["same_as_source"]
        self.assertEqual(same_as_source["translation_unchanged"], 0)
        self.assertEqual(same_as_source["text"], "网络连接失败\n译文：网络连接失败")

    def test_submit_response_and_apply_exceptions_are_caught_without_leaking_details(self) -> None:
        result = self._scenarios()["exceptions"]
        self.assertEqual(result["submit_text"], "submit source\n译文：翻译服务异常")
        self.assertEqual(result["submit_failures"], 1)
        self.assertEqual(result["submit_count"], 1)
        self.assertEqual(result["submit_applied"], 1)
        self.assertEqual(result["submit_cancels"], 0)
        self.assertEqual(result["submit_responses"], 0)
        self.assertEqual(result["submit_adapter_errors"], 1)
        self.assertEqual(result["submit_error_displays"], 1)
        self.assertEqual(result["response_text"], "response source\n译文：翻译服务异常")
        self.assertEqual(result["response_errors"], 1)
        self.assertEqual(result["response_applied"], 1)
        self.assertEqual(result["response_error_displays"], 1)
        self.assertEqual(result["apply_errors"], 1)
        for report in (result["submit_report"], result["response_report"], result["apply_report"]):
            for secret in ("PRIVATE_SUBMIT_SECRET", "PRIVATE_RESPONSE_SECRET", "PRIVATE_APPLY_SECRET"):
                self.assertNotIn(secret, report)


if __name__ == "__main__":
    unittest.main()
