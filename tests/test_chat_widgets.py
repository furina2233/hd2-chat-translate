from __future__ import annotations

import json
import re
import unittest

from lua_support import LUA_DLL, ROOT, LuaJIT
from test_chat_observe import LUA_OBSERVER_ADAPTER_HARNESS


WIDGET_ADAPTER_CHECKS = r'''
-- 建立独立的控件、属性表和事件环缓冲区；所有地址均为本LuaJIT测试进程内的假地址。
local widget_root = 0x5000000
local widget_manager = widget_root + 0x14498
local widget_root_global = tonumber(module_base) + 0x346D538
local widget_global_page = math.floor(widget_root_global / 4096) * 4096
local widget_root_size = 0x4F7080 + 0x12D08
local widget_root_region = add_region(widget_root, widget_root_size, widget_root, 0x20000, 0x04)
local widget_manager_region = widget_root_region
local widget_global_region = add_region(widget_global_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local widget_ring = widget_root + 0x4F7080
local widget_ring_end = widget_ring + 64 * 0x4B4
local widget_metadata = widget_ring + 0x12D00
local widget_slot_address = widget_manager + 0x4390
local widget_map = widget_slot_address + 0x220
local widget_map_count = widget_map + 0x158
local widget_entries = widget_map + 8
local widget_event_slot = 4
local widget_event_record = widget_ring + widget_event_slot * 0x4B4
local widget_body = widget_event_record + 0xB4
local widget_root_alternate = 0xA000000
local WIDGET_KEY = 0x7518C954
local WIDGET_EVENT = 0x1C12037F
local WIDGET_ASCII = "HD2CT_PROBE_ASCII_01"
local WIDGET_CJK = "HD2CT_PROBE_中文_02"

local function widget_body_bytes(text, terminated)
    if terminated == false then return string.rep("X", 1024) end
    return text .. "\0" .. string.rep("\0", 1023 - #text)
end

local function widget_record_address(index)
    return widget_ring + index * 0x4B4
end

local function widget_map_address(index)
    return widget_manager + 0x4390 + index * 0x3D8 + 0x220
end

local function write_widget_map(count, matching_indices, pointer)
    write_bytes(widget_map_count, string.char(count))
    local count_region = find_region(widget_map_count)
    local stored_count = ffi.string(count_region.data + (widget_map_count - count_region.base), 1):byte(1)
    assert(stored_count == count, "widget map count write failed before entries")
    for index = 0, math.min(count, 14) - 1 do
        local key = 0x12340000 + index
        local value = 0
        if matching_indices[index] then key = WIDGET_KEY; value = pointer end
        local address = widget_entries + index * 0x18
        write_bytes(address, pack32(key) .. pack32(0) .. pack64(value) .. pack64(0))
        local current_count = ffi.string(count_region.data + (widget_map_count - count_region.base), 1):byte(1)
        assert(current_count == count,
            "widget entry overwrote count at index " .. tostring(index) .. ": " .. tostring(current_count))
    end
end

local function write_widget_record(index, event_code, body)
    local record = widget_record_address(index)
    write_bytes(record, pack32(event_code))
    write_bytes(record + 0xB4, body)
end

local function prepare_widget_case(options)
    options = options or {}
    widget_root_region.protect = 0x04
    widget_manager_region.protect = 0x04
    widget_global_region.protect = 0x02
    local event_slot = options.event_slot == nil and 4 or options.event_slot
    local next_index = options.next_index == nil and 5 or options.next_index
    local active_count = options.active_count == nil and 2 or options.active_count
    local map_count = options.map_count == nil and 14 or options.map_count
    local target_index = options.target_index == nil and map_count - 1 or options.target_index
    local event_code = options.event_code == nil and WIDGET_EVENT or options.event_code
    local pointer = options.pointer
    if pointer == nil then pointer = widget_record_address(event_slot) + 0xB4 end

    write_bytes(widget_root_global, pack64(widget_root))
    write_bytes(widget_metadata, pack32(next_index) .. pack32(active_count))
    local matches = {}
    if target_index >= 0 and target_index < map_count then matches[target_index] = true end
    if options.duplicate_index ~= nil then matches[options.duplicate_index] = true end
    write_widget_map(map_count, matches, pointer)
    local map_region = find_region(widget_map_count)
    local written_count = ffi.string(map_region.data + (widget_map_count - map_region.base), 1):byte(1)
    assert(written_count == map_count,
        "widget fixture count mismatch: " .. tostring(written_count) .. " at " .. string.format("%x", widget_map_count))
    write_widget_record(event_slot, event_code, options.body or widget_body_bytes(WIDGET_ASCII))
    reset_counters()
    observer_read_budget = 0
    return event_slot, pointer
end

local function expect_widget_status(expected, options)
    prepare_widget_case(options)
    local status, sample = observer_widget_read_widget_slot(0)
    if status ~= expected then
        local touched = {}
        for _, operation in ipairs(read_log) do
            touched[#touched + 1] = string.format("%x/%d", operation.address, operation.length)
        end
        error("unexpected widget read status: expected " .. expected .. ", got " .. tostring(status)
            .. "; count_reads=" .. tostring(read_counts[widget_map_count])
            .. "; reads=" .. table.concat(touched, ","))
    end
    assert(observer_read_budget <= MAX_OBSERVER_READ, "widget reader exceeded the per-step budget")
    return sample
end

-- 第14项仍可匹配；第15项只读count并拒绝，不读取entries。
local sample = expect_widget_status("ascii")
assert(sample and sample.event_slot == widget_event_slot and sample.owner_anon_id > 0)
assert(read_counts[widget_entries] == 2, "the valid 14-entry map was not double-read")

prepare_widget_case({map_count = 15})
local status = observer_widget_read_widget_slot(0)
assert(status == "count_out_of_range")
assert(read_counts[widget_entries] == nil, "15-entry map read its entries")

-- value指针必须精确等于活动record的正文起点；偏移一字节与非活动槽均拒绝且不读正文。
expect_widget_status("pointer_outside", {pointer = widget_body + 1})
assert(read_counts[widget_body] == nil, "offset pointer caused a body read")
local inactive_record = widget_record_address(3)
expect_widget_status("inactive", {event_slot = 3, next_index = 5, active_count = 1})
assert(read_counts[inactive_record] == nil, "inactive event record was read")

-- 普通正文、错误event code、缺失属性和重复key均只返回固定状态。
expect_widget_status("no_match", {body = widget_body_bytes("ordinary widget text")})
local filtered_sample = expect_widget_status("event_type_mismatch", {event_code = 0x12345678})
assert(filtered_sample == nil, "a non-matching event produced a chat sample")
assert(read_counts[widget_event_record] == 2, "event type mismatch was not stable across reads")
assert(read_counts[widget_body] == nil, "event type mismatch caused a body read")
expect_widget_status("key_missing", {target_index = -1})
expect_widget_status("ambiguous_key", {duplicate_index = 0})
expect_widget_status("empty", {map_count = 0, target_index = -1})

-- 正文位于最后一条record时只读取其固定1024字节；邻接的metadata NUL不能补足正文终止符。
local last_index = 63
local last_body = widget_record_address(last_index) + 0xB4
expect_widget_status("missing_terminator", {
    event_slot = last_index, next_index = 0, active_count = 1,
    body = widget_body_bytes("", false),
})
assert(read_counts[last_body] == 2, "fixed body range was not read exactly twice")
assert(read_counts[widget_ring_end] == 2, "body scan read beyond the ring into adjacent metadata")
for _, operation in ipairs(read_log) do
    if operation.address >= widget_ring and operation.address < widget_ring_end then
        assert(operation.address + operation.length <= widget_ring_end, "native read crossed the event ring boundary")
    end
end

-- 在正文页的fake VirtualQuery上切换成不可读保护，确认不会调用fake ReadProcessMemory读取正文。
prepare_widget_case()
query_mutation = {
    address = widget_body,
    fn = function(region) region.protect = 0x20 end,
}
status = observer_widget_read_widget_slot(0)
assert(status == "read_failed")
assert(read_counts[widget_body] == nil, "protected widget body reached ReadProcessMemory")

-- root漂移时只复核缓存的全局槽，不跟随新root读取其ring或manager。
prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_root_global and count == 2 then
        write_bytes(widget_root_global, pack64(widget_root_alternate))
    end
end
status = observer_widget_read_widget_slot(0)
assert(status == "unstable", "root drift was not rejected as unstable")
for _, address in ipairs(query_address_log) do
    assert(address < widget_root_alternate or address >= widget_root_alternate + widget_root_size,
        "reader followed a newly observed root pointer")
end

-- root环metadata、map count、entries、event code和正文的二读变化都必须丢弃结果。
prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_metadata and count == 2 then write_bytes(widget_metadata, pack32(5) .. pack32(3)) end
end
assert(observer_widget_read_widget_slot(0) == "unstable", "ring metadata drift was accepted")

prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_map_count and count == 2 then write_bytes(widget_map_count, string.char(13)) end
end
assert(observer_widget_read_widget_slot(0) == "unstable", "map count drift was accepted")

prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_entries and count == 2 then
        write_bytes(widget_entries + 13 * 0x18, pack32(WIDGET_KEY) .. pack32(0)
            .. pack64(widget_body + 1) .. pack64(0))
    end
end
assert(observer_widget_read_widget_slot(0) == "unstable", "entry drift was accepted")

prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_event_record and count == 2 then write_bytes(widget_event_record, pack32(0x12345678)) end
end
assert(observer_widget_read_widget_slot(0) == "unstable", "event code drift was accepted")

prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_body and count == 2 then write_bytes(widget_body, widget_body_bytes(WIDGET_CJK)) end
end
assert(observer_widget_read_widget_slot(0) == "unstable", "body drift was accepted")

-- 剩余预算小于4096时立即defer，不查询页面、不读取内存、也不追加预算。
prepare_widget_case()
observer_read_budget = MAX_OBSERVER_READ - 4095
local budget_before = observer_read_budget
status = observer_widget_read_widget_slot(0)
assert(status == "deferred" and observer_read_budget == budget_before)
assert(query_calls == 0 and read_calls == 0, "deferred widget probe touched the fake kernel")
'''


