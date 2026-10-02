"""固定中文显示测试的 LuaJIT 隔离回归。"""

from __future__ import annotations

import json
import io
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
import zipfile

from test_chat_observe import LUA_OBSERVER_ADAPTER_HARNESS
from test_chat_probe import LUA_DLL, ROOT, LuaJIT, builder
from test_chat_widgets import WIDGET_ADAPTER_CHECKS


DISPLAY_NATIVE_CHECKS = r'''
-- 生产setter已在source-extraction时换成fake spy，任何情况下都不调用真实native地址。
local expected_text = "聊天翻译测试成功"

prepare_widget_case()
DISPLAY_TEST_ENABLED = false
observer_display_native_gate = true
display_spy_calls = 0
local status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "target_unverified" and confirmed == false and display_spy_calls == 0,
    "disabled mode reached the native setter spy")

prepare_widget_case()
DISPLAY_TEST_ENABLED = true
observer_display_native_gate = false
status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "target_unverified" and confirmed == false and display_spy_calls == 0,
    "unverified target reached the native setter spy")

-- ASCII哨兵和event/owner匹配时仅调用一次spy，并把固定UTF-8测试串放入终止NUL的长生命周期pin。
prepare_widget_case()
observer_display_native_gate = true
display_spy_calls = 0
display_spy_write_property = true
status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "called_confirmed" and confirmed == true and display_spy_calls == 1)
assert(display_spy_widget == widget_slot_address + 0x110, "setter received the wrong widget argument")
assert(display_spy_text == expected_text and display_spy_terminated, "setter buffer UTF-8/NUL bytes changed")
local pins = rawget(_G, DISPLAY_PIN_TABLE)
assert(type(pins) == "table" and #pins == 1, "buffer was not retained in the process pin table")
collectgarbage("collect")
assert(ffi.string(pins[1]) == expected_text, "pinned buffer did not survive Lua garbage collection")

-- owner或event漂移、root复核变化、正文二读变化和预算不足均在调用setter前拒绝。
prepare_widget_case()
display_spy_calls = 0
status = observer_display_replace_ascii_widget(0, widget_event_slot + 1, observer_anon_id(widget_root))
assert(status == "stale" and display_spy_calls == 0, "event-slot mismatch reached the setter")

prepare_widget_case()
display_spy_calls = 0
status = observer_display_replace_ascii_widget(0, widget_event_slot, observer_anon_id(widget_root) + 1)
assert(status == "stale" and display_spy_calls == 0, "owner mismatch reached the setter")

prepare_widget_case()
display_spy_calls = 0
read_mutation = function(address, count)
    if address == widget_root_global and count == 2 then write_bytes(widget_root_global, pack64(widget_root_alternate)) end
end
status = observer_display_replace_ascii_widget(0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "stale" and display_spy_calls == 0, "root drift reached the setter")

prepare_widget_case()
display_spy_calls = 0
read_mutation = function(address, count)
    if address == widget_body and count == 2 then write_bytes(widget_body, widget_body_bytes(WIDGET_CJK)) end
end
status = observer_display_replace_ascii_widget(0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "stale" and display_spy_calls == 0, "body drift reached the setter")

prepare_widget_case()
display_spy_calls = 0
observer_read_budget = MAX_OBSERVER_READ - 4095
status = observer_display_replace_ascii_widget(0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "deferred" and display_spy_calls == 0 and query_calls == 0 and read_calls == 0,
    "budget deferral touched memory or reached the setter")

-- 调用后若属性指针未指向pin，只能报告未确认，仍不得把正文或地址写入结果。
prepare_widget_case()
observer_read_budget = 0
display_spy_calls = 0
display_spy_write_property = false
status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "called_unconfirmed" and confirmed == false and display_spy_calls == 1)

-- wrapper之后若目标key变化或另一个entry被改写，二次复核不确认显示结果。
prepare_widget_case()
observer_read_budget = 0
display_spy_calls = 0
display_spy_write_property = true
display_spy_corrupt_target_key = true
status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "called_unconfirmed" and confirmed == false and display_spy_calls == 1,
    "target key drift was incorrectly confirmed")

prepare_widget_case()
observer_read_budget = 0
display_spy_calls = 0
display_spy_corrupt_target_key = false
display_spy_add_extra_key = true
status, confirmed = observer_display_replace_ascii_widget(
    0, widget_event_slot, observer_anon_id(widget_root))
assert(status == "called_unconfirmed" and confirmed == false and display_spy_calls == 1,
    "unrelated entry drift was incorrectly confirmed")
RESULT = "display fake-setter checks ok"
'''


