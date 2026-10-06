"""聊天多行布局 factory 的私有 LuaJIT fake-kernel 回归。"""

from __future__ import annotations

from pathlib import Path
import re
import struct
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT


HELPER_SIGNATURE_HEX = (
    "40534883ec20488bd94881c110010000e8cbd0beff0f57c0f30f11442430f30f108330010000"
    "f30f598320010000e8c96b8a00f30f11442434488bcb488b5424304883c4205be95560beff"
)
POSITION_SIGNATURE_HEX = (
    "488954241053564883ec38488bda488bf14584c07513e8e568beff48899ecc0300004883c4385e5bc3"
)


LAYOUT_FAKE_KERNEL = r'''
local ffi = require("ffi")
ffi.cdef[[typedef unsigned long long HD2Probe_U64;]]
ffi.cdef[[typedef unsigned char HD2Probe_U8;]]

local memory = {}
local read_counts = {}
local query_calls = 0
local region = {
    base = 0x100000, finish = 0x400000, allocation_base = 0x100000,
    state = 0x1000, protect = 0x04, type = 0x20000,
}
local read_failure, read_failure_address, read_mutation, pre_setter_mutation, split_address, split_allocation
local split_protect, mutate_after_measure
local context_stable = true
local verify_apply = true
local measured_height
local throw_measure, throw_position, throw_setter, setter_returns_false
local mutate_after_setter, mutate_after_position
local current_message
local observer_read_budget = 0
local MAX_OBSERVER_READ = 16 * 1024
local TRANSLATE_ENABLED = true
local observer_display_native_gate = true
local translate_heartbeat_fresh = true
local TRANSLATE_MAX_REQUEST_BYTES = 1023
local TRANSLATE_MAX_RESPONSE_BYTES = 16387
local translate_core = {
    MAX_DISPLAY_BYTES = 17417,
    valid_text = function(text, maximum)
        return type(text) == "string" and #text > 0 and #text <= maximum
            and not text:find("\0", 1, true)
    end,
}

local function pack32(value)
    return string.char(value % 256, math.floor(value / 256) % 256,
        math.floor(value / 65536) % 256, math.floor(value / 16777216) % 256)
end

local function pack64(value)
    local item = ffi.new("HD2Probe_U64[1]")
    item[0] = value
    return ffi.string(item, 8)
end

local function float_bytes(value)
    local item = ffi.new("float[1]")
    item[0] = value
    return ffi.string(item, 4)
end

local function write_bytes(address, bytes)
    for index = 1, #bytes do memory[address + index - 1] = bytes:byte(index) end
end

local function read_bytes(address, length)
    if read_failure and (read_failure_address == nil or address == read_failure_address) then
        return nil, read_failure
    end
    if length > MAX_OBSERVER_READ - observer_read_budget then return nil, "budget_exhausted" end
    observer_read_budget = observer_read_budget + length
    read_counts[address] = (read_counts[address] or 0) + 1
    if read_mutation then read_mutation(address, read_counts[address]) end
    local output = {}
    for index = 0, length - 1 do
        local byte = memory[address + index]
        if byte == nil then return nil, "read_failed" end
        output[#output + 1] = string.char(byte)
    end
    return table.concat(output)
end

local function query(address)
    query_calls = query_calls + 1
    if address < region.base or address >= region.finish then return nil end
    if split_address and address >= split_address then
        return {
            base = split_address, finish = region.finish,
            allocation_base = split_allocation or region.allocation_base,
            state = region.state, protect = split_protect or region.protect, type = region.type,
        }
    end
    return {
        base = region.base, finish = split_address or region.finish,
        allocation_base = region.allocation_base, state = region.state,
        protect = region.protect, type = region.type,
    }
end

local function same_region(left, right)
    return left and right and left.base == right.base and left.finish == right.finish
        and left.allocation_base == right.allocation_base and left.state == right.state
        and left.protect == right.protect and left.type == right.type
end

local root, manager, context, target_slot, target_row, original_head, original_count
local position_calls, measure_calls, setter_calls, setter_text, positioned = 0, 0, 0, nil, {}
local function reset_case(config)
    config = config or {}
    memory, read_counts, positioned = {}, {}, {}
    query_calls = 0
    region.protect = config.protect or 0x04
    region.state = 0x1000
    region.type = config.region_type or 0x20000
    region.allocation_base = 0x100000
    read_failure, read_failure_address, read_mutation = nil, nil, nil
    pre_setter_mutation, split_address, split_allocation = nil, nil, nil
    split_protect = nil
    mutate_after_measure = config.mutate_after_measure
    context_stable = config.context_stable ~= false
    verify_apply = config.verify_apply ~= false
    measured_height = config.measured_height
    throw_measure, throw_position = config.throw_measure, config.throw_position
    throw_setter, setter_returns_false = config.throw_setter, config.setter_returns_false
    mutate_after_setter, mutate_after_position = config.mutate_after_setter, config.mutate_after_position
    position_calls, measure_calls, setter_calls, setter_text = 0, 0, 0, nil
    observer_read_budget = config.entry_budget or 0

    root = 0x120000
    manager = root + 0x14498
    original_head = config.head or 0
    original_count = config.count or 3
    local history = pack32(original_head) .. string.rep("\0", 0x30 - 4) .. pack32(original_count)
    write_bytes(manager + 0x13990, history)

    local scales = config.scales or {1, 2, 0.5}
    local heights = config.heights or {20, 30, 70}
    local slots = {}
    for index = 0, original_count - 1 do
        local slot = (original_head - index - 1) % 64
        slots[index + 1] = slot
        local row = manager + 0x4390 + slot * 0x3D8
        write_bytes(row + 0x10, float_bytes(heights[index + 1] or 20)
            .. string.rep("\0", 0x20 - 0x10 - 4) .. float_bytes(scales[index + 1] or 1))
    end

    local target_index = config.target_index or 0
    target_slot = slots[target_index + 1]
    target_row = manager + 0x4390 + target_slot * 0x3D8
    if config.split_allocation then
        split_address = target_row + 0x200
        split_allocation = 0x200000
    end
    context = {
        root = root, root_global = root + 0x80, ring = root + 0x1000,
        metadata_address = root + 0x200, metadata = pack32(1) .. pack32(3),
        next_index = 1, active_count = 3,
    }
    write_bytes(context.root_global, pack64(root))
    write_bytes(context.metadata_address, context.metadata)
    local event_address = context.ring + 4 * 0x4B4
    local body_address = event_address + 0xB4
    local body_bytes = "source message\0" .. string.rep("\0", 1024 - #"source message" - 1)
    write_bytes(event_address, pack32(0x1C12037F))
    write_bytes(body_address, body_bytes)
    local map_address = target_row + 0x220
    local entries_address = map_address + 8
    local entries = {}
    for index = 0, 13 do
        if index == 2 then
            entries[#entries + 1] = pack32(0x7518C954) .. pack32(1)
                .. pack64(body_address) .. pack64(0)
        else
            entries[#entries + 1] = pack32(0x12340000 + index) .. pack32(0)
                .. pack64(0) .. pack64(0)
        end
    end
    local entries_bytes = table.concat(entries)
    write_bytes(map_address + 0x158, string.char(14))
    write_bytes(entries_address, entries_bytes)
    current_message = {
        widget_slot = target_slot, event_slot = 4, owner_id = 77, body = "source message",
        proof = {
            context = context, widget = target_row, map = map_address,
            count_address = map_address + 0x158, count_bytes = string.char(14),
            entries_address = entries_address, entries_bytes = entries_bytes,
            key_index = 2, event_slot = 4,
            event_address = event_address, event_bytes = pack32(0x1C12037F),
            body_address = body_address, body_bytes = body_bytes,
            body = "source message",
        },
    }

    if config.entry_budget then observer_read_budget = config.entry_budget end

    translate_layout.instance = translate_layout.new(ffi, {
        verified = config.verified ~= false,
        gap = config.gap == nil and 5 or config.gap,
        read = read_bytes,
        query = query,
        same_region = same_region,
        region_allowed = function(item)
            if item.state ~= 0x1000 then return false end
            if item.protect ~= 0x02 and item.protect ~= 0x04 and item.protect ~= 0x08 then return false end
            return item.type == 0x20000
                or (item.type == 0x1000000 and item.allocation_base == 0x999000)
        end,
        verify_context = function(value)
            if not context_stable then return false, "unstable" end
            local root_bytes, root_reason = read_bytes(value.root_global, 8)
            local metadata_bytes, metadata_reason = read_bytes(value.metadata_address, 8)
            if not root_bytes or not metadata_bytes then
                return false, root_reason or metadata_reason or "read_failed"
            end
            if root_bytes ~= pack64(value.root) or metadata_bytes ~= value.metadata then
                return false, "unstable"
            end
            return true
        end,
        manager_for_root = function(value) return value + 0x14498 end,
        measure = function(row)
            measure_calls = measure_calls + 1
            if throw_measure then error("private measure spy failure") end
            if measured_height ~= nil then write_bytes(row + 0x10, float_bytes(measured_height)) end
            if mutate_after_measure then mutate_after_measure() end
        end,
        position = function(row, packed, flag)
            assert(flag == 0, "layout must use the verified flag=0 wrapper path")
            position_calls = position_calls + 1
            if throw_position and position_calls == throw_position then error("private position spy failure") end
            local value = ffi.new("HD2Probe_U64[1]")
            value[0] = packed
            local bytes = ffi.string(value, 8)
            write_bytes(row + 0x3CC, bytes)
            local vector = ffi.new("float[2]")
            ffi.copy(vector, bytes, 8)
            positioned[#positioned + 1] = {
                row = row, x = tonumber(vector[0]), y = tonumber(vector[1]), bytes = bytes,
            }
            if mutate_after_position and position_calls == 1 then mutate_after_position() end
        end,
    })
end

local observer_read = read_bytes
local function observer_read_pointer(address)
    local bytes, reason = observer_read(address, 8)
    if not bytes then return nil, reason end
    local value = ffi.new("HD2Probe_U64[1]")
    ffi.copy(value, bytes, 8)
    return tonumber(value[0])
end
local function observer_u32(bytes, offset)
    local a, b, c, d = bytes:byte(offset + 1, offset + 4)
    if not d then return nil end
    return a + b * 256 + c * 65536 + d * 16777216
end

__VERIFY_VALUES__

local observer_widget_read_widget_slot = function(slot, _)
    if slot ~= current_message.widget_slot then return "empty" end
    -- 模拟完整14项属性表的正文、event、root及map双读最大输入。
    observer_read_budget = observer_read_budget + 2762
    return "ok", current_message
end
local observer_translate_heartbeat = function()
    return translate_heartbeat_fresh
end
local observer_translate_pin_text = function(text) return text end
local observer_add = function(address, offset) return address + offset end
local observer_translate_refresh_for_setter = function()
    if pre_setter_mutation then pre_setter_mutation() end
    return true
end
local observer_translate_setter = function(widget_argument, buffer)
    setter_calls = setter_calls + 1
    assert(widget_argument == target_row + 0x110)
    setter_text = buffer
    if throw_setter then error("private setter spy failure") end
    if setter_returns_false then return false end
    return true
end
local observer_translate_verify_apply = function()
    observer_read_budget = observer_read_budget + 2762
    if mutate_after_setter then mutate_after_setter() end
    return verify_apply
end

__APPLY_FUNCTION__

local function near(actual, expected)
    assert(math.abs(actual - expected) < 0.001,
        string.format("expected %.3f, got %.3f", expected, actual))
end

local function apply_text()
    return observer_translate_apply(current_message, current_message.body .. "\n译文：localized")
end

__REPORT_SANITIZER__

-- 最新行增高后，所有更旧行按各自scale与保留的extent让出空间；head/count不动。
reset_case({head = 0, target_index = 0, scales = {1, 2, 0.5}, heights = {20, 30, 70},
    measured_height = 50, gap = 5})
local result = apply_text()
assert(result == "called_confirmed" and setter_calls == 1 and measure_calls == 1)
assert(setter_text == "source message\n译文：localized")
assert(position_calls == 3 and #positioned == 3)
near(positioned[1].y, 0)
near(positioned[2].y, 55)
near(positioned[3].y, 120)
assert(positioned[1].row == manager + 0x4390 + 63 * 0x3D8)
assert(positioned[3].row == manager + 0x4390 + 61 * 0x3D8)
assert(read_bytes(manager + 0x13990, 4) == pack32(original_head))
assert(read_bytes(manager + 0x139C0, 4) == pack32(original_count))
local oldest_after = read_bytes(positioned[3].row + 0x10, 4)
near((function() local value = ffi.new("float[1]"); ffi.copy(value, oldest_after, 4); return tonumber(value[0]) end)(), 70)

-- 中间行增高时只测它自身，整个历史仍重排，较新的位置与较旧译文extent保持。
reset_case({head = 1, target_index = 1, scales = {1, 0.5, 1.5}, heights = {25, 30, 60},
    measured_height = 90, gap = 4})
result = apply_text()
assert(result == "called_confirmed" and setter_calls == 1 and measure_calls == 1)
assert(position_calls == 3 and positioned[1].row == manager + 0x4390)
assert(positioned[2].row == manager + 0x4390 + 63 * 0x3D8)
assert(positioned[3].row == manager + 0x4390 + 62 * 0x3D8)
near(positioned[1].y, 0)
near(positioned[2].y, 29)
near(positioned[3].y, 78)
near((function() local value = ffi.new("float[1]"); ffi.copy(value, read_bytes(positioned[1].row + 0x10, 4), 4); return tonumber(value[0]) end)(), 25)
near((function() local value = ffi.new("float[1]"); ffi.copy(value, read_bytes(positioned[3].row + 0x10, 4), 4); return tonumber(value[0]) end)(), 60)
assert(read_bytes(manager + 0x13990, 4) == pack32(original_head))
assert(read_bytes(manager + 0x139C0, 4) == pack32(original_count))

-- 官方字段映射下，row+0x10的height可为30/90；row+0x20的scale为1。
reset_case({scales = {1, 1, 1}, heights = {30, 90, 25}, measured_height = 50})
assert(apply_text() == "called_confirmed")
assert(setter_calls == 1 and measure_calls == 1 and position_calls == 3)

-- stale root/ring、head/count、行几何、只读页、无效浮点/count及预算不足都不回写。
local function expect_preflight_failure(config, expected, mutate)
    reset_case(config)
    if mutate then mutate() end
    local status = apply_text()
    assert(status == expected, "expected " .. expected .. ", got " .. tostring(status))
    assert(setter_calls == 0 and measure_calls == 0 and position_calls == 0)
end

local function expect_failure_diagnostic(config, expected_status, expected_code, mutate)
    reset_case(config)
    if mutate then mutate() end
    local status = apply_text()
    assert(status == expected_status, "unexpected diagnostic status: " .. tostring(status))
    assert(setter_calls == 0 and measure_calls == 0 and position_calls == 0)
    local stats = translate_layout.instance.stats()
    assert(stats.last_failure_code == expected_code,
        "expected layout failure code " .. expected_code .. ", got " .. tostring(stats.last_failure_code))
    return stats
end

expect_preflight_failure({context_stable = false}, "stale")
local history_drift = expect_failure_diagnostic({head = 0}, "stale", 9, function()
    read_mutation = function(address, count)
        if address == manager + 0x13990 and count == 2 then write_bytes(address, pack32(1)) end
    end
end)
assert(history_drift.last_history_head == 1 and history_drift.last_history_count == 3)
expect_failure_diagnostic({head = 0}, "stale", 10, function()
    local row = target_row
    read_mutation = function(address, count)
        if address == row + 0x10 and count == 2 then write_bytes(row + 0x10, float_bytes(33)) end
    end
end)
expect_preflight_failure({protect = 0x02}, "read_failed")
expect_preflight_failure({scales = {0/0, 1, 1}}, "read_failed")
expect_preflight_failure({heights = {0/0, 1, 1}}, "read_failed")
expect_preflight_failure({heights = {math.huge, 1, 1}}, "read_failed")
expect_preflight_failure({count = 65}, "read_failed")
expect_preflight_failure({region_type = 0x40000}, "read_failed")
expect_preflight_failure({split_allocation = true}, "read_failed")
-- 位置字段所在的尾部区域只读或归属不同allocation，整行校验必须完整拒绝。
expect_preflight_failure({}, "read_failed", function()
    split_address = target_row + 0x3CC
    split_protect = 0x02
end)
expect_preflight_failure({}, "read_failed", function()
    split_address = target_row + 0x3CC
    split_allocation = 0x200000
end)
expect_preflight_failure({}, "deferred", function()
    read_failure = "budget_exhausted"
    read_failure_address = manager + 0x13990
end)
expect_preflight_failure({entry_budget = 2049}, "deferred")

local history_read_failure = expect_failure_diagnostic({}, "read_failed", 2, function()
    read_failure = "read_failed"
    read_failure_address = manager + 0x13990
end)
assert(history_read_failure.last_history_head == 0 and history_read_failure.last_history_count == 0)
local history_range_failure = expect_failure_diagnostic({count = 65}, "read_failed", 3)
assert(history_range_failure.last_history_count == 65)
expect_failure_diagnostic({protect = 0x02}, "read_failed", 4)
expect_failure_diagnostic({}, "read_failed", 5, function()
    read_failure = "private geometry read detail"
    read_failure_address = target_row + 0x10
end)

-- scale=30必须拒绝，且失败报告只记录有限、无地址的量化值与分类。
local bad_scale = expect_failure_diagnostic({heights = {1, 30, 90}, scales = {30, 1, 1}},
    "read_failed", 6)
assert(bad_scale.last_geometry_slot == 63)
assert(bad_scale.last_scale_milli == 30000 and bad_scale.last_height_milli == 1000)
assert(bad_scale.last_scale_class == 5 and bad_scale.last_height_class == 4)
local diagnostic_report = observer_translate_sanitize_report({status = "ready", counters = {}})
assert(diagnostic_report.layout.last_failure_code == 6)
assert(diagnostic_report.layout.last_geometry_slot == 63)
assert(diagnostic_report.layout.last_scale_milli == 30000)
assert(diagnostic_report.layout.last_height_class == 4)
local bad_height = expect_failure_diagnostic({heights = {-2, 30, 90}, scales = {1, 1, 1}},
    "read_failed", 7)
assert(bad_height.last_height_milli == 0 and bad_height.last_height_class == 2)
assert(bad_height.last_scale_milli == 1000 and bad_height.last_scale_class == 4)
local nan_scale = expect_failure_diagnostic({scales = {0/0, 1, 1}}, "read_failed", 6)
assert(nan_scale.last_scale_milli == 0 and nan_scale.last_scale_class == 1)

local function expect_heartbeat_failure(mutate, expected)
    reset_case({measured_height = 40})
    pre_setter_mutation = mutate
    local status = apply_text()
    assert(status == expected, "unexpected heartbeat-race status: " .. tostring(status))
    assert(setter_calls == 0 and measure_calls == 0 and position_calls == 0)
end

-- heartbeat和pin期间若root/ring、history、几何、保护页或allocation改变，setter前必须拒绝。
expect_heartbeat_failure(function()
    write_bytes(context.root_global, pack64(root + 0x10000))
end, "stale")
expect_heartbeat_failure(function()
    write_bytes(context.metadata_address, pack32(2) .. pack32(3))
end, "stale")
expect_heartbeat_failure(function()
    write_bytes(manager + 0x13990, pack32(1))
end, "stale")
expect_heartbeat_failure(function()
    write_bytes(target_row + 0x10, float_bytes(44))
end, "stale")
expect_heartbeat_failure(function() region.protect = 0x02 end, "read_failed")
expect_heartbeat_failure(function() region.allocation_base = 0x200000 end, "read_failed")

-- 正文、event或map在heartbeat I/O期间变化时，生产verify_values二读阻止setter。
expect_heartbeat_failure(function()
    write_bytes(current_message.proof.event_address, pack32(0x12345678))
end, "stale")
expect_heartbeat_failure(function()
    write_bytes(current_message.proof.body_address, "changed message")
end, "stale")
expect_heartbeat_failure(function()
    write_bytes(current_message.proof.entries_address + 2 * 0x18 + 20, pack32(0xDEADBEEF))
end, "stale")

-- 完整64槽、最大14项map按fake-kernel实际读长计费；2048B旧预算加本次apply仍低于16KiB。
reset_case({head = 0, count = 64, target_index = 0, entry_budget = 2048,
    measured_height = 40, gap = 5})
result = apply_text()
local full_history_budget = observer_read_budget
assert(result == "called_confirmed" and setter_calls == 1 and measure_calls == 1)
assert(position_calls == 64 and full_history_budget == 15129)
assert(query_calls <= 1024, "64-row layout exceeded the range-query budget")
assert(full_history_budget <= MAX_OBSERVER_READ and MAX_OBSERVER_READ - full_history_budget == 1255)
assert(read_bytes(manager + 0x13990, 4) == pack32(original_head))
assert(read_bytes(manager + 0x139C0, 4) == pack32(original_count))

-- setter属性未确认时不执行测量/重排，并永久停用后续setter。
reset_case({measured_height = 40, verify_apply = false})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 0 and position_calls == 0)
assert(translate_layout.instance.stats().last_failure_code == 11)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)

-- setter返回失败或抛异常都归为未确认并禁用后续写入。
reset_case({throw_setter = true})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 0 and position_calls == 0)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)
reset_case({setter_returns_false = true})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 0 and position_calls == 0)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)

-- native measure/position spy异常被捕获；已调用setter后不再允许后续回写。
reset_case({measured_height = 40, throw_measure = true})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 1 and position_calls == 0)
local stats_method = translate_layout.instance.stats
translate_layout.instance.stats = function()
    local stats = stats_method()
    stats.exception_text = "PRIVATE_LAYOUT_EXCEPTION"
    return stats
end
local safe_report = observer_translate_sanitize_report({
    status = "ready", code = "invalid_session", body = "PRIVATE_LAYOUT_EXCEPTION",
    exception = "PRIVATE_LAYOUT_EXCEPTION", counters = {private_count = 7},
})
assert(safe_report.layout.last_failure_code == 11)
assert(safe_report.layout.exception_text == nil and safe_report.exception == nil)
local function contains_private_value(value)
    if value == "PRIVATE_LAYOUT_EXCEPTION" then return true end
    if type(value) == "table" then
        for key, child in pairs(value) do
            if key == "PRIVATE_LAYOUT_EXCEPTION" or contains_private_value(child) then return true end
        end
    end
    return false
end
assert(not contains_private_value(safe_report))
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)
reset_case({measured_height = 40, throw_position = 2})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 1 and position_calls == 2)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)

-- setter确认后measure前或每行position前若RW/allocation契约改变，不再进入对应native。
reset_case({mutate_after_setter = function() region.protect = 0x02 end})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 0 and position_calls == 0)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)
reset_case({mutate_after_setter = function() region.allocation_base = 0x200000 end})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 0 and position_calls == 0)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)
-- measure也是原生调用；返回后的整行校验仍必须拒绝allocation变化。
reset_case({mutate_after_measure = function() region.allocation_base = 0x200000 end})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 1 and position_calls == 0)
reset_case({mutate_after_position = function() region.protect = 0x02 end})
assert(apply_text() == "called_unconfirmed")
assert(setter_calls == 1 and measure_calls == 1 and position_calls == 1)
observer_read_budget = 0
assert(apply_text() == "disabled" and setter_calls == 1)

-- 缺失或任一字节不同的函数签名会关闭factory；所有native spy都保持零调用。
reset_case({verified = false})
assert(apply_text() == "disabled")
assert(setter_calls == 0 and measure_calls == 0 and position_calls == 0)

RESULT = "chat layout fake-kernel and native-spy checks ok"
'''


