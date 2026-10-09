"""LuaJIT mock 测试：扫描核心、Lua 入口和边界行为。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from lua_support import LUA_DLL, LuaJIT, ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import build_outgoing_probe  # noqa: E402
import build_package  # noqa: E402


LUA_CORE_HARNESS = r'''
local core = dofile([[CORE_PATH]])
local function put16(bytes, offset, value)
    bytes[offset + 1] = value % 256
    bytes[offset + 2] = math.floor(value / 256) % 256
end
local function put32(bytes, offset, value)
    for index = 0, 3 do bytes[offset + index + 1] = math.floor(value / (256 ^ index)) % 256 end
end
local function put_text(bytes, offset, text)
    for index = 1, #text do bytes[offset + index] = text:byte(index) end
end
local function make_header(config)
    local bytes = {}
    for index = 1, 4096 do bytes[index] = 0 end
    put_text(bytes, 0, "MZ")
    put32(bytes, 0x3c, 0x80)
    put_text(bytes, 0x80, string.char(80, 69, 0, 0))
    local file = 0x84
    put16(bytes, file, 0x8664)
    put16(bytes, file + 2, 2)
    put32(bytes, file + 4, config and config.mode == "pemismatch" and 1790161982 or 1790161983)
    put16(bytes, file + 16, 240)
    local optional = file + 20
    put16(bytes, optional, 0x20b)
    put32(bytes, optional + 56, 74727424)
    put32(bytes, optional + 60, 4096)
    local sections = optional + 240
    put_text(bytes, sections, string.rep(" ", 8))
    put32(bytes, sections + 8, 34667155)
    put32(bytes, sections + 12, 4096)
    put32(bytes, sections + 36, 0x60000020)
    put_text(bytes, sections + 40, ".data" .. string.rep("\0", 3))
    put32(bytes, sections + 48, 4096)
    put32(bytes, sections + 52, 0x200000)
    put32(bytes, sections + 76, 0x40000040)
    local chunks = {}
    for start = 1, 4096, 512 do
        local part = {}
        for index = start, start + 511 do part[#part + 1] = string.char(bytes[index]) end
        chunks[#chunks + 1] = table.concat(part)
    end
    return table.concat(chunks)
end
local function from_hex(value)
    return (value:gsub("..", function(pair) return string.char(tonumber(pair, 16)) end))
end
local function same(a, b)
    return a and b and a.start_rva == b.start_rva and a.size == b.size
        and a.allocation_base == b.allocation_base and a.type == b.type
        and a.state == b.state and a.protect == b.protect
end
local function run_case(config)
    local section_start = core.SECTION.rva
    local section_end = section_start + core.SECTION.size
    local mapped_length = config.mapped_length or 65536
    local mapped_end = section_start + mapped_length
    local header = make_header(config)
    local code
    if config.mode == "dense" then
        local pattern_groups = {
            "94950000",
            "90950000",
            "18c40000",
            "8b87949500008b8f90950000",
        }
        local dense_parts = {}
        for _, hex in ipairs(pattern_groups) do
            for _ = 1, 32 do dense_parts[#dense_parts + 1] = from_hex(hex) .. string.rep(string.char(0), 16 - #hex / 2) end
        end
        local dense_code = table.concat(dense_parts)
        code = dense_code .. string.rep(string.char(0), mapped_length - #dense_code)
    else
        code = string.rep(string.char(0), mapped_length)
    end
    if config.signature_offset then
        local signature = from_hex("8b87949500008b8f90950000")
        local offset = config.signature_offset
        code = code:sub(1, offset) .. signature .. code:sub(offset + #signature + 1)
    end
    local query_count = 0
    local read_count = 0
    local requested_code_bytes = 0
    local section_query_count = 0
    local function query(rva)
        query_count = query_count + 1
        if rva < section_start then
            return {start_rva = 0, size = section_start, allocation_base = true,
                type = 0x1000000, state = 0x1000, protect = 0x02}
        end
        if config.mode == "zero" and rva == section_start then
            return {start_rva = section_start, size = 0, allocation_base = true,
                type = 0x1000000, state = 0x1000, protect = 0x20}
        end
        if rva >= section_start and rva < mapped_end then
            local protect, region_type, region_state = 0x20, 0x1000000, 0x1000
            if config.mode == "nonimage" then region_type = 0x20000 end
            if config.mode == "nonexec" then protect = 0x02 end
            if config.mode == "guard" then protect = 0x120 end
            if config.mode == "permissionswitch" and rva == section_start then
                section_query_count = section_query_count + 1
                if section_query_count == 2 then protect = 0x40 end
            end
            return {start_rva = section_start, size = mapped_length, allocation_base = true,
                type = region_type, state = region_state, protect = protect}
        end
        if rva >= mapped_end and rva < section_end then
            return {start_rva = mapped_end, size = section_end - mapped_end,
                allocation_base = true, type = 0x1000000, state = 0x2000, protect = 0}
        end
        return nil
    end
    local adapter = {
        hash_file = function()
            if config.mode == "hashmismatch" then return string.rep("0", 64), core.SOURCE.disk_size end
            if config.mode == "sizemismatch" then return core.SOURCE.sha256, core.SOURCE.disk_size - 1 end
            return core.SOURCE.sha256, core.SOURCE.disk_size
        end,
        query = query,
        read = function(rva, length, executable)
            read_count = read_count + 1
            if rva >= section_start then requested_code_bytes = requested_code_bytes + length end
            if config.mode == "shortread" and rva == section_start then
                return string.rep(string.char(0), math.max(0, length - 1))
            end
            if rva + length > core.SOURCE.size_of_image then return nil end
            local output = {}
            local cursor = rva
            while cursor < rva + length do
                local region = query(cursor)
                local allowed = region and region.allocation_base and region.type == 0x1000000
                    and region.state == 0x1000
                    and (region.protect == 0x02 or region.protect == 0x04 or region.protect == 0x08
                        or region.protect == 0x20 or region.protect == 0x40 or region.protect == 0x80)
                    and (not executable or region.protect == 0x20 or region.protect == 0x40 or region.protect == 0x80)
                if not allowed then return nil end
                local region_end = region.start_rva + region.size
                if region.start_rva > cursor or region_end <= cursor then return nil end
                local chunk_end = math.min(rva + length, region_end, math.floor(cursor / 4096 + 1) * 4096)
                local rechecked = query(cursor)
                if not same(region, rechecked) then return nil end
                if cursor < section_start then
                    local header_end = math.min(chunk_end, section_start)
                    output[#output + 1] = header:sub(cursor + 1, header_end)
                else
                    local code_end = math.min(chunk_end, mapped_end)
                    if code_end <= cursor then return nil end
                    output[#output + 1] = code:sub(cursor - section_start + 1, code_end - section_start)
                end
                cursor = chunk_end
            end
            local data = table.concat(output)
            if #data ~= length then return nil end
            return data
        end,
        hash_bytes = function() return string.rep("a", 64) end,
    }
    local state = core.new(adapter)
    if state.outgoing_probe then error("default core.new unexpectedly enabled outgoing probe") end
    local done, manifest = false, nil
    local max_frame_code_request = 0
    for _ = 1, 256 do
        local before = requested_code_bytes
        done, manifest = core.step(state)
        max_frame_code_request = math.max(max_frame_code_request, requested_code_bytes - before)
        if done then break end
    end
    if not done then error("mock scan exceeded frame cap: " .. config.name) end
    local pattern_slots = 0
    for _ in pairs(state.pattern_last_seen) do pattern_slots = pattern_slots + 1 end
    return {manifest = manifest, query_calls = query_count, read_calls = read_count,
        pattern_slots = pattern_slots, max_frame_code_request = max_frame_code_request}
end
local outgoing_windows = {
    {rva = 0x1097500, size = 0x3000},
    {rva = 0x185f000, size = 0x2000},
    {rva = 0xbeaf00, size = 0x1800},
    {rva = 0xbde300, size = 0x1000},
    {rva = 0x1327f00, size = 0x2000},
    {rva = 0x174fa00, size = 0x800},
    {rva = 0x20bba00, size = 0x1800},
    {rva = 0x18f2d00, size = 0x1800},
}
local function in_window(rva, length)
    for index, window in ipairs(outgoing_windows) do
        if rva >= window.rva and rva < window.rva + window.size
            and length <= window.rva + window.size - rva then return index end
    end
    return nil
end
local function make_window_bytes(window_index, rva, length, window)
    local output = {}
    local first = rva - window.rva
    for offset = 0, length - 1 do
        output[#output + 1] = string.char((window_index * 29 + first + offset) % 256)
    end
    return table.concat(output)
end
local function expected_window_hex(window_index, window)
    local bytes = make_window_bytes(window_index, window.rva, window.size, window)
    return (bytes:gsub(".", function(value) return string.format("%02x", value:byte()) end))
end
local function run_outgoing_case(config)
    local header = make_header(config)
    local query_calls, read_calls, total_read_bytes = 0, 0, 0
    local max_step_read_bytes, max_step_read_calls, max_read_request = 0, 0, 0
    local outside_query, outside_read = false, false
    local read_ranges = {}
    local function query(rva)
        query_calls = query_calls + 1
        if rva >= 0 and rva < 4096 then
            return {start_rva = 0, size = 4096, allocation_base = true,
                type = 0x1000000, state = 0x1000, protect = 0x02}
        end
        local window_index = in_window(rva, 1)
        if window_index then
            if config.mode == "gapfailure" and window_index == 1 then
                return {start_rva = rva + 1, size = outgoing_windows[window_index].size - 1,
                    allocation_base = true, type = 0x1000000, state = 0x1000, protect = 0x20}
            end
            local state = config.mode == "regionfailure" and window_index == 2 and 0x2000 or 0x1000
            return {start_rva = outgoing_windows[window_index].rva,
                size = outgoing_windows[window_index].size, allocation_base = true,
                type = 0x1000000, state = state, protect = 0x20}
        end
        outside_query = true
        return nil
    end
    local adapter = {
        hash_file = function()
            if config.mode == "hashmismatch" then return string.rep("0", 64), core.SOURCE.disk_size end
            return core.SOURCE.sha256, core.SOURCE.disk_size
        end,
        query = query,
        read = function(rva, length, executable)
            read_calls = read_calls + 1
            total_read_bytes = total_read_bytes + length
            max_read_request = math.max(max_read_request, length)
            read_ranges[#read_ranges + 1] = {rva = rva, size = length, executable = executable}
            if length > 1024 then outside_read = true end
            if not executable and rva + length <= 4096 and length <= 1024 then
                return header:sub(rva + 1, rva + length)
            end
            local window_index = in_window(rva, length)
            if not window_index then outside_read = true; return nil end
            local region, rechecked = query(rva), query(rva)
            if not same(region, rechecked) or not executable then return nil end
            if config.mode == "shortread" and window_index == 1 and rva == outgoing_windows[1].rva + 4096 then
                return make_window_bytes(window_index, rva, length - 1, outgoing_windows[window_index])
            end
            return make_window_bytes(window_index, rva, length, outgoing_windows[window_index])
        end,
        hash_bytes = function() return string.rep("a", 64) end,
    }
    local state = core.new(adapter, {outgoing_probe = true})
    local done, manifest = false, nil
    for _ = 1, 64 do
        local before = total_read_bytes
        local calls_before = read_calls
        done, manifest = core.step(state, 16 * 1024)
        max_step_read_bytes = math.max(max_step_read_bytes, total_read_bytes - before)
        max_step_read_calls = math.max(max_step_read_calls, read_calls - calls_before)
        if done then break end
    end
    if not done then error("outgoing probe exceeded frame cap: " .. config.name) end
    local expected = {}
    for index, window in ipairs(outgoing_windows) do
        expected[index] = expected_window_hex(index, window)
    end
    return {manifest = manifest, query_calls = query_calls, read_calls = read_calls,
        total_read_bytes = total_read_bytes, max_step_read_bytes = max_step_read_bytes,
        max_step_read_calls = max_step_read_calls, max_read_request = max_read_request,
        outside_query = outside_query, outside_read = outside_read,
        read_ranges = read_ranges, expected_hex = expected}
end
local cases = {
    {name = "cross_block", mapped_length = 65536, signature_offset = 16384 - 6},
    {name = "shortread", mode = "shortread", mapped_length = 8192, signature_offset = 4096 - 6},
    {name = "hole", mode = "hole", mapped_length = 4096, signature_offset = 4096 - 6},
    {name = "permissionswitch", mode = "permissionswitch", mapped_length = 32768, signature_offset = 4096 - 6},
    {name = "nonimage", mode = "nonimage", mapped_length = 4096},
    {name = "nonexec", mode = "nonexec", mapped_length = 4096},
    {name = "guard", mode = "guard", mapped_length = 4096},
    {name = "zero", mode = "zero", mapped_length = 4096},
    {name = "dense", mode = "dense", mapped_length = 32768},
    {name = "hashmismatch", mode = "hashmismatch", mapped_length = 4096},
    {name = "sizemismatch", mode = "sizemismatch", mapped_length = 4096},
}
local results = {}
for _, config in ipairs(cases) do results[config.name] = run_case(config) end
local outgoing_cases = {
    {name = "success"},
    {name = "hashmismatch", mode = "hashmismatch"},
    {name = "pemismatch", mode = "pemismatch"},
    {name = "regionfailure", mode = "regionfailure"},
    {name = "gapfailure", mode = "gapfailure"},
    {name = "shortread", mode = "shortread"},
}
results.outgoing = {}
for _, config in ipairs(outgoing_cases) do results.outgoing[config.name] = run_outgoing_case(config) end
RESULT = core.encode_json(results)
'''


LUA_OUTGOING_LIFECYCLE_HARNESS = r'''
local core = dofile([[CORE_PATH]])
local probe_module = dofile([[PROBE_PATH]])
local function make_adapter(config)
    config.samples = config.samples or 0
    config.writes = config.writes or 0
    config.adapter_calls = config.adapter_calls or 0
    config.reports = config.reports or {}
    local function called() config.adapter_calls = config.adapter_calls + 1 end
    local adapter = {}
    adapter.is_ready = function() called(); return config.ready == true end
    adapter.now_ms = function() called(); return config.now end
    adapter.prepare_report = function()
        called()
        config.prepared = (config.prepared or 0) + 1
        return true
    end
    adapter.sample = function(phase)
        called()
        config.samples = config.samples + 1
        if config.throw_sample then error(config.throw_sample) end
        if config.sample_factory then return config.sample_factory(phase, config.samples) end
        return {
            generation = config.generation or 0,
            root_changed = config.root_changed or 0,
            submit_flag = config.submit_flag or 0,
            body_length = config.body_length or 0,
            input_flags = config.input_flags or 0,
            history_head = config.history_head or 0,
            history_count = config.history_count or 0,
            read_failures = config.read_failures or 0,
            read_bytes = config.read_bytes or 100,
            body_text = config.body_text,
            manager_slots = config.manager_slots or {},
            input_slots = config.input_slots or {},
        }
    end
    adapter.encode_json = function(value)
        called()
        config.encodes = (config.encodes or 0) + 1
        return core.encode_json(value)
    end
    adapter.write_report = function(value)
        called()
        config.writes = config.writes + 1
        if config.fail_write then error("secret report path") end
        config.reports[#config.reports + 1] = value
        config.last_report = value
        return true
    end
    return adapter
end
local function pack(...)
    return {n = select("#", ...), ...}
end
local function new_probe(config, duration)
    return probe_module.new(make_adapter(config), {duration_ms = duration})
end

local gated = {ready = false, now = 0, body_text = "PRIVATE_BODY_SENTINEL"}
gated.sample_factory = function(phase)
    return {
        generation = 0,
        submit_flag = phase == "addon_pre_update" and 1 or 0,
        body_length = phase == "addon_pre_update" and 12 or 0,
        input_flags = 0, history_head = 0, history_count = 0,
        read_failures = 0, read_bytes = 100, body_text = gated.body_text,
    }
end
local gated_probe = new_probe(gated, 100)
gated_probe.step("addon_pre_update")
local gate_result = {
    reads = gated.samples,
    prepares = gated.prepared or 0,
    writes = gated.writes,
        adapter_calls = gated.adapter_calls,
}
gated.ready = true
gated_probe.step("addon_pre_update")
gated_probe.step("addon_post_update")
local pre_post = gated_probe.manifest()
for _ = 1, 20 do
    gated_probe.step("addon_pre_update")
    gated_probe.step("addon_post_update")
end
local idle_events = #gated_probe.manifest().events
gated.now = 5000
gated_probe.step("addon_pre_update")
gated_probe.step("addon_post_update")
local idle_writes = gated.writes

local changes = {ready = true, now = 1, submit_flag = 1, body_length = 12}
local changes_probe = new_probe(changes, 10000)
changes_probe.step("addon_pre_update")
changes.submit_flag, changes.body_length = 0, 0
changes_probe.step("addon_post_update")
local change_report = changes_probe.manifest()

local generation_mismatch = {ready = true, now = 5}
generation_mismatch.sample_factory = function(phase)
    return {
        generation = phase == "addon_pre_update" and 0 or 1,
        submit_flag = phase == "addon_pre_update" and 1 or 0,
        body_length = phase == "addon_pre_update" and 12 or 0,
        input_flags = 0, history_head = 0, history_count = 0,
        read_failures = 0, read_bytes = 100,
    }
end
local generation_mismatch_probe = new_probe(generation_mismatch, 10000)
generation_mismatch_probe.step("addon_pre_update")
generation_mismatch_probe.step("addon_post_update")

local failed_clear = {ready = true, now = 6}
failed_clear.sample_factory = function(phase)
    return {
        generation = 0,
        submit_flag = phase == "addon_pre_update" and 1 or -1,
        body_length = phase == "addon_pre_update" and 12 or -1,
        input_flags = 0, history_head = 0, history_count = 0,
        read_failures = phase == "addon_pre_update" and 0 or 1,
        read_bytes = 100,
    }
end
local failed_clear_probe = new_probe(failed_clear, 10000)
failed_clear_probe.step("addon_pre_update")
failed_clear_probe.step("addon_post_update")

local root_change = {ready = true, now = 2, generation = 1, root_changed = 1}
local root_probe = new_probe(root_change, 10000)
root_probe.step("addon_pre_update")
local root_report = root_probe.manifest()

local failed_read = {
    ready = true, now = 3, submit_flag = -1, body_length = -1,
    input_flags = -1, history_head = -1, history_count = -1, read_failures = 3,
}
local failed_read_probe = new_probe(failed_read, 10000)
failed_read_probe.step("addon_pre_update")
local failed_read_report = failed_read_probe.manifest()

local missing_terminator = {ready = true, now = 4, body_length = -2}
local missing_terminator_probe = new_probe(missing_terminator, 10000)
missing_terminator_probe.step("addon_pre_update")
local missing_terminator_report = missing_terminator_probe.manifest()

local cap = {ready = true, now = 0}
cap.sample_factory = function(_phase, count)
    return {
        generation = count, submit_flag = count % 2, body_length = count % 804,
        input_flags = 1099511627775 - count,
        history_head = 4294967295 - count, history_count = 4294967295 - count,
        read_failures = 0, read_bytes = 1106,
    }
end
local cap_probe = new_probe(cap, 30000)
for index = 1, 600 do
    cap_probe.step(index % 2 == 1 and "addon_pre_update" or "addon_post_update")
end
local cap_before_deadline = cap_probe.manifest()
local cap_event_count_before_deadline = #cap_before_deadline.events
cap.now = 30000
cap_probe.step("addon_post_update")
local cap_report = cap.last_report
local cap_encoded = core.encode_json(cap_report)

local deadline = {ready = true, now = 100}
local deadline_probe = new_probe(deadline, 10)
deadline_probe.step("addon_pre_update")
deadline.now = 110
deadline_probe.step("addon_post_update")

local write_failure = {ready = true, now = 0, fail_write = true}
local write_failure_probe = new_probe(write_failure, 10000)
write_failure_probe.step("addon_pre_update")
write_failure.now = 5000
write_failure_probe.step("addon_post_update")
local write_failure_report = write_failure_probe.manifest()

local thrown = {ready = true, now = 0, throw_sample = "private adapter error"}
local thrown_probe = new_probe(thrown, 10000)
local original_calls = 0
local received_args
local wrapped = thrown_probe.wrap_update(function(...)
    original_calls = original_calls + 1
    received_args = pack(...)
    return "left", nil, "right"
end)
local returned = pack(wrapped("first", nil, "third"))
local wrapped_manifest = thrown_probe.manifest()

local completed = {ready = true, now = 0}
local completed_probe = new_probe(completed, 10)
local completed_original_calls = 0
local completed_wrapper = completed_probe.wrap_update(function(...)
    completed_original_calls = completed_original_calls + 1
    return "complete", nil
end)
completed_wrapper("start")
completed.now = 10
local completed_deadline_result = pack(completed_wrapper("deadline"))
local completed_adapter_calls = completed.adapter_calls
completed.now = 20
local completed_after_result = pack(completed_wrapper("after"))
local completed_after_adapter_calls = completed.adapter_calls

local original_error_probe = new_probe({ready = true, now = 0}, 10000)
local error_wrapper = original_error_probe.wrap_update(function() error("original update failure") end)
local original_error_ok, original_error = pcall(error_wrapper)

RESULT = core.encode_json({
    gate = gate_result,
    pre_post = pre_post,
    idle = {events = idle_events, writes = idle_writes},
    changes = change_report,
    generation_mismatch_clear = generation_mismatch_probe.manifest().counters.body_cleared_pairs,
    failed_clear = failed_clear_probe.manifest().counters.body_cleared_pairs,
    root_change = root_report,
    failed_read = failed_read_report,
    missing_terminator = missing_terminator_report,
    cap = {
        before = cap_event_count_before_deadline,
        dropped_before = cap_before_deadline.counters.events_dropped,
        events = #cap_report.events,
        last_frame = cap_report.events[#cap_report.events].frame,
        dropped = cap_report.counters.events_dropped,
        bytes = #cap_encoded,
        writes = cap.writes,
        encodes = cap.encodes,
    },
    deadline = {
        status = deadline_probe.manifest().status,
        reason = deadline_probe.manifest().reason,
        writes = deadline.writes,
        report_status = deadline.last_report.status,
    },
    write_failure = {
        status = write_failure_report.status,
        reason = write_failure_report.reason,
        writes = write_failure.writes,
        report_json = core.encode_json(write_failure_report),
    },
    wrapped = {
        status = wrapped_manifest.status,
        reason = wrapped_manifest.reason,
        original_calls = original_calls,
        arg_count = received_args.n,
        arg_two_is_nil = received_args[2] == nil,
        arg_three = received_args[3],
        result_count = returned.n,
        result_one = returned[1],
        result_two_is_nil = returned[2] == nil,
        result_three = returned[3],
        writes = thrown.writes,
        written_report_reason = thrown.last_report and thrown.last_report.reason,
        report_json = core.encode_json(wrapped_manifest),
    },
    completed_wrapper = {
        status = completed_probe.manifest().status,
        writes = completed.writes,
        adapter_calls_at_done = completed_adapter_calls,
        adapter_calls_after = completed_after_adapter_calls,
        original_calls = completed_original_calls,
        deadline_count = completed_deadline_result.n,
        deadline_value = completed_deadline_result[1],
        deadline_nil = completed_deadline_result[2] == nil,
        after_count = completed_after_result.n,
        after_value = completed_after_result[1],
        after_nil = completed_after_result[2] == nil,
    },
    original_error = {ok = original_error_ok, message = original_error},
})
'''


@unittest.skipUnless(LUA_DLL is not None, "本机未提供 LuaJIT lua51.dll")
class LuaCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = LuaJIT(LUA_DLL)
        cls.core_path = (ROOT / "game" / "chat_probe_core.lua").as_posix()

    def test_mock_scans_bounds_gaps_permissions_hash_and_caps(self):
        script = LUA_CORE_HARNESS.replace("CORE_PATH", self.core_path)
        result = json.loads(self.lua.run(script))

        cross = result["cross_block"]["manifest"]
        self.assertEqual(cross["status"], "scan_complete")
        self.assertEqual(cross["scan"]["pattern_matches"]["history_signature"], 1)
        self.assertGreaterEqual(len(cross["candidates"]), 1)
        self.assertLessEqual(result["cross_block"]["max_frame_code_request"], 16 * 1024)
        self.assertLessEqual(result["dense"]["max_frame_code_request"], 16 * 1024)

        for name in ("shortread", "hole", "permissionswitch"):
            with self.subTest(name=name):
                manifest = result[name]["manifest"]
                self.assertEqual(manifest["status"], "scan_complete")
                self.assertEqual(manifest["scan"]["pattern_matches"]["history_signature"], 0)
                self.assertGreater(manifest["scan"]["skipped_bytes"], 0)
        self.assertGreater(result["shortread"]["manifest"]["scan"]["read_failures"], 0)
        self.assertGreater(result["permissionswitch"]["manifest"]["scan"]["read_failures"], 0)

        for name in ("nonimage", "nonexec", "guard", "zero"):
            with self.subTest(name=name):
                manifest = result[name]["manifest"]
                self.assertEqual(manifest["status"], "scan_complete")
                self.assertEqual(manifest["scan"]["scanned_bytes"], 0)
                self.assertGreater(manifest["scan"]["skipped_regions"], 0)

        dense = result["dense"]["manifest"]
        self.assertLessEqual(len(dense["candidates"]), 128)
        self.assertGreater(dense["scan"]["truncation"]["total"], 0)
        self.assertEqual(dense["scan"]["pattern_matches"]["imm_le32_c418"], 32)
        self.assertGreater(dense["scan"]["truncation"]["patterns"]["imm_le32_9590"], 0)
        self.assertLessEqual(dense["scan"]["candidate_bytes"], 128 * 1024)
        self.assertEqual(result["dense"]["pattern_slots"], 4)
        known_rvas = {0x1097560, 0x186025D, 0x1097A7C, 0xBEB103, 0x143BF90, 0x143C950}
        self.assertEqual(
            {item["rva"] for item in cross["known_signatures"]},
            known_rvas,
        )

        mismatch = result["hashmismatch"]
        self.assertEqual(mismatch["manifest"]["status"], "hash_mismatch")
        self.assertEqual(mismatch["read_calls"], 0)
        self.assertEqual(mismatch["manifest"]["candidates"], [])
        self.assertEqual(mismatch["manifest"]["known_signatures"], [])
        wrong_size = result["sizemismatch"]
        self.assertEqual(wrong_size["manifest"]["status"], "disk_size_mismatch")
        self.assertEqual(wrong_size["read_calls"], 0)

        outgoing = result["outgoing"]
        success = outgoing["success"]
        success_manifest = success["manifest"]
        self.assertEqual(success_manifest["mode"], "outgoing_send_code_probe")
        self.assertEqual(success_manifest["status"], "outgoing_probe_complete")
        self.assertEqual(success_manifest["function_verification"], "unverified")
        self.assertEqual(success_manifest["read_limits"], {
            "max_memory_read_bytes_per_step": 4096,
            "max_memory_read_bytes_per_call": 1024,
            "disk_hash_bytes_excluded_from_memory_budget": True,
        })
        expected_windows = [
            (0x1097500, 0x3000),
            (0x185F000, 0x2000),
            (0xBEAF00, 0x1800),
            (0xBDE300, 0x1000),
            (0x1327F00, 0x2000),
            (0x174FA00, 0x800),
            (0x20BBA00, 0x1800),
            (0x18F2D00, 0x1800),
        ]
        self.assertEqual(
            [(item["rva"], item["size"]) for item in success_manifest["windows"]],
            expected_windows,
        )
        for index, window in enumerate(success_manifest["windows"]):
            with self.subTest(window=index):
                self.assertEqual(set(window), {"rva", "size", "status", "hex"})
                self.assertEqual(window["status"], "complete")
                self.assertEqual(window["hex"], success["expected_hex"][index])
                self.assertEqual(len(window["hex"]), window["size"] * 2)
        self.assertLessEqual(success["max_step_read_bytes"], 4096)
        self.assertLessEqual(success["max_step_read_calls"], 4)
        self.assertLessEqual(success["max_read_request"], 1024)
        self.assertFalse(success["outside_query"])
        self.assertFalse(success["outside_read"])
        self.assertEqual(sum(size for _, size in expected_windows), 53248)
        self.assertEqual(success["total_read_bytes"], 4096 + 53248)
        self.assertEqual(
            [(item["rva"], item["size"], item["executable"]) for item in success["read_ranges"][:4]],
            [(offset, 1024, False) for offset in (0, 1024, 2048, 3072)],
        )
        self.assertEqual(success["read_ranges"][4]["rva"], expected_windows[0][0])

        outgoing_hash = outgoing["hashmismatch"]
        self.assertEqual(outgoing_hash["manifest"]["status"], "hash_mismatch")
        self.assertEqual(outgoing_hash["read_calls"], 0)
        self.assertTrue(all(window["status"] == "not_attempted"
                            for window in outgoing_hash["manifest"]["windows"]))
        outgoing_pe = outgoing["pemismatch"]
        self.assertEqual(outgoing_pe["manifest"]["status"], "pe_mismatch")
        self.assertEqual(outgoing_pe["read_calls"], 4)
        self.assertTrue(all(window["status"] == "not_attempted"
                            for window in outgoing_pe["manifest"]["windows"]))

        region_failure = outgoing["regionfailure"]["manifest"]
        self.assertEqual(region_failure["status"], "outgoing_probe_failed")
        self.assertEqual([window["status"] for window in region_failure["windows"]],
                         ["complete", "failed", "not_attempted", "not_attempted",
                          "not_attempted", "not_attempted", "not_attempted",
                          "not_attempted"])
        self.assertNotEqual(region_failure["windows"][1]["status"], "complete")
        gap_failure = outgoing["gapfailure"]["manifest"]
        self.assertEqual(gap_failure["status"], "outgoing_probe_failed")
        self.assertEqual([window["status"] for window in gap_failure["windows"]],
                         ["failed", "not_attempted", "not_attempted", "not_attempted",
                          "not_attempted", "not_attempted", "not_attempted",
                          "not_attempted"])
        partial = outgoing["shortread"]["manifest"]
        self.assertEqual(partial["status"], "outgoing_probe_partial")
        self.assertEqual([window["status"] for window in partial["windows"]],
                         ["partial", "not_attempted", "not_attempted", "not_attempted",
                          "not_attempted", "not_attempted", "not_attempted",
                          "not_attempted"])
        self.assertEqual(len(partial["windows"][0]["hex"]), 4096 * 2)

        entry_path = ROOT / "game" / "chat_probe.lua"
        core_path = ROOT / "game" / "chat_probe_core.lua"
        lifecycle_path = ROOT / "game" / "chat_outgoing_probe.lua"
        normal_entry = build_package.entry_source(entry_path.read_bytes(), core_path.read_bytes())
        diagnostic_entry = build_outgoing_probe._diagnostic_entry()
        lifecycle_source = lifecycle_path.read_bytes()
        outgoing_flag = build_outgoing_probe.OUTGOING_PROBE_FLAG
        self.assertEqual(normal_entry.count(outgoing_flag), 1)
        self.assertIn(outgoing_flag, normal_entry)
        self.assertIn(outgoing_flag.replace(b"= false", b"= true"), diagnostic_entry)
        self.assertNotIn(b"chat_outgoing_lifecycle_probe", normal_entry)
        self.assertIn(b"chat_outgoing_lifecycle_probe", diagnostic_entry)
        self.assertTrue(
            lifecycle_source.rstrip().replace(b"\r\n", b"\n")
            in diagnostic_entry.replace(b"\r\n", b"\n"),
            "诊断入口应完整嵌入生命周期模块，允许统一行尾格式",
        )
        self.assertIn(b"state.done == true and state.status == \"outgoing_probe_complete\"",
                      diagnostic_entry)
        self.assertIn(b"observer_read_budget = 0", diagnostic_entry)
        for offset in (b"0x346D538", b"0x14498", b"0x1E0F", b"0x16D4",
                       b"0x139B8", b"0x13990", b"0x139C0", b"0x1398"):
            self.assertIn(offset, diagnostic_entry)
        self.assertLess(diagnostic_entry.index(b"local root_first ="),
                        diagnostic_entry.index(b"local root_last ="))
        self.assertIn(b"if root_last ~= root_first then", diagnostic_entry)
        self.assertLess(
            diagnostic_entry.rfind(b"target_language_settings.wrap_update"),
            diagnostic_entry.rfind(b"outgoing_lifecycle.wrap_update"),
        )
        self.assertIn(b"addon_pre_update", diagnostic_entry)
        self.assertIn(b"addon_post_update", diagnostic_entry)
        self.assertIn(b"startup_report_retention.run, ffi, kernel, OBSERVE_ENABLED, TRANSLATE_ENABLED",
                      normal_entry)
        self.assertIn(b"startup_report_retention.run, ffi, kernel, true, TRANSLATE_ENABLED",
                      diagnostic_entry)
        for disabled_flag in build_outgoing_probe.DISABLED_FLAGS:
            self.assertIn(disabled_flag, diagnostic_entry)
            self.assertNotIn(disabled_flag.replace(b"= false", b"= true"), diagnostic_entry)
        normal_literal = json.dumps(normal_entry.decode("utf-8"), ensure_ascii=False)
        self.assertEqual(self.lua.run(
            "local chunk, compile_error = loadstring(" + normal_literal + ")\n"
            "assert(chunk, compile_error)\nRESULT = 'normal compiled without execution'\n"
        ), "normal compiled without execution")
        lua_literal = json.dumps(diagnostic_entry.decode("utf-8"), ensure_ascii=False)
        self.assertEqual(self.lua.run(
            "local chunk, compile_error = loadstring(" + lua_literal + ")\n"
            "assert(chunk, compile_error)\nRESULT = 'compiled without execution'\n"
        ), "compiled without execution")

        lifecycle_script = LUA_OUTGOING_LIFECYCLE_HARNESS.replace(
            "CORE_PATH", self.core_path
        ).replace("PROBE_PATH", lifecycle_path.as_posix())
        lifecycle_output = self.lua.run(lifecycle_script)
        self.assertNotIn("PRIVATE_BODY_SENTINEL", lifecycle_output)
        lifecycle = json.loads(lifecycle_output)
        self.assertEqual(lifecycle["gate"], {
            "reads": 0, "prepares": 0, "writes": 0, "adapter_calls": 1,
        })
        pre_post = lifecycle["pre_post"]
        self.assertEqual(pre_post["status"], "running")
        self.assertEqual(pre_post["counters"]["frames"], 1)
        self.assertEqual(pre_post["counters"]["pre_samples"], 1)
        self.assertEqual(pre_post["counters"]["post_samples"], 1)
        self.assertEqual(pre_post["counters"]["pre_flag_count"], 1)
        self.assertEqual(pre_post["counters"]["post_flag_count"], 0)
        self.assertEqual(pre_post["counters"]["body_cleared_pairs"], 1)
        self.assertEqual(lifecycle["idle"]["events"], 2)
        self.assertEqual(lifecycle["idle"]["writes"], 1)
        self.assertEqual(lifecycle["changes"]["counters"]["body_cleared_pairs"], 1)
        self.assertEqual(lifecycle["generation_mismatch_clear"], 0)
        self.assertEqual(lifecycle["failed_clear"], 0)
        self.assertEqual(lifecycle["root_change"]["counters"]["root_changed"], 1)
        self.assertEqual(lifecycle["root_change"]["events"][0]["generation"], 1)
        self.assertEqual(lifecycle["failed_read"]["counters"]["read_failures"], 3)
        self.assertEqual(lifecycle["failed_read"]["events"][0]["body_length"], -1)
        self.assertEqual(lifecycle["missing_terminator"]["events"][0]["body_length"], -2)
        self.assertLessEqual(pre_post["counters"]["max_read_bytes_per_phase"], 4096)
        self.assertEqual(lifecycle["cap"]["before"], 512)
        self.assertGreater(lifecycle["cap"]["dropped_before"], 0)
        self.assertLessEqual(lifecycle["cap"]["events"], 512)
        self.assertGreater(lifecycle["cap"]["dropped"], 0)
        self.assertLessEqual(lifecycle["cap"]["bytes"], 64 * 1024)
        self.assertEqual(lifecycle["cap"]["writes"], 1)
        self.assertEqual(lifecycle["cap"]["last_frame"], 300)
        self.assertGreater(lifecycle["cap"]["encodes"], 1)
        self.assertLessEqual(lifecycle["cap"]["encodes"], 10)
        self.assertEqual(lifecycle["deadline"], {
            "status": "complete",
            "reason": "deadline_reached",
            "writes": 1,
            "report_status": "complete",
        })
        self.assertEqual(lifecycle["write_failure"]["status"], "stopped")
        self.assertEqual(lifecycle["write_failure"]["reason"], "report_write_failed")
        self.assertNotIn("secret report path", lifecycle["write_failure"]["report_json"])
        self.assertEqual(lifecycle["wrapped"]["status"], "stopped")
        self.assertEqual(lifecycle["wrapped"]["reason"], "sample_failed")
        self.assertIn("sample_failed", lifecycle["wrapped"]["report_json"])
        self.assertEqual(lifecycle["wrapped"]["writes"], 1)
        self.assertEqual(lifecycle["wrapped"]["written_report_reason"], "sample_failed")
        self.assertEqual(lifecycle["wrapped"]["original_calls"], 1)
        self.assertEqual(lifecycle["wrapped"]["arg_count"], 3)
        self.assertTrue(lifecycle["wrapped"]["arg_two_is_nil"])
        self.assertEqual(lifecycle["wrapped"]["arg_three"], "third")
        self.assertEqual(lifecycle["wrapped"]["result_count"], 3)
        self.assertEqual(lifecycle["wrapped"]["result_one"], "left")
        self.assertTrue(lifecycle["wrapped"]["result_two_is_nil"])
        self.assertEqual(lifecycle["wrapped"]["result_three"], "right")
        self.assertNotIn("private adapter error", lifecycle["wrapped"]["report_json"])
        completed_wrapper = lifecycle["completed_wrapper"]
        self.assertEqual(completed_wrapper["status"], "complete")
        self.assertEqual(completed_wrapper["writes"], 1)
        self.assertEqual(completed_wrapper["adapter_calls_after"],
                         completed_wrapper["adapter_calls_at_done"])
        self.assertEqual(completed_wrapper["original_calls"], 3)
        self.assertEqual(completed_wrapper["deadline_count"], 2)
        self.assertEqual(completed_wrapper["deadline_value"], "complete")
        self.assertTrue(completed_wrapper["deadline_nil"])
        self.assertEqual(completed_wrapper["after_count"], 2)
        self.assertEqual(completed_wrapper["after_value"], "complete")
        self.assertTrue(completed_wrapper["after_nil"])
        self.assertFalse(lifecycle["original_error"]["ok"])
        self.assertTrue(lifecycle["original_error"]["message"].endswith(
            "original update failure"))

        archive_name = "Addon/" + build_package.ARCHIVE_NAME
        member_names = (
            "manifest.json",
            archive_name,
            archive_name + ".stream",
            archive_name + ".gpu_resources",
            "LICENSE",
            "LICENSES/cJSON-LICENSE.txt",
            "LICENSES/BingusSharedLoader-README.txt",
            "LICENSES/BingusSharedLoader-manifest.json",
            "LICENSES/BingusSharedLoader-SOURCE.txt",
            "LICENSES/ModOptionsMenu-LICENSE.txt",
            "LICENSES/ModOptionsMenu-SOURCE.txt",
        )
        base_manifest = {
            "Version": 1,
            "Guid": build_package.ADDON_GUID,
            "Name": "existing",
            "Description": "existing",
            "Options": [{"Name": "existing", "Description": "existing", "Include": ["Addon"]}],
        }
        captured_entry = {}

        def fake_addon_files(entry, *, loader_zip=None, menu_zip=None):
            captured_entry["bytes"] = entry
            captured_entry["loader_zip"] = loader_zip
            captured_entry["menu_zip"] = menu_zip
            return {
                name: (json.dumps(base_manifest).encode("utf-8") if name == "manifest.json" else b"test")
                for name in member_names
            }

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            package_path = temporary_root / "diagnostic.zip"
            loader_path = temporary_root / "loader.zip"
            menu_path = temporary_root / "menu.zip"
            with mock.patch.object(build_outgoing_probe.builder, "addon_files", side_effect=fake_addon_files):
                built_path = build_outgoing_probe.build_artifact(
                    package_path,
                    loader_zip=loader_path,
                    menu_zip=menu_path,
                )
            self.assertEqual(built_path, package_path)
            self.assertEqual(captured_entry["loader_zip"], loader_path)
            self.assertEqual(captured_entry["menu_zip"], menu_path)
            with zipfile.ZipFile(package_path) as package:
                self.assertEqual(set(package.namelist()), set(member_names))
                package_manifest = json.loads(package.read("manifest.json"))
                self.assertEqual(package_manifest["Guid"], build_package.ADDON_GUID)
                self.assertEqual(package_manifest["Name"], "HD2 Chat Outgoing Code Probe")
                self.assertIn("暂时替换同 GUID", package_manifest["Description"])
                self.assertIn(outgoing_flag.replace(b"= false", b"= true"), captured_entry["bytes"])




if __name__ == "__main__":
    unittest.main()
