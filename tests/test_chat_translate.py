"""完整聊天翻译桥接纯核心与离线端到端测试。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from test_chat_probe import LUA_DLL, LuaJIT, ROOT


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
        deferred_slots = {},
        responses = {},
        submit_accept = true,
        submit_calls = {},
        response_calls = {},
        apply_calls = {},
        cancel_calls = {},
        output_calls = {},
        read_calls = {},
        applied = 0,
        apply_status = "called_confirmed",
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
            local message = env.messages[slot]
            if message then return "ok", message end
            return "empty"
        end,
        submit = function(token, body)
            env.submit_calls[#env.submit_calls + 1] = {token = token, body = body}
            return env.submit_accept
        end,
        response = function(token)
            env.response_calls[#env.response_calls + 1] = token
            return env.responses[token]
        end,
        apply = function(message, text)
            env.applied = env.applied + 1
            env.apply_calls[#env.apply_calls + 1] = {
                body = message.body,
                text = text,
                slot = message.widget_slot,
            }
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
    env.state = core.new(adapter, {target_verified = true, session_id = "test_session"})
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
    local state = core.new(adapter, {target_verified = false, session_id = "test_session"})
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
    env.messages[0] = make_message(0, "Move to extraction", 2, 6)
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
        translated_text = env.apply_calls[1] and env.apply_calls[1].text,
        stale_count = counters(changed).apply_stale,
        stale_cancel_count = #changed.cancel_calls,
        stale_body = changed.apply_calls[1] and changed.apply_calls[1].body,
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

local function run_response_case(text)
    local env = new_env()
    finish_baseline(env)
    env.messages[0] = make_message(0, "source", 0, 6)
    step(env)
    local token = env.submit_calls[1].token
    env.responses[token] = "OK\n" .. text
    step(env)
    local manifest = core.manifest(env.state)
    return {
        applied_bytes = env.apply_calls[1] and #env.apply_calls[1].text or 0,
        applied = #env.apply_calls,
        invalid = manifest.counters.response_invalid,
        unchanged = manifest.counters.translation_unchanged,
    }
end

local function one_response_bounds_case()
    local valid_max = run_response_case(string.rep("T", 16384))
    local too_long = run_response_case(string.rep("T", 16385))
    local nul = run_response_case("first" .. string.char(0) .. "last")
    local malformed = run_response_case(string.char(0xf0, 0x80, 0x80, 0x80))
    return {valid_max = valid_max, too_long = too_long, nul = nul, malformed = malformed}
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
    local state = core.new(adapter, {target_verified = true, session_id = "clock_session"})
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
    capacity_ttl = one_capacity_ttl_case(),
    deferred_submit_failure = one_deferred_and_submit_failure_case(),
    heartbeat_stop = one_inactive_heartbeat_case(),
    heartbeat_apply_gate = one_apply_heartbeat_gate_case(),
    heartbeat_stale = one_stale_heartbeat_case(),
    heartbeat_clock_race = one_heartbeat_clock_race_case(),
    response_bounds = one_response_bounds_case(),
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
        cases = self._scenarios()["validation"]
        self.assertTrue(cases["ascii"])
        self.assertTrue(cases["chinese"])
        self.assertTrue(cases["boundary"])
        for invalid in ("over_boundary", "embedded_nul", "overlong", "surrogate", "truncated", "oversized_translation"):
            with self.subTest(invalid=invalid):
                self.assertFalse(cases[invalid])

    def test_build_gate_baselines_old_chat_and_preserves_unchanged_chinese(self) -> None:
        result = self._scenarios()
        gated = result["unverified"]
        self.assertEqual(gated["manifest"]["status"], "target_unverified")
        self.assertEqual(gated["heartbeat"], 0)
        self.assertEqual(gated["reads"], 0)

        baseline = result["baseline_chinese"]
        self.assertEqual(baseline["after_baseline"], 0, "preexisting chat was submitted")
        self.assertEqual(baseline["submits"], 1, "Chinese source was not sent through model detection")
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
        self.assertEqual(result["translated_text"], "前往撤离点")
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["stale_cancel_count"], 1)
        self.assertEqual(result["stale_body"], "Original chat")

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
        self.assertEqual(result["apply_text_after_resume"], "保持阵地")
        self.assertEqual(result["attempts_first"], 1)
        self.assertEqual(result["attempts_second"], 0)
        self.assertEqual(result["failed_submit_count"], 1)

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
        self.assertEqual(cases["valid_max"]["applied_bytes"], 16384)
        for label in ("too_long", "nul", "malformed"):
            with self.subTest(response=label):
                self.assertEqual(cases[label]["applied"], 0)
                self.assertEqual(cases[label]["invalid"], 1)


if __name__ == "__main__":
    unittest.main()