WIDGET_CORE_HARNESS = r'''
local json_core = dofile([[SCAN_CORE_PATH]])
local core = dofile([[OBSERVE_CORE_PATH]])

local function run_case(config)
    local now = 0
    local step_index = 0
    local calls = 0
    local slots = {}
    local per_step = {}
    local adapter = {
        now_ms = function() return now end,
        begin_cycle = function() return nil end,
        read_slot = function() return nil end,
        finish_cycle = function() return nil end,
        ui_snapshot = function() return {screen_depth = 0, screen_ids = {}, controllers = {}} end,
        take_command = function() return nil end,
        output = function() end,
    }
    if not config.disabled then
        adapter.read_widget_slot = function(slot)
            calls = calls + 1
            slots[#slots + 1] = slot
            per_step[step_index] = (per_step[step_index] or 0) + 1
            if config.mode == "defer_first" and calls == 1 then
                return "deferred", {raw = "PRIVATE_DEFERRED_EXTRA"}
            end
            if config.mode == "match" then
                return "ascii", {
                    event_slot = (slot + 5) % 64,
                    owner_anon_id = 900 + slot,
                    raw_pointer = "PRIVATE_WIDGET_POINTER",
                    body = "PRIVATE_WIDGET_BODY",
                    arbitrary = {secret = "PRIVATE_NESTED_FIELD"},
                }
            end
            if config.mode == "evil_status" then
                return "PRIVATE_WIDGET_STATUS", {event_slot = 1, owner_anon_id = 2}
            end
            return "no_match", {raw_body = "ordinary widget text must never be emitted"}
        end
    end
    local state = core.new(adapter)
    state.seen_ascii = config.seen ~= false
    state.seen_cjk = config.seen_cjk == true
    for index = 1, config.steps do
        step_index = index
        now = index - 1
        core.step(state)
    end
    local maximum = 0
    for _, count in pairs(per_step) do maximum = math.max(maximum, count) end
    local manifest = core.manifest(state)
    return {
        manifest = manifest,
        output_json = json_core.encode_json(manifest),
        calls = calls,
        slots = slots,
        max_per_step = maximum,
    }
end

local cases = {
    deferred = run_case({steps = 4, mode = "defer_first"}),
    cap = run_case({steps = 140, mode = "match"}),
    ordinary = run_case({steps = 3, mode = "ordinary"}),
    evil_status = run_case({steps = 2, mode = "evil_status"}),
    no_seen_signal = run_case({steps = 3, mode = "ordinary", seen = false}),
    disabled = run_case({steps = 3, mode = "ordinary", disabled = true}),
}
RESULT = json_core.encode_json(cases)
'''