DISPLAY_CORE_GATE = r'''
local json_core = dofile([[SCAN_CORE_PATH]])
local core = dofile([[OBSERVE_CORE_PATH]])

local function run_case(config)
    local now = 0
    local calls, reads = 0, 0
    local slots = {}
    local adapter = {
        now_ms = function() return now end,
        begin_cycle = function() return nil end,
        read_slot = function() return nil end,
        finish_cycle = function() return nil end,
        ui_snapshot = function() return {screen_depth = 0, screen_ids = {}, controllers = {}} end,
        take_command = function() return nil end,
        output = function() end,
        read_widget_slot = function()
            reads = reads + 1
            if config.reader_status == "ascii" then return "ascii", {
                event_slot = 4, owner_anon_id = 77,
                pointer = "PRIVATE_POINTER", body = "PRIVATE_BODY",
            } end
            return config.reader_status or "no_match", {event_slot = 4, owner_anon_id = 77}
        end,
        replace_ascii_widget = function(slot, event_slot, owner_id)
            calls = calls + 1
            slots[#slots + 1] = {slot = slot, event_slot = event_slot, owner_id = owner_id}
            if config.defer_once and calls == 1 then return "deferred", false end
            return config.status or "called_confirmed", config.confirmed ~= false
        end,
    }
    local state = core.new(adapter, config.options)
    state.seen_ascii = config.seen_ascii == true
    state.seen_cjk = config.seen_cjk == true
    for index = 1, config.steps do now = index - 1; core.step(state) end
    local manifest = core.manifest(state)
    return {manifest = manifest, json = json_core.encode_json(manifest), calls = calls, reads = reads, slots = slots}
end

RESULT = json_core.encode_json({
    disabled = run_case({steps = 2, options = nil, seen_ascii = true, reader_status = "ascii"}),
    unverified = run_case({steps = 2, options = {display_test = true, target_verified = false}, seen_ascii = true, reader_status = "ascii"}),
    ordinary = run_case({steps = 2, options = {display_test = true, target_verified = true}, seen_ascii = true, reader_status = "no_match"}),
    cjk = run_case({steps = 2, options = {display_test = true, target_verified = true}, seen_cjk = true, reader_status = "cjk"}),
    retry = run_case({steps = 2, options = {display_test = true, target_verified = true}, seen_ascii = true, reader_status = "ascii", defer_once = true}),
    stale = run_case({steps = 3, options = {display_test = true, target_verified = true}, seen_ascii = true, reader_status = "ascii", status = "stale"}),
    unconfirmed = run_case({steps = 2, options = {display_test = true, target_verified = true}, seen_ascii = true, reader_status = "ascii", status = "called_unconfirmed", confirmed = false}),
})
'''


