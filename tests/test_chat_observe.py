"""只读聊天观察核心的 LuaJIT mock 与构建边界测试。"""

from __future__ import annotations

import json
import io
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
import zipfile

from test_chat_probe import LUA_DLL, ROOT, LuaJIT, builder


LUA_OBSERVE_HARNESS = r'''
local json_core = dofile([[SCAN_CORE_PATH]])
local core = dofile([[OBSERVE_CORE_PATH]])

local function make_body(config, read_index)
    local text
    if config.mode == "mutable_table" then
        text = read_index == 1 and "FIRST_MUTABLE_VALUE" or "SECOND_MUTABLE_VALUE"
    elseif config.mode == "unstable_reads" then
        text = read_index == 1 and "FIRST_READ_VALUE" or "SECOND_READ_VALUE"
    elseif config.mode == "overlong_utf8" then
        text = string.char(0xc0, 0xaf)
    elseif config.mode == "surrogate_utf8" then
        text = string.char(0xed, 0xa0, 0x80)
    elseif config.mode == "truncated_utf8" then
        text = string.char(0xe2, 0x82)
    elseif config.mode == "too_large" then
        return string.rep("L", 513)
    elseif config.mode == "boundary_512" then
        text = string.rep("B", 512)
    elseif config.mode == "unterminated" then
        return string.rep("N", 513)
    else
        text = (config.texts and config.texts[read_index and config.active_text_index or 1]) or ""
    end
    if config.raw_text ~= nil then text = config.raw_text end
    if #text > 512 then error("mock body exceeds fixed record capacity") end
    return text .. "\0" .. string.rep("\0", 512 - #text)
end

local function run_case(config)
    local now = 0
    local command_index = 0
    local read_counts = {}
    local slots_this_step = {}
    local max_slots_this_step = 0
    local read_calls = 0
    local slot_order = {}
    local outputs = {}
    local identity_bytes = "0xDEADBEEF!!!!!!"
    local shared_record = {identity = identity_bytes, body = string.rep("\0", 513), flags = string.rep("F", 8)}
    local base_metadata = config.metadata or {
        owner_id = 17,
        first = config.first or 0,
        count = config.count == nil and 1 or config.count,
    }

    local function metadata_copy(changed)
        return {
            owner_id = base_metadata.owner_id + (changed and 1 or 0),
            first = base_metadata.first,
            count = base_metadata.count,
        }
    end

    local adapter = {
        now_ms = function() return now end,
        ui_snapshot = function() return config.ui or {screen_depth = 0, screen_ids = {}, controllers = {}} end,
        take_command = function()
            command_index = command_index + 1
            if command_index <= (config.command_count or 0) then return "chat_open" end
            return nil
        end,
        begin_cycle = function() return metadata_copy(false) end,
        read_slot = function(slot)
            read_calls = read_calls + 1
            if not slots_this_step[slot] then
                slots_this_step[slot] = true
                if not read_counts[slot] then slot_order[#slot_order + 1] = slot end
            end
            read_counts[slot] = (read_counts[slot] or 0) + 1

            local text_index = ((slot - base_metadata.first) % 64) + 1
            config.active_text_index = text_index
            local body = make_body(config, read_counts[slot])
            local record
            if config.mode == "mutable_table" then
                shared_record.body = body
                record = shared_record
            else
                record = {identity = identity_bytes, body = body, flags = string.rep("F", 8)}
            end
            return record
        end,
        finish_cycle = function() return metadata_copy(config.finish_changes == true) end,
        output = function(manifest)
            outputs[#outputs + 1] = json_core.encode_json(manifest)
        end,
    }

    local state = core.new(adapter)
    local times = config.times or {}
    local step_count = config.steps or #times
    if step_count == 0 then step_count = 1 end
    for index = 1, step_count do
        now = times[index] or ((index - 1) * 500)
        slots_this_step = {}
        local done = core.step(state)
        local unique = 0
        for _ in pairs(slots_this_step) do unique = unique + 1 end
        max_slots_this_step = math.max(max_slots_this_step, unique)
        if done then break end
    end

    return {
        manifest = core.manifest(state),
        output_json = table.concat(outputs, "\n"),
        read_calls = read_calls,
        max_slots_per_step = max_slots_this_step,
        slot_order = slot_order,
    }
end

local cases = {
    recognize = run_case({
        first = 62,
        count = 4,
        texts = {"HD2CT_PROBE_ASCII_01", "HD2CT_PROBE_中文_02", "ordinary chat: KEEP THIS PRIVATE", "HD2CT_PROBE_ASCII_01 extra"},
    }),
    batch64 = run_case({first = 61, count = 64, steps = 8}),
    overlong = run_case({count = 1, mode = "overlong_utf8"}),
    surrogate = run_case({count = 1, mode = "surrogate_utf8"}),
    truncated = run_case({count = 1, mode = "truncated_utf8"}),
    too_large = run_case({count = 1, mode = "too_large"}),
    unterminated = run_case({count = 1, mode = "unterminated"}),
    boundary512 = run_case({count = 1, mode = "boundary_512"}),
    unstable_reads = run_case({count = 1, mode = "unstable_reads"}),
    mutable_table = run_case({count = 1, mode = "mutable_table"}),
    changing_metadata = run_case({count = 9, steps = 2, finish_changes = true}),
    invalid_first = run_case({metadata = {owner_id = 17, first = 64, count = 1}}),
    invalid_count = run_case({metadata = {owner_id = 17, first = 0, count = 65}}),
    empty_ui = run_case({count = 0}),
    u32_ui = run_case({count = 0, ui = {
        screen_depth = 1,
        screen_ids = {4294967295},
        controllers = {{kind = 4294967295, object_id = 9, vtable_rva = 74727423,
            vtable_functions = {8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201}}},
    }}),
    out_of_range_screen = run_case({count = 0, ui = {screen_depth = 1, screen_ids = {4294967296}, controllers = {}}}),
    bad_kind = run_case({count = 0, ui = {screen_depth = 0, screen_ids = {}, controllers = {{kind = 4294967296, object_id = 1}}}}),
    bad_vtable = run_case({count = 0, ui = {screen_depth = 0, screen_ids = {}, controllers = {{kind = 7, object_id = 1, vtable_rva = 74727424}}}}),
    snapshot_cap = run_case({count = 0, steps = 41, command_count = 40}),
    runtime_limit = run_case({count = 0, times = {0, 1800000}}),
    clock_backwards = run_case({count = 0, times = {1000, 1500, 1499}}),
}
RESULT = json_core.encode_json(cases)
'''