class ChatWidgetAdapterTests(unittest.TestCase):
    """动态抽取生产reader，在本进程的LuaJIT fake-kernel内验证控件定位。"""

    @classmethod
    def setUpClass(cls):
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行widget fake-kernel测试")
        cls.lua = LuaJIT(LUA_DLL)

    def build_adapter_script(self) -> str:
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
        adapter = source[adapter_start:adapter_end] + "\n" + source[ui_start:ui_end]

        harness = LUA_OBSERVER_ADAPTER_HARNESS
        replacements = (
            (
                "local query_calls, read_calls = 0, 0",
                "local query_calls, read_calls = 0, 0\nlocal query_address_log, read_log = {}, {}",
            ),
            (
                "local numeric_address = tonumber(ffi.cast(\"size_t\", address))\n"
                "    if query_fail_address == numeric_address then return 0 end\n"
                "    local region = find_region(numeric_address)",
                "local numeric_address = tonumber(ffi.cast(\"size_t\", address))\n"
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
                "local address = tonumber(ffi.cast(\"size_t\", source))\n    read_counts[address] = (read_counts[address] or 0) + 1",
                "local address = tonumber(ffi.cast(\"size_t\", source))\n"
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
        script = harness.replace("__FFI_DECL__", declaration.group(1))
        script = script.replace("__MAX_OBSERVER_READ__", read_budget.group(1).strip())
        script = script.replace("__OBSERVER_ADAPTER__", adapter)
        script = script.replace("__OBSERVER_TAKE_COMMAND__", source[command_start:command_end])
        checks = WIDGET_ADAPTER_CHECKS + '\nRESULT = "widget adapter fake-kernel mocks ok"'
        self.assertIn('RESULT = "adapter fake-kernel mocks ok"', script)
        return script.replace('RESULT = "adapter fake-kernel mocks ok"', checks, 1)

    def test_reader_uses_bounded_fake_memory_and_revalidates_records(self):
        self.assertEqual(self.lua.run(self.build_adapter_script()), "widget adapter fake-kernel mocks ok")

    def test_core_widget_probe_limits_and_sanitizes_callback_results(self):
        scan_core = (ROOT / "game" / "chat_probe_core.lua").as_posix()
        observe_core = (ROOT / "game" / "chat_observe_core.lua").as_posix()
        script = WIDGET_CORE_HARNESS.replace("SCAN_CORE_PATH", scan_core).replace(
            "OBSERVE_CORE_PATH", observe_core
        )
        results = json.loads(self.lua.run(script))

        deferred = results["deferred"]
        probe = deferred["manifest"]["widget_probe"]
        self.assertEqual(deferred["slots"], [0, 0, 1, 2])
        self.assertEqual(probe["attempts"], 3)
        self.assertEqual(probe["status_counts"]["deferred"], 1)
        self.assertEqual(probe["status_counts"]["no_match"], 3)
        self.assertEqual(deferred["max_per_step"], 1)

        capped = results["cap"]
        probe = capped["manifest"]["widget_probe"]
        self.assertEqual(capped["calls"], 128)
        self.assertEqual(capped["max_per_step"], 1)
        self.assertEqual(capped["slots"][:64], list(range(64)))
        self.assertEqual(capped["slots"][64:], list(range(64)))
        self.assertEqual(probe["status"], "complete")
        self.assertEqual(probe["attempts"], 128)
        self.assertEqual(probe["status_counts"]["ascii"], 128)
        self.assertEqual(len(probe["matches"]), 64)
        self.assertEqual(probe["matches_dropped"], 64)
        self.assertEqual(
            set(probe["matches"][0]),
            {"widget_slot", "event_slot", "owner_anon_id", "seen_ascii", "seen_cjk"},
        )
        for private_value in (
            "PRIVATE_WIDGET_POINTER",
            "PRIVATE_WIDGET_BODY",
            "PRIVATE_NESTED_FIELD",
            "ordinary widget text must never be emitted",
        ):
            self.assertNotIn(private_value, capped["output_json"])

        ordinary = results["ordinary"]["manifest"]["widget_probe"]
        self.assertEqual(ordinary["status_counts"]["no_match"], 3)
        self.assertEqual(ordinary["matches"], [])
        self.assertNotIn("ordinary widget text", results["ordinary"]["output_json"])

        evil_status = results["evil_status"]["manifest"]["widget_probe"]
        self.assertEqual(evil_status["status_counts"]["read_failed"], 2)
        self.assertNotIn("PRIVATE_WIDGET_STATUS", results["evil_status"]["output_json"])

        no_signal = results["no_seen_signal"]["manifest"]["widget_probe"]
        self.assertEqual(no_signal["attempts"], 0)
        self.assertEqual(no_signal["status"], "waiting")
        self.assertEqual(results["no_seen_signal"]["calls"], 0)
        disabled = results["disabled"]["manifest"]["widget_probe"]
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(disabled["attempts"], 0)
        self.assertEqual(results["disabled"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