class ChatLayoutTests(unittest.TestCase):
    """生产factory与apply函数在隔离的LuaJIT状态中运行。"""

    @classmethod
    def setUpClass(cls) -> None:
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行布局fake-kernel测试")
        cls.lua = LuaJIT(LUA_DLL)
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        factory_start = source.index("local translate_layout = (function()")
        factory_end = source.index("\n\nlocal function initialize_probe()", factory_start)
        apply_start = source.index("    local function observer_translate_apply(message, text)")
        apply_end = source.index("    local function observer_finish_cycle()", apply_start)
        verify_start = source.index("    local function observer_widget_verify_context(context)")
        verify_end = source.index("    local function observer_widget_read_widget_slot(", verify_start)
        report_start = source.index("    local TRANSLATE_REPORT_STATUSES = {")
        report_end = source.index("\n\n    local function observer_translate_write_report(", report_start)
        cls.factory = source[factory_start:factory_end]
        cls.apply = source[apply_start:apply_end]
        cls.verify_values = source[verify_start:verify_end]
        cls.sanitizer = source[report_start:report_end]

    def test_signature_gate_reads_full_verified_windows_and_reasonable_gap(self) -> None:
        checks = r'''
local calls = {}
local function from_hex(value)
    return (value:gsub("..", function(pair) return string.char(tonumber(pair, 16)) end))
end
local helper = from_hex("__HELPER_SIGNATURE__")
local position = from_hex("__POSITION_SIGNATURE__")
local gap_bytes = from_hex("__GAP_BYTES__")
local valid, gap = translate_layout.verify(ffi, function(rva, length, executable)
    calls[#calls + 1] = {rva = rva, length = length, executable = executable}
    if rva == 0x18610C0 and length == 75 and executable then return helper end
    if rva == 0x1860DA0 and length == 41 and executable then return position end
    if rva == 0x23C7554 and length == 4 and not executable then return gap_bytes end
    return nil
end)
assert(valid and math.abs(gap - 5.5) < 0.001)
assert(#calls == 3 and calls[1].length == 75 and calls[2].length == 41 and calls[3].length == 4)
local changed = helper:sub(1, 74) .. string.char((helper:byte(75) + 1) % 256)
local bad_helper = translate_layout.verify(ffi, function(rva)
    if rva == 0x18610C0 then return changed end
    if rva == 0x1860DA0 then return position end
    return gap_bytes
end)
local missing_position = translate_layout.verify(ffi, function(rva)
    if rva == 0x18610C0 then return helper end
    if rva == 0x23C7554 then return gap_bytes end
    return nil
end)
local bad_gap = translate_layout.verify(ffi, function(rva)
    if rva == 0x18610C0 then return helper end
    if rva == 0x1860DA0 then return position end
    return float_bytes(math.huge)
end)
assert(not bad_helper and not missing_position and not bad_gap)
RESULT = "chat layout signature gate checks ok"
'''
        script = (
            'local ffi = require("ffi")\nffi.cdef[[typedef unsigned long long HD2Probe_U64;]]\n'
            + self.factory
            + '\nlocal function float_bytes(value)\n'
            + '    local item = ffi.new("float[1]"); item[0] = value; return ffi.string(item, 4)\nend\n'
            + checks
        )
        script = script.replace("__HELPER_SIGNATURE__", HELPER_SIGNATURE_HEX)
        script = script.replace("__POSITION_SIGNATURE__", POSITION_SIGNATURE_HEX)
        script = script.replace("__GAP_BYTES__", struct.pack("<f", 5.5).hex())
        self.assertEqual(self.lua.run(script), "chat layout signature gate checks ok")

    def test_reflow_preflight_apply_order_and_fail_closed_spies(self) -> None:
        script = self.factory + "\n" + LAYOUT_FAKE_KERNEL.replace(
            "__VERIFY_VALUES__", self.verify_values
        ).replace("__APPLY_FUNCTION__", self.apply).replace(
            "__REPORT_SANITIZER__",
            "local STANDALONE_ENABLED = false\n" + self.sanitizer,
        )
        self.assertEqual(self.lua.run(script), "chat layout fake-kernel and native-spy checks ok")


if __name__ == "__main__":
    unittest.main()