LUA_OBSERVER_ADAPTER_HARNESS = r'''
local ffi = require("ffi")
local base_assert = assert
local assert_count = 0
assert = function(value, message)
    assert_count = assert_count + 1
    if not value then error("mock assertion " .. assert_count .. ": " .. tostring(message), 2) end
    return value
end
ffi.cdef[[__FFI_DECL__]]
local module_base = ffi.cast("size_t", 0x10000000)
local expected_image_size = 74727424
local OBSERVER_IMAGE_SIZE = expected_image_size
local expected_section_end = 4096 + 34667155
local core = {SECTION = {rva = 4096, size = 34667155}}
local MAX_OBSERVER_ADDRESS = 0x7fffffffffff
local MAX_OBSERVER_READ = 16 * 1024
local observer_read_budget = 0
local observer_active_cycle = nil
local observer_ids = {}
local observer_id_count = 0
local observer_next_id = 0
local process = ffi.cast("void *", 1)
local regions = {}
local query_calls, read_calls = 0, 0
local query_mutation, read_mutation
local read_counts = {}

local function add_region(base, size, allocation_base, kind, protect, state)
    local region = {
        base = base, size = size, allocation_base = allocation_base,
        type = kind, protect = protect, state = state or 0x1000,
        data = ffi.new("HD2Probe_U8[?]", size),
    }
    regions[#regions + 1] = region
    return region
end

local function find_region(address)
    for _, region in ipairs(regions) do
        if address >= region.base and address < region.base + region.size then return region end
    end
    return nil
end

local function write_bytes(address, value)
    local region = assert(find_region(address), "fixture write outside region")
    assert(address + #value <= region.base + region.size, "fixture write crosses region")
    if #value > 0 then ffi.copy(region.data + (address - region.base), value, #value) end
end

local function pack32(value)
    return string.char(value % 256, math.floor(value / 256) % 256,
        math.floor(value / 65536) % 256, math.floor(value / 16777216) % 256)
end

local function pack64(value)
    local out = {}
    for index = 0, 7 do out[#out + 1] = string.char(math.floor(value / (256 ^ index)) % 256) end
    return table.concat(out)
end

local kernel = {}
function kernel.VirtualQuery(address, information, _)
    query_calls = query_calls + 1
    local numeric_address = tonumber(ffi.cast("size_t", address))
    local region = find_region(numeric_address)
    if not region then return 0 end
    if query_mutation and query_calls == query_mutation.call then query_mutation.fn(region) end
    information[0].BaseAddress = ffi.cast("void *", region.base)
    information[0].AllocationBase = ffi.cast("void *", region.allocation_base)
    information[0].RegionSize = region.size
    information[0].State = region.state
    information[0].Protect = region.protect
    information[0].Type = region.type
    return ffi.sizeof(information[0])
end

function kernel.ReadProcessMemory(_, source, destination, length, bytes_read)
    read_calls = read_calls + 1
    local address = tonumber(ffi.cast("size_t", source))
    read_counts[address] = (read_counts[address] or 0) + 1
    if read_mutation then read_mutation(address, read_counts[address]) end
    local region = find_region(address)
    if not region or address + length > region.base + region.size then return 0 end
    ffi.copy(destination, region.data + (address - region.base), length)
    bytes_read[0] = length
    return 1
end

local function reset_counters()
    query_calls, read_calls = 0, 0
    read_counts = {}
    query_mutation, read_mutation = nil, nil
end

__OBSERVER_ADAPTER__

-- 跨4KiB页并跨两个同AllocationBase区域的读取应成功，且每页做两次查询。
local same_a = add_region(0x20000, 4096, 0x20000, 0x20000, 0x04)
local same_b = add_region(0x21000, 4096, 0x20000, 0x20000, 0x04)
for i = 0, 4095 do same_a.data[i] = i % 251; same_b.data[i] = (i + 3) % 251 end
reset_counters(); observer_read_budget = 0
local page_data = observer_read(0x20ff0, 32)
assert(page_data == ffi.string(same_a.data + 4080, 16) .. ffi.string(same_b.data, 16))
assert(query_calls == 4 and read_calls == 2 and observer_read_budget == 32)

-- 跨AllocationBase请求应在第二页拒绝；第一次已完成的读取仍计入预算。
add_region(0x30000, 4096, 0x30000, 0x20000, 0x04)
add_region(0x31000, 4096, 0x31000, 0x20000, 0x04)
reset_counters(); observer_read_budget = 0
assert(observer_read(0x30ff0, 32) == nil)
assert(read_calls == 1 and observer_read_budget == 32)

-- 第二次VirtualQuery发现保护位变化时，必须在native读之前拒绝。
add_region(0x50000, 4096, 0x50000, 0x20000, 0x04)
reset_counters(); observer_read_budget = 0
query_mutation = {call = 2, fn = function(region) region.protect = 0x20 end}
assert(observer_read(0x50000, 16) == nil)
assert(query_calls == 2 and read_calls == 0 and observer_read_budget == 16)

-- VirtualQuery失败也计入已请求字节；无法映射的区域不触发native读取。
reset_counters(); observer_read_budget = 0
assert(observer_read(0x90000, 24) == nil)
assert(query_calls == 1 and read_calls == 0 and observer_read_budget == 24)

-- 失败的请求预算达到16KiB后，额外读取会被预算挡住，不再查页或读内存。
reset_counters(); observer_read_budget = MAX_OBSERVER_READ - 8
assert(observer_read(0x20000, 16) == nil)
assert(query_calls == 0 and read_calls == 0 and observer_read_budget == MAX_OBSERVER_READ - 8)

-- 建立假的只读模块页与聊天对象；root指针和history首槽按64槽环绕。
local module_global_page = math.floor((tonumber(module_base) + 0x347CE28) / 4096) * 4096
add_region(module_global_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local root_global = tonumber(module_base) + 0x347CEF0
local owner_global = tonumber(module_base) + 0x347CE28
local context, chat = 0x100000, 0x100000 + 0xC418
add_region(0x100000, 0x20000, 0x100000, 0x20000, 0x04)
local metadata = chat + 0x9590
write_bytes(root_global, pack64(context))
write_bytes(metadata, pack32(62) .. pack32(4))
for _, slot in ipairs({62, 63, 0, 1}) do
    local base = chat + slot * 0x228
    local body = "HD2CT_PROBE_ASCII_01" .. string.rep("\0", 513 - #"HD2CT_PROBE_ASCII_01")
    write_bytes(base + 0xB90, string.rep(string.char(slot + 1), 8))
    write_bytes(base + 0xBA0, body)
    write_bytes(base + 0xDA0, string.char(0, 1, 2, 3, 4, 5, 6, 7))
end
reset_counters(); observer_read_budget = 0
local cycle = observer_begin_cycle()
assert(cycle and cycle.first == 62 and cycle.count == 4 and cycle.owner_id > 0)
for _, slot in ipairs({62, 63, 0, 1}) do
    local record = observer_read_slot(slot)
    assert(record and #record.identity == 16 and #record.body == 513 and #record.flags == 8)
end
assert(observer_read_slot(2) == nil)
local finished = observer_finish_cycle()
assert(finished and finished.first == 62 and finished.count == 4)

-- root漂移和history metadata漂移都使当前cycle失效。
reset_counters(); observer_read_budget = 0
cycle = observer_begin_cycle(); assert(cycle)
write_bytes(root_global, pack64(0x110000))
assert(observer_read_slot(62) == nil and observer_finish_cycle() == nil)
write_bytes(root_global, pack64(context))
reset_counters(); observer_read_budget = 0
cycle = observer_begin_cycle(); assert(cycle)
write_bytes(metadata, pack32(61) .. pack32(4))
assert(observer_read_slot(62) == nil and observer_finish_cycle() == nil)
write_bytes(metadata, pack32(62) .. pack32(4))

-- UI snapshot记录净化结构；stack和rows在结束复核时变化则拒绝整份快照。
local owner, dispatch, controller = 0x200000, 0x300000, 0x400000
add_region(owner, 0x10000, owner, 0x20000, 0x04)
add_region(dispatch, 0x10000, dispatch, 0x20000, 0x04)
add_region(controller, 4096, controller, 0x20000, 0x04)
local dispatch_global_page = math.floor((tonumber(module_base) + 0x3326E68) / 4096) * 4096
add_region(dispatch_global_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local vtable = tonumber(module_base) + 0x3000000
local vtable_page = math.floor(vtable / 4096) * 4096
add_region(vtable_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local stack_address = owner + 0x429C
local dispatch_rows = dispatch + 0x5744
local dispatch_count = dispatch + 0x5740
local stack = pack32(11) .. pack32(22) .. string.rep("\0", 12) .. pack32(2)
write_bytes(owner_global, pack64(owner))
write_bytes(stack_address, stack)
write_bytes(tonumber(module_base) + 0x3326E68, pack64(dispatch))
write_bytes(dispatch_count, pack32(1))
write_bytes(dispatch_rows, pack64(controller) .. pack32(0x1234) .. pack32(0))
write_bytes(controller, pack64(vtable))
write_bytes(vtable, pack64(tonumber(module_base) + 0x1000) .. pack64(tonumber(module_base) + 0x2000) .. string.rep("\0", 48))
reset_counters(); observer_read_budget = 0
local snapshot = observer_ui_snapshot()
assert(snapshot and snapshot.screen_depth == 2 and snapshot.screen_ids[1] == 11 and snapshot.screen_ids[2] == 22,
    string.format("snapshot nil q=%d r=%d budget=%d", query_calls, read_calls, observer_read_budget))
assert(snapshot.controllers[1].kind == 0x1234 and snapshot.controllers[1].object_id ~= controller)
assert(snapshot.controllers[1].vtable_rva == 0x3000000)
assert(#snapshot.controllers[1].vtable_functions == 2)

reset_counters(); observer_read_budget = 0
local stack_changed = pack32(11) .. pack32(99) .. string.rep("\0", 12) .. pack32(2)
read_mutation = function(address, count)
    if address == stack_address and count == 2 then write_bytes(stack_address, stack_changed) end
end
assert(observer_ui_snapshot() == nil)

reset_counters(); observer_read_budget = 0
write_bytes(stack_address, stack)
local changed_rows = pack64(controller) .. pack32(0x5678) .. pack32(0)
read_mutation = function(address, count)
    if address == dispatch_rows and count == 2 then write_bytes(dispatch_rows, changed_rows) end
end
assert(observer_ui_snapshot() == nil)
RESULT = "adapter fake-kernel mocks ok"
'''