class ChatDisplayTests(unittest.TestCase):
    """核心、fake-kernel setter和构建模式的隔离验收。"""

    @classmethod
    def setUpClass(cls):
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行显示测试mock")
        cls.lua = LuaJIT(LUA_DLL)

    def test_core_gates_ascii_match_retries_deferred_once_and_sanitizes_manifest(self):
        scan_core = (ROOT / "game" / "chat_probe_core.lua").as_posix()
        observe_core = (ROOT / "game" / "chat_observe_core.lua").as_posix()
        script = DISPLAY_CORE_GATE.replace("SCAN_CORE_PATH", scan_core).replace(
            "OBSERVE_CORE_PATH", observe_core
        )
        results = json.loads(self.lua.run(script))

        for name, status in (("disabled", "disabled"), ("unverified", "target_unverified")):
            with self.subTest(mode=name):
                display = results[name]["manifest"]["display_test"]
                self.assertEqual(display, {"status": status, "attempts": 0, "property_confirmed": False})
                self.assertEqual(results[name]["calls"], 0)

        for name in ("ordinary", "cjk"):
            with self.subTest(widget_match=name):
                display = results[name]["manifest"]["display_test"]
                self.assertEqual(display["attempts"], 0)
                self.assertEqual(results[name]["calls"], 0)

        retry = results["retry"]
        self.assertEqual(retry["calls"], 2)
        self.assertEqual(retry["slots"], [
            {"slot": 0, "event_slot": 4, "owner_id": 77},
            {"slot": 0, "event_slot": 4, "owner_id": 77},
        ])
        self.assertEqual(retry["manifest"]["display_test"], {
            "status": "called_confirmed", "attempts": 1, "property_confirmed": True,
        })

        stale = results["stale"]["manifest"]["display_test"]
        self.assertEqual(stale, {"status": "stale", "attempts": 0, "property_confirmed": False})
        self.assertEqual(results["stale"]["calls"], 1, "stale result must stop retries")
        unconfirmed = results["unconfirmed"]["manifest"]["display_test"]
        self.assertEqual(unconfirmed, {"status": "called_unconfirmed", "attempts": 1, "property_confirmed": False})
        for result in results.values():
            for secret in ("PRIVATE_POINTER", "PRIVATE_BODY"):
                self.assertNotIn(secret, result["json"])

    def test_adapter_fake_setter_pins_utf8_and_rejects_stale_or_unverified_widget(self):
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        declaration = re.search(r"ffi\.cdef\[\[(.*?)\]\]", source, re.S)
        read_budget = re.search(r"local MAX_OBSERVER_READ = ([^\r\n]+)", source)
        self.assertIsNotNone(declaration)
        self.assertIsNotNone(read_budget)
        adapter_start = source.index("    local function observer_query_address(address)")
        adapter_end = source.index("    local function observer_path_with_suffix", adapter_start)
        ui_start = source.index("    local function observer_ui_failure(")
        ui_end = source.index("    local function make_observer_adapter()", ui_start)
        command_start = source.index("    local function observer_take_command()")
        command_end = source.index("    local function observer_ui_failure(", command_start)
        adapter = source[adapter_start:adapter_end]
        setter_start = adapter.index("    local function observer_display_setter(")
        native_start = adapter.index("        local target = ffi.cast(", setter_start)
        native_end = adapter.index("        return true\n    end", native_start)
        setter_spy = r'''
        display_spy_calls = display_spy_calls + 1
        display_spy_widget = tonumber(ffi.cast("size_t", widget_argument))
        display_spy_text = ffi.string(buffer)
        display_spy_terminated = buffer[#DISPLAY_TEST_TEXT] == 0
        if display_spy_write_property then
            local pointer = tonumber(ffi.cast("size_t", ffi.cast("void *", buffer)))
            local target_entry = display_spy_widget + 0x250
            -- 模拟native wrapper改写flag、正文指针和cached hash三个字段。
            write_bytes(target_entry + 4, pack32(1))
            write_bytes(target_entry + 8, pack64(pointer))
            write_bytes(target_entry + 16, pack32(0xC0DEC0DE))
            if display_spy_corrupt_target_key then write_bytes(target_entry, pack32(0xBADC0DE)) end
            if display_spy_add_extra_key then
                write_bytes(target_entry - 0x18, pack32(OBSERVER_WIDGET_KEY))
            end
        end
'''
        adapter = adapter[:native_start] + setter_spy + adapter[native_end:]
        adapter += "\n" + source[ui_start:ui_end]

        harness = LUA_OBSERVER_ADAPTER_HARNESS
        replacements = (
            (
                "local query_calls, read_calls = 0, 0",
                "local query_calls, read_calls = 0, 0\nlocal query_address_log, read_log = {}, {}",
            ),
            (
                'local numeric_address = tonumber(ffi.cast("size_t", address))\n'
                "    if query_fail_address == numeric_address then return 0 end\n"
                "    local region = find_region(numeric_address)",
                'local numeric_address = tonumber(ffi.cast("size_t", address))\n'
                "    query_address_log[#query_address_log + 1] = numeric_address\n"
                "    if query_fail_address == numeric_address then return 0 end\n"
                "    local region = find_region(numeric_address)",
            ),
            (
                "if query_mutation and query_calls == query_mutation.call then query_mutation.fn(region) end",
                "if query_mutation and (query_calls == query_mutation.call or query_mutation.address == numeric_address) then\n"
                "        query_mutation.fn(region)\n"
                "    end",
            ),
            (
                'local address = tonumber(ffi.cast("size_t", source))\n    read_counts[address] = (read_counts[address] or 0) + 1',
                'local address = tonumber(ffi.cast("size_t", source))\n'
                "    read_log[#read_log + 1] = {address = address, length = length}\n"
                "    read_counts[address] = (read_counts[address] or 0) + 1",
            ),
            (
                "read_counts = {}\n    query_mutation, read_mutation = nil, nil",
                "read_counts = {}; query_address_log = {}; read_log = {}\n"
                "    query_mutation, read_mutation = nil, nil",
            ),
        )
        for before, after in replacements:
            self.assertIn(before, harness)
            harness = harness.replace(before, after, 1)

        display_prelude = r'''
local DISPLAY_TEST_ENABLED = false
local observer_display_native_gate = false
local DISPLAY_TARGET_RVA = 0x1441CA0
local DISPLAY_TEST_TEXT = "聊天翻译测试成功"
local DISPLAY_PIN_TABLE = "__HD2_CHAT_DISPLAY_TEST_PINS_V1"
local DISPLAY_MAX_PINS = 16
local OBSERVER_WIDGET_KEY = 0x7518C954
local display_spy_calls = 0
local display_spy_widget, display_spy_text, display_spy_terminated
local display_spy_write_property = true
local display_spy_corrupt_target_key = false
local display_spy_add_extra_key = false
'''
        script = harness.replace("__FFI_DECL__", declaration.group(1))
        script = script.replace("__MAX_OBSERVER_READ__", read_budget.group(1).strip())
        script = script.replace("__OBSERVER_ADAPTER__", display_prelude + adapter)
        script = script.replace("__OBSERVER_TAKE_COMMAND__", source[command_start:command_end])
        checks = WIDGET_ADAPTER_CHECKS + "\n" + DISPLAY_NATIVE_CHECKS
        self.assertIn('RESULT = "adapter fake-kernel mocks ok"', script)
        script = script.replace('RESULT = "adapter fake-kernel mocks ok"', checks, 1)
        self.assertEqual(self.lua.run(script), "display fake-setter checks ok")

    def test_target_signature_and_display_builder_are_separate_from_default_and_observe(self):
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        scan_core = (ROOT / "game" / "chat_probe_core.lua").read_bytes()
        observe_core = (ROOT / "game" / "chat_observe_core.lua").read_bytes()
        display_rva = re.search(r"local DISPLAY_TARGET_RVA = ([^\r\n]+)", source)
        display_prefix = re.search(r'local DISPLAY_TARGET_PREFIX = "([^\r\n]+)"', source)
        self.assertIsNotNone(display_rva)
        self.assertIsNotNone(display_prefix)

        gate_start = source.index("    local function signatures_verified(manifest)")
        gate_end = source.index("    local function start_observer(", gate_start)
        gate_source = source[gate_start:gate_end]
        gate_script = (
            "local json_core = dofile([[" + (ROOT / "game" / "chat_probe_core.lua").as_posix() + "]])\n"
            + "local DISPLAY_TARGET_RVA = " + display_rva.group(1).strip() + "\n"
            + 'local DISPLAY_TARGET_PREFIX = "' + display_prefix.group(1) + '"\n'
            + gate_source
            + r'''
local function make_manifest(candidate, valid_signatures)
    local signatures = {}
    for index = 1, 6 do
        signatures[index] = {comparison = (not valid_signatures and index == 4) and "false" or "true"}
    end
    return {status = "scan_complete", known_signatures = signatures, candidates = candidate and {candidate} or {}}
end
local target = DISPLAY_TARGET_RVA
local prefix = DISPLAY_TARGET_PREFIX
local start = target - 10
local length = 31
local valid_candidate = {
    rva = target, window_rva = start, byte_length = length,
    bytes_hex = string.rep("00", 10) .. prefix,
}
local bad_prefix = {rva = target, window_rva = target, byte_length = 21,
    bytes_hex = "00" .. prefix:sub(3)}
local out_of_window = {rva = target, window_rva = target + 1, byte_length = 21, bytes_hex = prefix}
local short_window = {rva = target, window_rva = start, byte_length = 30,
    bytes_hex = string.rep("00", 10) .. prefix:sub(1, 40)}
local wrong_length = {rva = target, window_rva = target, byte_length = 21, bytes_hex = prefix .. "00"}
RESULT = json_core.encode_json({
    good = display_target_verified(make_manifest(valid_candidate, true)),
    bad_signatures = display_target_verified(make_manifest(valid_candidate, false)),
    bad_prefix = display_target_verified(make_manifest(bad_prefix, true)),
    out_of_window = display_target_verified(make_manifest(out_of_window, true)),
    short_window = display_target_verified(make_manifest(short_window, true)),
    wrong_length = display_target_verified(make_manifest(wrong_length, true)),
    absent = display_target_verified(make_manifest(nil, true)),
})
'''
        )
        gate_results = json.loads(self.lua.run(gate_script))
        self.assertEqual(gate_results, {
            "good": True, "bad_signatures": False, "bad_prefix": False,
            "out_of_window": False, "short_window": False, "wrong_length": False,
            "absent": False,
        })

        entry = source.encode("utf-8")
        default_entry = builder.entry_source(entry, scan_core)
        observe_entry = builder.entry_source(entry, scan_core, observe_core)
        display_entry = builder.entry_source(entry, scan_core, observe_core, display_test=True)
        self.assertIn(b"local DISPLAY_TEST_ENABLED = false", default_entry)
        self.assertIn(b"local DISPLAY_TEST_ENABLED = false", observe_entry)
        self.assertIn(b"local DISPLAY_TEST_ENABLED = true", display_entry)
        self.assertIn(b"local OBSERVE_ENABLED = true", display_entry)
        self.assertIn("聊天翻译测试成功".encode("utf-8"), display_entry)
        self.assertLessEqual(len(display_entry), builder.MAX_SOURCE_BYTES)

        minimal_old_entry = (
            b"local core = (function()\n" + builder.CORE_MARKER + b"\nend)()\n"
            + builder.OBSERVE_FLAG + b"\nlocal observer_core = (function()\n"
            + builder.OBSERVE_MARKER + b"\nend)()\n"
        )
        compat_default = builder.entry_source(minimal_old_entry, b"return {}", b"return nil")
        self.assertIn(b"local OBSERVE_ENABLED = true", compat_default)

        for packed in (default_entry, observe_entry, display_entry):
            text = packed.decode("utf-8")
            quoted = "[========[" + text + "]========]"
            self.assertEqual(self.lua.run("local chunk, err=loadstring(" + quoted + "); assert(chunk,err); RESULT='syntax ok'"), "syntax ok")

        files = builder.addon_files(display_entry, display_test=True)
        manifest = json.loads(files["manifest.json"])
        self.assertIn("固定聊天显示测试", manifest["Description"])
        self.assertIn("聊天翻译测试成功", manifest["Description"])
        self.assertNotIn("只读聊天观察器", manifest["Description"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "HD2ChatTranslate20260101000000.zip"
            receipt = root / "receipt.json"
            args = ["build_chat_probe.py", "--display-test", "--output", str(output)]
            with mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt), \
                    mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                builder.main()
            with zipfile.ZipFile(output) as package:
                self.assertIsNone(package.testzip())
                packaged_manifest = json.loads(package.read("manifest.json"))
                self.assertIn("固定聊天显示测试", packaged_manifest["Description"])
                archive = package.read("Addon/" + builder.ARCHIVE_NAME)
                record = struct.unpack_from("<7Q6I", archive, 104)
                payload = archive[record[2] : record[2] + record[7]]
                packaged_source = payload[8:]
                self.assertIn(b"local DISPLAY_TEST_ENABLED = true", packaged_source)
                self.assertIn("聊天翻译测试成功".encode("utf-8"), packaged_source)

            referenced = root / "deployed-display.zip"
            referenced.write_bytes(b"preserve deployed source")
            receipt.write_text(json.dumps({"sourceZips": {
                "probe": {"path": str(referenced)}, "loader": {"path": str(root / "loader.zip")},
            }}), encoding="utf-8")
            with mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt):
                with self.assertRaisesRegex(ValueError, "仍被部署收据引用"):
                    builder.build_artifact(referenced, display_test=True)
            self.assertEqual(referenced.read_bytes(), b"preserve deployed source")

            with mock.patch.object(builder, "DEPLOYMENT_RECEIPT", root / "missing-receipt.json"), \
                    mock.patch.object(sys, "argv", ["build_chat_probe.py", "--observe", "--display-test"]), \
                    redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    builder.main()


if __name__ == "__main__":
    unittest.main()
