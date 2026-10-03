"""聊天翻译核心的 LuaJIT mock 回归。"""

from __future__ import annotations

import json
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
        responses = {},
        submit_throw = false,
        response_throw = false,
        apply_throw = false,
        submit_calls = {},
        response_calls = {},
        apply_calls = {},
        cancel_calls = {},
        output_calls = {},
        read_calls = {},
        apply_status = "called_confirmed",
    }
    local adapter = {
        now_ms = function() return env.now end,
        heartbeat = function()
            if env.heartbeat_age == false then return nil end
            return string.format("HD2CT1 %d\n", env.now - env.heartbeat_age)
        end,
        read_slot = function(slot)
            env.read_calls[#env.read_calls + 1] = slot
            if env.filtered_slots[slot] then return "filtered_event" end
            local message = env.messages[slot]
            if message then return "ok", message end
            return "empty"
        end,
        submit = function(token, body)
            env.submit_calls[#env.submit_calls + 1] = {token = token, body = body}
            if env.submit_throw then error("PRIVATE_SUBMIT_SECRET") end
            return true
        end,
        response = function(token)
            env.response_calls[#env.response_calls + 1] = token
            if env.response_throw then error("PRIVATE_RESPONSE_SECRET") end
            return env.responses[token]
        end,
        apply = function(message, text)
            env.apply_calls[#env.apply_calls + 1] = {
                body = message.body,
                text = text,
                slot = message.widget_slot,
            }
            if env.apply_throw then error("PRIVATE_APPLY_SECRET") end
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
    return core.step(env.state)
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


local function one_error_message_case()
    return {
        http401 = run_error_response_case("HTTP_401"),
        network = run_error_response_case("NETWORK"),
        bad_response = run_error_response_case("BAD_RESPONSE"),
        same_as_source = run_error_response_case("NETWORK", "网络连接失败"),
        unknown_private = run_error_response_case("PRIVATE_SECRET_API_KEY"),
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
    filtered_event = one_filtered_event_case(),
    response_bounds = one_response_bounds_case(),
    error_messages = one_error_message_case(),
    exceptions = one_exception_case(),
})
'''


class ChatTranslateCoreTests(unittest.TestCase):
    """在本机 LuaJIT 进程中验证聊天翻译状态机。"""

    @classmethod
    def setUpClass(cls) -> None:
        if LUA_DLL is None:
            raise RuntimeError("本机未提供 LuaJIT lua51.dll，不能运行桥接核心mock")
        cls.lua = LuaJIT(LUA_DLL)

    def _scenarios(self) -> dict:
        script = CORE_SCENARIOS.replace(
            "SCAN_CORE_PATH", (ROOT / "game" / "chat_probe_core.lua").as_posix()
        ).replace(
            "TRANSLATE_CORE_PATH", (ROOT / "game" / "chat_translate_core.lua").as_posix()
        )
        return json.loads(self.lua.run(script))

    def test_utf8_bytes_and_bilingual_separator_contract(self) -> None:
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

        responses = scenarios["response_bounds"]
        self.assertEqual(responses["valid_max"]["applied"], 1)
        self.assertEqual(responses["valid_max"]["applied_bytes"], 16400)
        self.assertEqual(responses["valid_max"]["error_displays"], 0)
        self.assertEqual(responses["valid_max"]["translations_ready"], 1)
        maximum = responses["maximum_display"]
        self.assertEqual(maximum["applied"], 1)
        self.assertEqual(maximum["applied_bytes"], 17417)
        self.assertEqual(maximum["applied_text"], "S" * 1023 + "\n译文：" + "T" * 16384)
        self.assertEqual(maximum["submitted_body"], "S" * 1023)
        for label in ("too_long", "nul", "malformed"):
            with self.subTest(response=label):
                self.assertEqual(responses[label]["invalid"], 1)
                self.assertEqual(responses[label]["error_displays"], 1)
                self.assertEqual(responses[label]["applied_text"], "source\n译文：返回内容无效")

    def test_build_gate_baselines_old_chat_and_preserves_unchanged_chinese(self) -> None:
        result = self._scenarios()
        gated = result["unverified"]
        self.assertEqual(gated["manifest"]["status"], "target_unverified")
        self.assertEqual(gated["heartbeat"], 0)
        self.assertEqual(gated["reads"], 0)

        baseline = result["baseline_chinese"]
        self.assertEqual(baseline["after_baseline"], 0)
        self.assertEqual(baseline["submits"], 1)
        self.assertEqual(baseline["submitted_body"], "你好，队友")
        self.assertEqual(baseline["applies"], 0)
        self.assertEqual(baseline["unchanged"], 1)
        self.assertEqual(baseline["cancels"], 1)
        self.assertGreaterEqual(baseline["duplicates"], 1)
        for private in ("你好，队友", "PRIVATE_ADAPTER_PROOF"):
            self.assertNotIn(private, baseline["manifest_json"])
            self.assertNotIn(private, baseline["output_json"])

    def test_english_bilingual_result_applies_once_and_stale_apply_is_terminal(self) -> None:
        result = self._scenarios()["apply_stale"]
        self.assertEqual(result["apply_count_after_first"], 1)
        self.assertEqual(result["apply_count"], 1)
        self.assertEqual(result["submitted_count"], 1)
        expected_source = 'Move to extraction\n"A1"'
        self.assertEqual(result["source_body"], expected_source)
        self.assertEqual(result["submitted_body"], expected_source)
        self.assertEqual(result["translated_text"], expected_source + "\n译文：前往撤离点")
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["stale_cancel_count"], 1)
        self.assertEqual(result["stale_body"], "Original chat")

    def test_filtered_notification_is_counted_without_submit(self) -> None:
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

    def test_short_errors_and_submit_response_apply_exceptions_are_safe(self) -> None:
        result = self._scenarios()
        cases = result["error_messages"]
        expected = {
            "http401": "API 密钥无效",
            "network": "网络连接失败",
            "bad_response": "返回内容无效",
        }
        for key, phrase in expected.items():
            with self.subTest(error=key):
                item = cases[key]
                self.assertEqual(item["text"], item["source"] + "\n译文：" + phrase)
                self.assertEqual(item["apply_count"], 1)
                self.assertEqual(item["translation_errors"], 1)
                self.assertEqual(item["response_invalid"], 0)
                self.assertEqual(item["translations_ready"], 0)
                self.assertEqual(item["error_displays_ready"], 1)
        same = cases["same_as_source"]
        self.assertEqual(same["translation_unchanged"], 0)
        self.assertEqual(same["text"], "网络连接失败\n译文：网络连接失败")
        unknown = cases["unknown_private"]
        self.assertEqual(unknown["text"], unknown["source"] + "\n译文：翻译失败，请稍后重试")
        self.assertNotIn("PRIVATE_SECRET_API_KEY", unknown["text"] + unknown["report"])

        exceptions = result["exceptions"]
        self.assertEqual(exceptions["submit_text"], "submit source\n译文：翻译服务异常")
        self.assertEqual(exceptions["submit_failures"], 1)
        self.assertEqual(exceptions["submit_count"], 1)
        self.assertEqual(exceptions["submit_applied"], 1)
        self.assertEqual(exceptions["submit_cancels"], 0)
        self.assertEqual(exceptions["submit_responses"], 0)
        self.assertEqual(exceptions["submit_adapter_errors"], 1)
        self.assertEqual(exceptions["submit_error_displays"], 1)
        self.assertEqual(exceptions["response_text"], "response source\n译文：翻译服务异常")
        self.assertEqual(exceptions["response_errors"], 1)
        self.assertEqual(exceptions["response_applied"], 1)
        self.assertEqual(exceptions["response_error_displays"], 1)
        self.assertEqual(exceptions["apply_errors"], 1)
        for report in (exceptions["submit_report"], exceptions["response_report"], exceptions["apply_report"]):
            for secret in ("PRIVATE_SUBMIT_SECRET", "PRIVATE_RESPONSE_SECRET", "PRIVATE_APPLY_SECRET"):
                self.assertNotIn(secret, report)


if __name__ == "__main__":
    unittest.main()