class ObserveLuaCoreTests(unittest.TestCase):
    """在独立 LuaJIT state 中运行 mock；不加载 game.dll 或连接游戏进程。"""

    @classmethod
    def setUpClass(cls):
        if LUA_DLL is None:
            raise RuntimeError("本机未提供 LuaJIT lua51.dll，不能执行要求的 observer core mocks")
        cls.lua = LuaJIT(LUA_DLL)
        cls.scan_core_path = (ROOT / "game" / "chat_probe_core.lua").as_posix()
        cls.observe_core_path = (ROOT / "game" / "chat_observe_core.lua").as_posix()

    def run_mock_cases(self) -> dict:
        script = LUA_OBSERVE_HARNESS.replace("SCAN_CORE_PATH", self.scan_core_path).replace(
            "OBSERVE_CORE_PATH", self.observe_core_path
        )
        return json.loads(self.lua.run(script))

    def test_fixed_ascii_and_cjk_matching_privacy_and_ring_wrap(self):
        result = self.run_mock_cases()["recognize"]
        manifest = result["manifest"]
        self.assertEqual(manifest["cycles_completed"], 1)
        self.assertEqual(manifest["entries_seen"], 4)
        self.assertEqual(manifest["ascii_matches"], 1)
        self.assertEqual(manifest["cjk_matches"], 1)
        self.assertEqual(result["slot_order"], [62, 63, 0, 1])
        self.assertEqual(manifest["owner_anon_id"], 17)
        for private_value in (
            "HD2CT_PROBE_ASCII_01",
            "HD2CT_PROBE_中文_02",
            "ordinary chat: KEEP THIS PRIVATE",
            "0xDEADBEEF",
            "FFFFFFFF",
        ):
            self.assertNotIn(private_value, result["output_json"])

    def test_history_reads_are_bounded_to_eight_slots_per_step(self):
        result = self.run_mock_cases()["batch64"]
        self.assertEqual(result["manifest"]["cycles_completed"], 1)
        self.assertEqual(result["manifest"]["entries_seen"], 64)
        self.assertEqual(result["read_calls"], 128)
        self.assertLessEqual(result["max_slots_per_step"], 8)
        self.assertEqual(result["slot_order"][:4], [61, 62, 63, 0])

    def test_utf8_body_boundaries_and_inconsistent_double_reads_are_dropped(self):
        results = self.run_mock_cases()
        for name, expected_counter in (
            ("overlong", "invalid_utf8_entries"),
            ("surrogate", "invalid_utf8_entries"),
            ("truncated", "invalid_utf8_entries"),
            ("too_large", "missing_body_terminators"),
            ("unterminated", "missing_body_terminators"),
        ):
            with self.subTest(case=name):
                manifest = results[name]["manifest"]
                self.assertEqual(manifest["cycles_discarded"], 1)
                self.assertEqual(manifest["entries_seen"], 0)
                self.assertEqual(manifest[expected_counter], 1)

        boundary = results["boundary512"]["manifest"]
        self.assertEqual(boundary["cycles_completed"], 1)
        self.assertEqual(boundary["body_bytes_min"], 512)
        self.assertEqual(boundary["body_bytes_max"], 512)

        for name in ("unstable_reads", "mutable_table"):
            with self.subTest(case=name):
                manifest = results[name]["manifest"]
                self.assertEqual(manifest["cycles_discarded"], 1)
                self.assertEqual(manifest["entries_seen"], 0)
                self.assertEqual(manifest["unstable_entries"], 1)

    def test_metadata_drift_discards_the_entire_cycle_and_rejects_bad_ranges(self):
        results = self.run_mock_cases()
        drift = results["changing_metadata"]["manifest"]
        self.assertEqual(drift["cycles_completed"], 0)
        self.assertEqual(drift["cycles_discarded"], 1)
        self.assertEqual(drift["metadata_changes"], 1)
        self.assertEqual(drift["entries_seen"], 0)
        self.assertEqual(drift["ascii_matches"], 0)
        self.assertEqual(drift["cjk_matches"], 0)

        for name in ("invalid_first", "invalid_count"):
            with self.subTest(case=name):
                manifest = results[name]["manifest"]
                self.assertEqual(manifest["invalid_cycle_metadata"], 1)
                self.assertEqual(manifest["entries_seen"], 0)

    def test_empty_ui_u32_kind_screen_bounds_and_vtable_limits(self):
        results = self.run_mock_cases()
        empty = results["empty_ui"]["manifest"]["ui_snapshots"][0]
        self.assertEqual(empty["screen_ids"], [])
        self.assertEqual(empty["controllers"], [])

        valid = results["u32_ui"]["manifest"]["ui_snapshots"][0]
        self.assertEqual(valid["screen_ids"], [4294967295])
        controller = valid["controllers"][0]
        self.assertEqual(controller["kind"], 4294967295)
        self.assertEqual(controller["vtable_rva"], 74727423)
        self.assertEqual(len(controller["vtable_functions"]), 8)
        self.assertEqual(controller["vtable_functions"], list(range(8192, 8200)))

        out_of_range_screen = results["out_of_range_screen"]["manifest"]
        self.assertEqual(out_of_range_screen["snapshot_count"], 0)
        self.assertEqual(out_of_range_screen["snapshot_failures"], 1)
        bad_kind = results["bad_kind"]["manifest"]
        self.assertEqual(bad_kind["snapshot_count"], 0)
        self.assertEqual(bad_kind["snapshot_failures"], 1)
        bad_vtable = results["bad_vtable"]["manifest"]["ui_snapshots"][0]["controllers"][0]
        self.assertNotIn("vtable_rva", bad_vtable)

    def test_ui_snapshot_cap_runtime_limit_and_backwards_clock(self):
        results = self.run_mock_cases()
        capped = results["snapshot_cap"]["manifest"]
        self.assertEqual(capped["snapshot_attempts"], 32)
        self.assertEqual(capped["snapshot_count"], 32)
        self.assertEqual(capped["snapshots_dropped"], 9)

        runtime = results["runtime_limit"]["manifest"]
        self.assertEqual(runtime["status"], "observer_stopped")
        self.assertEqual(runtime["stop_reason"], "runtime_limit")
        self.assertEqual(runtime["elapsed_ms"], 1800000)

        backwards = results["clock_backwards"]["manifest"]
        self.assertEqual(backwards["status"], "observer_stopped")
        self.assertEqual(backwards["stop_reason"], "clock_not_monotonic")
        self.assertEqual(backwards["clock_failures"], 1)


class ObserveBuilderAndAdapterTests(unittest.TestCase):
    """检查两种构建模式、来源包保护及无需游戏进程的适配器静态边界。"""

    @classmethod
    def setUpClass(cls):
        if LUA_DLL is None:
            raise RuntimeError("本机未提供 LuaJIT lua51.dll，不能执行打包 Lua 语法检查")
        cls.lua = LuaJIT(LUA_DLL)
        cls.entry = (ROOT / "game" / "chat_probe.lua").read_bytes()
        cls.scan_core = (ROOT / "game" / "chat_probe_core.lua").read_bytes()
        cls.observe_core = (ROOT / "game" / "chat_observe_core.lua").read_bytes()

    def compile_only(self, source: bytes) -> None:
        text = source.decode("utf-8")
        delimiter = "[========["
        closing = "]========]"
        self.assertNotIn(closing, text)
        script = (
            "local chunk, err = loadstring(" + delimiter + text + closing + "); "
            "assert(chunk, err); RESULT='syntax ok'"
        )
        self.assertEqual(self.lua.run(script), "syntax ok")

    def test_default_and_observe_modes_are_independent_and_lua_syntax_is_valid(self):
        default_entry = builder.entry_source(self.entry, self.scan_core)
        observe_entry = builder.entry_source(self.entry, self.scan_core, self.observe_core)

        self.assertIn(b"local OBSERVE_ENABLED = false", default_entry)
        self.assertIn(b"local OBSERVE_ENABLED = true", observe_entry)
        self.assertNotIn(builder.CORE_MARKER, default_entry)
        self.assertNotIn(builder.OBSERVE_MARKER, default_entry)
        self.assertNotIn(builder.CORE_MARKER, observe_entry)
        self.assertNotIn(builder.OBSERVE_MARKER, observe_entry)
        self.assertIn(b"return nil", default_entry)
        self.assertIn("只读聊天正文与界面结构观察核心".encode("utf-8"), observe_entry)
        self.assertLessEqual(len(default_entry), builder.MAX_SOURCE_BYTES)
        self.assertLessEqual(len(observe_entry), builder.MAX_SOURCE_BYTES)

        self.compile_only(default_entry)
        self.compile_only(observe_entry)

        files = builder.addon_files(observe_entry, observe=True)
        manifest = json.loads(files["manifest.json"])
        self.assertIn("只读聊天观察器", manifest["Description"])
        archive = files["Addon/9ba626afa44a3aa3.patch_0"]
        record = struct.unpack_from("<7Q6I", archive, 104)
        payload = archive[record[2] : record[2] + record[7]]
        source = payload[8:]
        self.assertIn(b"local OBSERVE_ENABLED = true", source)
        self.assertIn(b"TEST_ASCII", source)
        self.assertIn(b"TEST_CJK", source)
        self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.stream"], b"")
        self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.gpu_resources"], b"")

    def test_builder_rejects_merged_source_over_512_kib(self):
        source = (
            b"local core = (function()\n"
            + builder.CORE_MARKER
            + b"\nend)()\nlocal OBSERVE_ENABLED = false --[[HD2_CHAT_OBSERVER_ENABLED]]\n"
            + b"local observer_core = (function()\n"
            + builder.OBSERVE_MARKER
            + b"\nend)()\n"
        )
        scan_core = b"s" * (300 * 1024)
        observe_core = b"o" * (300 * 1024)
        self.assertLessEqual(max(map(len, (source, scan_core, observe_core))), builder.MAX_SOURCE_BYTES)
        with self.assertRaisesRegex(ValueError, "嵌入后的 Lua 源文件超过构建大小上限"):
            builder.entry_source(source, scan_core, observe_core)

    def test_observe_cli_builds_crc_valid_archive_with_embedded_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "observe.zip"
            receipt = root / "no-deployment-receipt.json"
            arguments = ["build_chat_probe.py", "--observe", "--output", str(output)]
            with (
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt),
                mock.patch.object(sys, "argv", arguments),
                redirect_stdout(io.StringIO()),
            ):
                builder.main()

            with zipfile.ZipFile(output) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(
                    set(package.namelist()),
                    {
                        "Addon/9ba626afa44a3aa3.patch_0",
                        "Addon/9ba626afa44a3aa3.patch_0.gpu_resources",
                        "Addon/9ba626afa44a3aa3.patch_0.stream",
                        "manifest.json",
                    },
                )
                manifest = json.loads(package.read("manifest.json"))
                self.assertIn("只读聊天观察器", manifest["Description"])
                archive = package.read("Addon/9ba626afa44a3aa3.patch_0")
                record = struct.unpack_from("<7Q6I", archive, 104)
                payload = archive[record[2] : record[2] + record[7]]
                source = payload[8:]
                self.assertIn(b"local OBSERVE_ENABLED = true", source)
                self.assertIn(b"TEST_ASCII", source)
                self.assertIn(b"TEST_CJK", source)
                self.assertNotIn(builder.OBSERVE_MARKER, source)

    def test_adapter_memory_and_ui_behaviors_with_own_process_fake_kernel(self):
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        declaration = re.search(r"ffi\.cdef\[\[(.*?)\]\]", source, re.S)
        self.assertIsNotNone(declaration)
        adapter_start = source.index("    local function observer_query_address(address)")
        adapter_end = source.index("    local function observer_path_with_suffix", adapter_start)
        ui_start = source.index("    local function observer_ui_snapshot()")
        ui_end = source.index("    local function make_observer_adapter()", ui_start)
        adapter = source[adapter_start:adapter_end] + "\n" + source[ui_start:ui_end]
        script = LUA_OBSERVER_ADAPTER_HARNESS.replace("__FFI_DECL__", declaration.group(1)).replace(
            "__OBSERVER_ADAPTER__", adapter
        )
        self.assertEqual(self.lua.run(script), "adapter fake-kernel mocks ok")

    def test_builder_protects_receipt_referenced_source_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_zip = root / "deployed-source.zip"
            old_zip.write_bytes(b"keep the deployed source unchanged")
            other_zip = root / "loader.zip"
            receipt = root / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "sourceZips": {
                            "probe": {"path": str(old_zip), "sha256": "a" * 64},
                            "loader": {"path": str(other_zip), "sha256": "b" * 64},
                        }
                    }
                ),
                encoding="utf-8",
            )
            original = old_zip.read_bytes()
            with mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt):
                with self.assertRaisesRegex(ValueError, "仍被部署收据引用"):
                    builder.build_artifact(old_zip, observe=True)
            self.assertEqual(old_zip.read_bytes(), original)

if __name__ == "__main__":
    unittest.main()
