"""聊天回写适配器的 LuaJIT fake-kernel mock 回归。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT
from chat_fixtures import LUA_OBSERVER_ADAPTER_HARNESS, WIDGET_ADAPTER_CHECKS


TRANSLATE_ADAPTER_CHECKS = r'''
local files, directories, handles = {}, {}, {}
local create_log, move_log, delete_log = {}, {}, {}
local next_file_handle = 0x700000
local flush_calls = 0
local fs_behavior = {
    fail_read = false, read_limit = nil,
    write_limit = nil, fail_flush = false, fail_close = false, fail_move = false,
}

local function wide_path(path)
    if type(path) == "string" then return path end
    local output = {}
    for index = 0, 32767 do
        local byte = tonumber(path[index])
        if not byte or byte == 0 then break end
        assert(byte <= 0x7f, "fake filesystem received an unexpected non-ASCII path")
        output[#output + 1] = string.char(byte)
    end
    return table.concat(output)
end

local function handle_record(handle)
    return handles[tonumber(ffi.cast("size_t", handle))]
end

kernel.CreateDirectoryW = function(path, _)
    directories[wide_path(path)] = true
    return 1
end
kernel.GetTickCount64 = function() return observer_now end
kernel.CreateFileW = function(path, access, share_mode, _, creation, attributes, _)
    local filename = wide_path(path)
    create_log[#create_log + 1] = {
        path = filename, access = access, share_mode = share_mode,
        creation = creation, attributes = attributes,
    }
    assert(attributes == 0x80, "unexpected mailbox file attributes")
    if access == 0x40000000 and creation == 1 then
        if files[filename] ~= nil then return invalid_handle end
        files[filename] = {data = ""}
    elseif access == 0x80000000 and creation == 3 then
        if files[filename] == nil then return invalid_handle end
    else
        error("unexpected mailbox CreateFileW mode")
    end
    next_file_handle = next_file_handle + 1
    local handle = ffi.cast("HD2Probe_HANDLE", next_file_handle)
    handles[next_file_handle] = {path = filename}
    return handle
end
kernel.ReadFile = function(handle, buffer, length, received, _)
    local record = handle_record(handle)
    if not record then return 0 end
    if fs_behavior.fail_read then return 0 end
    local item = files[record.path]
    if not item then return 0 end
    local count = math.min(#item.data, length)
    if fs_behavior.read_limit ~= nil then count = math.min(count, fs_behavior.read_limit) end
    if count > 0 then ffi.copy(buffer, item.data, count) end
    received[0] = count
    return 1
end
kernel.WriteFile = function(handle, buffer, length, written, _)
    local record = handle_record(handle)
    if not record or not files[record.path] then return 0 end
    local count = math.min(length, fs_behavior.write_limit or length)
    files[record.path].data = ffi.string(buffer, count)
    written[0] = count
    return 1
end
kernel.FlushFileBuffers = function(handle)
    flush_calls = flush_calls + 1
    return handle_record(handle) and not fs_behavior.fail_flush and 1 or 0
end
kernel.CloseHandle = function(handle)
    local record = handle_record(handle)
    if not record then return 0 end
    record.closed = true
    return fs_behavior.fail_close and 0 or 1
end
kernel.DeleteFileW = function(path)
    local filename = wide_path(path)
    delete_log[#delete_log + 1] = filename
    if files[filename] == nil then return 0 end
    files[filename] = nil
    return 1
end
kernel.MoveFileExW = function(old_path, new_path, flags)
    local old_name, new_name = wide_path(old_path), wide_path(new_path)
    move_log[#move_log + 1] = {old = old_name, new = new_name, flags = flags}
    if fs_behavior.fail_move or files[old_name] == nil then return 0 end
    local replace_existing = flags % 2 == 1
    if files[new_name] ~= nil and not replace_existing then return 0 end
    files[new_name] = files[old_name]
    files[old_name] = nil
    return 1
end

local function put_file(path, content)
    files[wide_path(path)] = {data = content}
end

local function list_temporary_files()
    local found = {}
    for filename in pairs(files) do
        if filename:find(".tmp", 1, true) then found[#found + 1] = filename end
    end
    table.sort(found)
    return found
end

local function reset_fs_failures()
    fs_behavior.fail_read = false
    fs_behavior.read_limit = nil
    fs_behavior.write_limit = nil
    fs_behavior.fail_flush = false
    fs_behavior.fail_close = false
    fs_behavior.fail_move = false
end

observer_faulted = false
observer_session_time = 1700000000
observer_session_nonce = 0x1234
local translate_adapter = make_translate_adapter()
local bridge_path = wide_path(translate_heartbeat_path)
local report_path = wide_path(translate_report_path)
local mailbox_path = wide_path(translate_mailbox_directory)
assert(mailbox_path == "C:\\Mock\\LocalAppData\\HD2ChatTranslate\\mailbox")
assert(directories["C:\\Mock\\LocalAppData\\HD2ChatTranslate"])
assert(directories[mailbox_path])

local session_prefix = translate_session_id .. "_"
assert(#translate_session_id <= 80 and translate_session_id:match("^[A-Za-z0-9_%-]+$"))
local token = session_prefix .. "1"

-- 未启用时heartbeat可以读固定文件，但read_slot不触碰普通聊天内存。
observer_now = 1000
put_file(bridge_path, "HD2CT1 1000\n")
assert(translate_adapter.heartbeat() == "HD2CT1 1000\n")
assert(translate_heartbeat_fresh)
TRANSLATE_ENABLED = false
prepare_widget_case()
local reads_before_disabled = read_calls
local disabled_status = translate_adapter.read_slot(0)
assert(disabled_status == "stale" and read_calls == reads_before_disabled)

-- fresh翻译会读取普通正文；窗口失效、无文件或budget不足时不能读取正文。
TRANSLATE_ENABLED = true
observer_display_native_gate = true
observer_now = 1100
put_file(bridge_path, "HD2CT1 1100\n")
translate_adapter.heartbeat()
prepare_widget_case({body = widget_body_bytes("Meet us at extraction")})
local direct_status = observer_widget_read_widget_slot(0, true)
assert(direct_status == "ok", tostring(direct_status))
prepare_widget_case({body = widget_body_bytes("Meet us at extraction")})
local status, message = translate_adapter.read_slot(0)
assert(status == "ok" and message.body == "Meet us at extraction", tostring(status) .. ":" .. tostring(message and message.body))
assert(message.widget_slot == 0 and message.event_slot == 4 and message.owner_id > 0)
assert(type(message.proof) == "table" and type(message.proof.context) == "table")
assert(read_counts[widget_body] == 2, "ordinary body was not double-read")

-- 事件槽直接换算覆盖环首尾；不对指针槽位逐项扫描。
prepare_widget_case({event_slot = 0, next_index = 1, active_count = 1})
status, message = translate_adapter.read_slot(0)
assert(status == "ok" and message.event_slot == 0, "event slot zero was not mapped")
prepare_widget_case({event_slot = 63, next_index = 0, active_count = 64})
status, message = translate_adapter.read_slot(0)
assert(status == "ok" and message.event_slot == 63, "event slot 63 was not mapped")

prepare_widget_case({pointer = widget_record_address(4) + 0xB5})
assert(observer_widget_read_widget_slot(0, true) == "pointer_outside",
    "misaligned event body pointer was accepted")
prepare_widget_case({pointer = 0})
assert(observer_widget_read_widget_slot(0, true) == "pointer_outside",
    "null event body pointer was accepted")
prepare_widget_case({pointer = widget_ring + 64 * 0x4B4 + 0xB4})
assert(observer_widget_read_widget_slot(0, true) == "pointer_outside",
    "one-past-ring event body pointer was accepted")

translate_adapter.test_scan_hint = {history = widget_manager + 0x13990}
translate_adapter.test_scan_hint.write_history = function(head, count, fill)
    write_bytes(translate_adapter.test_scan_hint.history, pack32(head) .. string.rep(fill or "\0", 44) .. pack32(count))
end

-- 初次重同步覆盖全槽；稳态只重试latest两次，其余计划每次轮转两个槽。
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 64)
translate_adapter.test_scan_hint.initial_plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.initial_plan) == "table" and translate_adapter.test_scan_hint.initial_plan.owner_id > 0
    and #translate_adapter.test_scan_hint.initial_plan.slots == 64 and translate_adapter.test_scan_hint.initial_plan.slots[1] == 0,
    "initial history hint did not request a bounded full resync")
translate_adapter.test_scan_hint.owner_before = translate_adapter.test_scan_hint.initial_plan.owner_id
translate_adapter.test_scan_hint.covered_slots = {}
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.plan) == "table" and #translate_adapter.test_scan_hint.plan.slots == 3
    and translate_adapter.test_scan_hint.plan.slots[1] == 0
    and translate_adapter.test_scan_hint.plan.slots[2] == 2
    and translate_adapter.test_scan_hint.plan.slots[3] == 3,
    "first stable retry did not include latest and two background slots")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 3
    and translate_adapter.test_scan_hint.plan.slots[1] == 0,
    "second stable retry did not include latest")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 2
    and translate_adapter.test_scan_hint.plan.slots[1] == 6
    and translate_adapter.test_scan_hint.plan.slots[2] == 7,
    "steady scan continued deep-reading latest or exceeded two background slots")
translate_adapter.test_scan_hint.background_body_slot = translate_adapter.test_scan_hint.plan.slots[1]
write_bytes(widget_map_address(translate_adapter.test_scan_hint.background_body_slot) + 0x158, string.char(1))
write_bytes(widget_map_address(translate_adapter.test_scan_hint.background_body_slot) + 8,
    pack32(WIDGET_KEY) .. pack32(1) .. pack64(widget_record_address(4) + 0xB4) .. pack64(0))
status, message = observer_widget_read_widget_slot(
    translate_adapter.test_scan_hint.background_body_slot, true)
assert(status == "ok" and message.body == WIDGET_ASCII, "background slot setup was not readable")
translate_adapter.test_scan_hint.index = 1
while translate_adapter.test_scan_hint.index <= #translate_adapter.test_scan_hint.plan.slots do
    local slot = translate_adapter.test_scan_hint.plan.slots[translate_adapter.test_scan_hint.index]
    translate_adapter.test_scan_hint.covered_slots[slot] = true
    if slot == translate_adapter.test_scan_hint.background_body_slot then
        status, message = observer_widget_read_widget_slot(slot, true)
        assert(status == "ok" and message.body == WIDGET_ASCII,
            "scheduled background read did not observe the original body")
    end
    translate_adapter.test_scan_hint.index = translate_adapter.test_scan_hint.index + 1
end
write_widget_record(4, WIDGET_EVENT, widget_body_bytes("Older body changed"))
translate_adapter.test_scan_hint.cycle = 1
translate_adapter.test_scan_hint.body_rechecked = false
while translate_adapter.test_scan_hint.cycle <= 32 do
    translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
    assert(type(translate_adapter.test_scan_hint.plan) == "table"
        and #translate_adapter.test_scan_hint.plan.slots == 2,
        "steady background scan did not rotate exactly two slots")
    translate_adapter.test_scan_hint.index = 1
    while translate_adapter.test_scan_hint.index <= #translate_adapter.test_scan_hint.plan.slots do
        local slot = translate_adapter.test_scan_hint.plan.slots[translate_adapter.test_scan_hint.index]
        translate_adapter.test_scan_hint.covered_slots[slot] = true
        if slot == translate_adapter.test_scan_hint.background_body_slot then
            status, message = observer_widget_read_widget_slot(slot, true)
            assert(status == "ok" and message.body == "Older body changed",
                "background audit did not observe the in-place body change")
            translate_adapter.test_scan_hint.body_rechecked = true
            translate_adapter.test_scan_hint.body_recheck_plan = translate_adapter.test_scan_hint.cycle
        end
        translate_adapter.test_scan_hint.index = translate_adapter.test_scan_hint.index + 1
    end
    translate_adapter.test_scan_hint.cycle = translate_adapter.test_scan_hint.cycle + 1
end
assert(translate_adapter.test_scan_hint.body_rechecked
    and translate_adapter.test_scan_hint.body_recheck_plan <= 32,
    "in-place body change was not revisited within one 64-slot audit cycle")
translate_adapter.test_scan_hint.index = 0
while translate_adapter.test_scan_hint.index <= 63 do
    assert(translate_adapter.test_scan_hint.covered_slots[translate_adapter.test_scan_hint.index], "background rotation missed slot " .. tostring(translate_adapter.test_scan_hint.index))
    translate_adapter.test_scan_hint.index = translate_adapter.test_scan_hint.index + 1
end

-- 有历史时后台物理槽只允许落在当前活跃history集合内。
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 3)
translate_adapter.scan_plan()
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots <= 3,
    "background hint included slots outside a short active history")
for _, slot in ipairs(translate_adapter.test_scan_hint.plan.slots) do
    assert((1 - 1 - slot) % 64 < 3,
        "background hint included an inactive history slot")
end

-- 52字节快照只比较head/count；未证明稳定的中间44字节变化不得阻止hint。
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 1)
translate_adapter.scan_plan()
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 1)
read_mutation = function(address, count)
    if address == translate_adapter.test_scan_hint.history and count == 2 then
        write_bytes(translate_adapter.test_scan_hint.history + 4, string.rep("Y", 44))
    end
end
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
read_mutation = nil
assert(type(translate_adapter.test_scan_hint.plan) == "table"
    and #translate_adapter.test_scan_hint.plan.slots >= 1
    and #translate_adapter.test_scan_hint.plan.slots <= 3,
    "unrelated UI history gap bytes invalidated a stable hint")

prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 1)
read_mutation = function(address, count)
    if address == translate_adapter.test_scan_hint.history and count == 2 then
        write_bytes(translate_adapter.test_scan_hint.history, pack32(2) .. string.rep("\0", 44) .. pack32(1))
    end
end
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
read_mutation = nil
assert(translate_adapter.test_scan_hint.plan == nil, "changed UI history head was accepted")

prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 1)
read_mutation = function(address, count)
    if address == widget_metadata and count == 2 then
        write_bytes(widget_metadata, pack32(6) .. pack32(1))
    end
end
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
read_mutation = nil
assert(translate_adapter.test_scan_hint.plan == nil, "changed event metadata was accepted by scan hint")

prepare_widget_case()
translate_adapter.test_scan_hint.write_history(1, 1)
read_mutation = function(address, count)
    if address == translate_adapter.test_scan_hint.history and count == 2 then
        write_bytes(widget_root_global, pack64(widget_root_alternate))
    end
end
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
read_mutation = nil
assert(translate_adapter.test_scan_hint.plan == nil, "changed widget root was accepted by scan hint")

-- 空历史环在核验root和metadata后直接返回，不访问残留的UI属性和正文。
prepare_widget_case({active_count = 0, map_count = 14})
translate_adapter.test_scan_hint.write_history(1, 1)
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.plan) == "table" and #translate_adapter.test_scan_hint.plan.slots == 0,
    "empty event ring should return an empty hint plan")
assert(read_counts[translate_adapter.test_scan_hint.history] == nil, "empty event ring read UI history")

-- count为零但event环仍活跃时只做一次baseline，之后走两个槽的稀疏回退。
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(10, 0)
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 64,
    "history empty transition did not request one baseline")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 3,
    "first empty-history retry did not stay bounded")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 3,
    "second empty-history retry did not stay bounded")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 2,
    "stable empty history did not use two-slot sparse fallback")
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(#translate_adapter.test_scan_hint.plan.slots == 2,
    "stable empty history triggered another full resync")

-- 历史head跨63到0时，新到的多个槽按FIFO顺序优先进入候选。
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(62, 60)
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.plan) == "table"
    and #translate_adapter.test_scan_hint.plan.slots == 64,
    "history reactivation did not request a bounded resync")
prepare_widget_case()
translate_adapter.test_scan_hint.write_history(2, 64)
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.plan) == "table"
    and translate_adapter.test_scan_hint.plan.slots[1] == 62
    and translate_adapter.test_scan_hint.plan.slots[2] == 63
    and translate_adapter.test_scan_hint.plan.slots[3] == 0
    and translate_adapter.test_scan_hint.plan.slots[4] == 1,
    "history wrap did not preserve FIFO order for new rows")
prepare_widget_case({active_count = 0, map_count = 14})
local empty_ring_status = observer_widget_read_widget_slot(0, true)
assert(empty_ring_status == "empty", tostring(empty_ring_status))
assert(read_counts[widget_map_count] == nil and read_counts[widget_entries] == nil
    and read_counts[widget_event_record] == nil and read_counts[widget_body] == nil,
    "empty event ring touched stale UI data")

prepare_widget_case({active_count = 0, map_count = 14})
read_mutation = function(address, count)
    if address == widget_metadata and count == 1 then
        write_bytes(widget_root_global, pack64(widget_root_alternate))
    end
end
local empty_root_drift = observer_widget_read_widget_slot(0, true)
read_mutation = nil
assert(empty_root_drift == "unstable" and read_counts[widget_map_count] == nil,
    "empty ring ignored root mutation")

prepare_widget_case({active_count = 0, map_count = 14})
read_mutation = function(address, count)
    if address == widget_metadata and count == 2 then
        write_bytes(widget_metadata, pack32(6) .. pack32(1))
    end
end
local empty_metadata_drift = observer_widget_read_widget_slot(0, true)
read_mutation = nil
assert(empty_metadata_drift == "unstable" and read_counts[widget_map_count] == nil,
    "empty ring ignored metadata mutation")

-- 一次scratch分配支撑多次读取；已返回字符串快照互不覆盖。
prepare_widget_case()
local saved_ffi_new = ffi.new
local ffi_new_calls = 0
ffi.new = function(...)
    ffi_new_calls = ffi_new_calls + 1
    return saved_ffi_new(...)
end
local first_snapshot = observer_read(widget_body, 16)
write_bytes(widget_body, "Z" .. first_snapshot:sub(2))
local second_snapshot = observer_read(widget_body, 16)
local scratch_ffi_new_calls = ffi_new_calls
ffi.new = saved_ffi_new
assert(scratch_ffi_new_calls == 0, "observer reads allocated temporary FFI buffers")
assert(first_snapshot:byte(1) == WIDGET_ASCII:byte(1)
    and second_snapshot:byte(1) == string.byte("Z")
    and first_snapshot ~= second_snapshot, "read snapshots shared reusable storage")

-- 重入读取使用独立scratch，异常退出后主scratch仍可继续读取。
prepare_widget_case()
local nested_snapshot
local entered_nested_read = false
read_mutation = function(address, _)
    if address == widget_body and not entered_nested_read then
        entered_nested_read = true
        nested_snapshot = observer_read(widget_event_record, 4)
    end
end
local outer_snapshot = observer_read(widget_body, 16)
read_mutation = nil
assert(outer_snapshot and nested_snapshot == pack32(WIDGET_EVENT), "reentrant read corrupted a snapshot")

prepare_widget_case()
local raise_once = true
read_mutation = function(address, _)
    if address == widget_body and raise_once then
        raise_once = false
        error("PRIVATE_READ_FAILURE")
    end
end
local read_raised = not pcall(observer_read, widget_body, 16)
read_mutation = nil
local recovered_snapshot = observer_read(widget_body, 16)
assert(read_raised and recovered_snapshot, "scratch pool did not recover after a read error")

-- 第二次query使用新的标量快照，即使底层MBI复用同一块scratch。
local first_region_snapshot = assert(observer_query_address(widget_root))
widget_root_region.protect = 0x08
local second_region_snapshot = assert(observer_query_address(widget_root))
widget_root_region.protect = 0x04
assert(first_region_snapshot ~= second_region_snapshot and first_region_snapshot.protect == 0x04
    and second_region_snapshot.protect == 0x08, "VirtualQuery snapshots aliased reusable memory")

prepare_widget_case()
rpm_short_on_call = read_calls + 1
local short_read_value, short_read_reason = observer_read(widget_body, 8)
rpm_short_on_call = nil
assert(short_read_value == nil and short_read_reason == "short_read", "short RPM read was accepted")
assert(observer_read(widget_body, 8), "scratch pool failed after a short read")

-- 稳定的不匹配event code只产生过滤状态；正文不读。漂移与稳定读失败仍映射到stale/failed。
prepare_widget_case({event_code = 0x12345678})
local filtered_event_status = translate_adapter.read_slot(0)
assert(filtered_event_status == "filtered_event")
assert(read_counts[widget_event_record] == 2 and read_counts[widget_body] == nil)

prepare_widget_case({map_count = 15})
local stable_failure_status = translate_adapter.read_slot(0)
assert(stable_failure_status == "read_failed")
assert(read_counts[widget_entries] == nil)

prepare_widget_case()
read_mutation = function(address, count)
    if address == widget_event_record and count == 2 then
        write_bytes(widget_event_record, pack32(0x12345678))
    end
end
local unstable_status = translate_adapter.read_slot(0)
read_mutation = nil
assert(unstable_status == "stale", tostring(unstable_status))

local private_message = message.body
put_file(bridge_path, "HD2CT1 0\n")
observer_now = 4101
translate_adapter.heartbeat()
prepare_widget_case({body = widget_body_bytes("must remain unread")})
local stale_read_count = read_calls
local stale_status = translate_adapter.read_slot(0)
assert(stale_status == "stale" and read_calls == stale_read_count)
assert(not translate_heartbeat_fresh)

-- standalone transport的Lua错误需局部捕获，不得触发memory adapter全局故障锁。
STANDALONE_ENABLED = true
translate_heartbeat_fresh = true
native_transport_api = {
    submit = function() return true end,
    response = function() return "OK\ntranslated" end,
    cancel = function() return true end,
}
local standalone_heartbeat = translate_adapter.heartbeat()
assert(standalone_heartbeat == "HD2CT1 4101\n" and not observer_faulted)
native_transport_api.submit = function() error("PRIVATE_NATIVE_SUBMIT") end
local submit_error_result, submit_error_code = translate_adapter.submit(token, "Standalone body")
assert(submit_error_result == false and submit_error_code == "SUBMIT_EXCEPTION" and not observer_faulted)
native_transport_api.response = function() error("PRIVATE_NATIVE_RESPONSE") end
assert(translate_adapter.response(token) == "ERR\nRESPONSE_EXCEPTION" and not observer_faulted)
STANDALONE_ENABLED = false
native_transport_api = nil
translate_heartbeat_fresh = false

-- 每槽保守预留4096字节；预算不足立即延期，Fake VirtualQuery/RPM保持静默。
observer_now = 4200
put_file(bridge_path, "HD2CT1 4200\n")
translate_adapter.heartbeat()
prepare_widget_case()
observer_read_budget = MAX_OBSERVER_READ - 4095
local budget_queries, budget_reads = query_calls, read_calls
local budget_status = translate_adapter.read_slot(0)
assert(budget_status == "deferred")
assert(query_calls == budget_queries and read_calls == budget_reads)

-- 请求仅允许当前session、有效UTF-8正文和新建文件；任何写入失败都清掉临时文件。
observer_read_budget = 0
TRANSLATE_ENABLED = false
local disabled_publish = translate_adapter.submit(token, "English body")
assert(disabled_publish == false and #list_temporary_files() == 0)
TRANSLATE_ENABLED = true
translate_heartbeat_fresh = false
translate_cached_heartbeat = nil
local no_heartbeat = translate_adapter.submit(token, "English body")
assert(no_heartbeat == false)
put_file(bridge_path, "HD2CT1 4200\n")
translate_adapter.heartbeat()
assert(no_heartbeat == false and #list_temporary_files() == 0)
local invalid_create_count = #create_log
assert(translate_adapter.submit("other_session_1", "English body") == false)
assert(translate_adapter.submit(token, "bad" .. string.char(0) .. "text") == false)
assert(translate_adapter.submit(token, string.char(0xc0, 0xaf)) == false)
assert(translate_adapter.submit(token, string.rep("A", 1024)) == false)
assert(translate_adapter.submit(token .. "../other", "English body") == false)
assert(#create_log == invalid_create_count, "invalid requests created mailbox files")

assert(translate_adapter.submit(token, "English body"))
local request_path = mailbox_path .. "\\" .. token .. ".req"
assert(files[request_path] and files[request_path].data == "English body")
assert(#list_temporary_files() == 0)
local request_move = move_log[#move_log]
assert(request_move.flags == 0x8 and request_move.new == request_path)

-- CreateNew和no-replace防止覆盖现有req；写短、flush和rename错误都留下干净目录。
local collision_token = session_prefix .. "2"
local collision_path = mailbox_path .. "\\" .. collision_token .. ".req"
put_file(collision_path, "keep existing request")
assert(not translate_adapter.submit(collision_token, "replace me"))
assert(files[collision_path].data == "keep existing request")
assert(#list_temporary_files() == 0)

local function failed_submit(suffix, behavior)
    reset_fs_failures()
    for name, value in pairs(behavior) do fs_behavior[name] = value end
    local failed_token = session_prefix .. suffix
    local before = #move_log
    assert(not translate_adapter.submit(failed_token, "temporary only"))
    assert(#list_temporary_files() == 0, "failed request left a temporary file")
    return #move_log - before
end
local short_writes_moved = failed_submit("3", {write_limit = 2})
local flush_failure_moved = failed_submit("4", {fail_flush = true})
local rename_failure_moved = failed_submit("5", {fail_move = true})
local close_failure_moved = failed_submit("6", {fail_close = true})
assert(short_writes_moved == 0 and flush_failure_moved == 0
    and rename_failure_moved == 1 and close_failure_moved == 0)
reset_fs_failures()

-- 响应只读取本session拥有的token；上限内可返回，短读/失败和超限可被上层拒绝。
local response_path = mailbox_path .. "\\" .. token .. ".res"
put_file(response_path, "OK\ntranslated")
assert(translate_adapter.response(token) == "OK\ntranslated")
put_file(response_path, "OK\n" .. string.rep("R", 16384))
assert(#translate_adapter.response(token) == 16387)
put_file(response_path, "OK\n" .. string.rep("R", 16385))
assert(translate_adapter.response(token) == "")
put_file(response_path, "OK\nshort-read")
fs_behavior.read_limit = 2
assert(translate_adapter.response(token) == "OK")
fs_behavior.read_limit = nil
fs_behavior.fail_read = true
assert(translate_adapter.response(token) == nil)
fs_behavior.fail_read = false
fs_behavior.fail_close = true
assert(translate_adapter.response(token) == nil)
fs_behavior.fail_close = false
local foreign_token = session_prefix .. "999"
local foreign_response = mailbox_path .. "\\" .. foreign_token .. ".res"
put_file(foreign_response, "OK\nforeign")
assert(translate_adapter.response(foreign_token) == nil)

-- cancel仅能清理由本实例发布的req/res；processing和其它token保持原样。
put_file(mailbox_path .. "\\" .. token .. ".processing", "worker owns this")
assert(translate_adapter.cancel(foreign_token) == false)
assert(files[foreign_response] ~= nil)
assert(translate_adapter.cancel(token) == true)
assert(files[request_path] == nil and files[response_path] == nil)
assert(files[mailbox_path .. "\\" .. token .. ".processing"].data == "worker owns this")
assert(files[foreign_response] ~= nil)

-- report仅包含固定白名单字段；普通聊天正文、proof、地址和未知状态不会落盘。
local sanitized_input = {
    status = "PRIVATE_STATUS", code = "PRIVATE_CODE", body = private_message,
    proof = {address = "PRIVATE_ADDRESS"}, extra = "PRIVATE_FIELD",
    baseline_remaining = 999, pending_count = 999,
    counters = {steps = 3, submitted = 1, slot_event_filtered = 7,
        error_displays_ready = 2, private_counter = 77},
}
local flushes_before_report = flush_calls
assert(translate_adapter.output(sanitized_input))
local sanitized = files[report_path].data
assert(flush_calls == flushes_before_report, "translation report unexpectedly flushed to disk")
local report_move = move_log[#move_log]
assert(report_move.flags == 0x1 and report_move.new == report_path,
    "translation report did not replace the final file atomically")
assert(not sanitized:find(private_message, 1, true))
assert(not sanitized:find("PRIVATE_", 1, true))
assert(sanitized:find('"status":"stopped"', 1, true))
assert(sanitized:find('"baseline_remaining":0', 1, true))
assert(sanitized:find('"pending_count":0', 1, true))
assert(sanitized:find('"slot_event_filtered":7', 1, true))
assert(sanitized:find('"error_displays_ready":2', 1, true))
assert(not sanitized:find('"private_counter"', 1, true))

-- 报告短写、关闭失败或替换失败时清理临时文件并保留旧报告。
local function failed_report(behavior)
    reset_fs_failures()
    for name, value in pairs(behavior) do fs_behavior[name] = value end
    local before_moves = #move_log
    local before_flushes = flush_calls
    local ok = pcall(observer_translate_write_report, sanitized_input)
    assert(not ok, "failed report write unexpectedly succeeded")
    assert(#list_temporary_files() == 0, "failed report write left a temporary file")
    assert(files[report_path].data == sanitized, "failed report write replaced the last good report")
    assert(flush_calls == before_flushes, "translation report unexpectedly flushed to disk")
    return #move_log - before_moves
end
local short_report_moves = failed_report({write_limit = 2})
local close_report_moves = failed_report({fail_close = true})
local rename_report_moves = failed_report({fail_move = true})
assert(short_report_moves == 0 and close_report_moves == 0 and rename_report_moves == 1)
reset_fs_failures()

-- 本native spy只模拟已知setter对目标属性entry的写入，绝不调用game.dll。
native_spy_calls = 0
prepare_widget_case({body = widget_body_bytes("你好，潜兵")})
observer_now = 4900
put_file(bridge_path, "HD2CT1 4900\n")
translate_adapter.heartbeat()
observer_read_budget = 0
local chinese_status, chinese_message = translate_adapter.read_slot(0)
assert(chinese_status == "ok" and chinese_message.body == "你好，潜兵")
-- 模拟下一帧的独立回写预算，不把前一帧扫描开销重复计入。
observer_read_budget = 0
local chinese_apply_status = translate_adapter.apply(chinese_message, chinese_message.body)
assert(chinese_apply_status == "stale", tostring(chinese_apply_status) .. ":" .. tostring(observer_faulted))
assert(native_spy_calls == 0, "unchanged Chinese source reached the native setter")

prepare_widget_case({body = widget_body_bytes("Hello, divers")})
observer_now = 5000
put_file(bridge_path, "HD2CT1 5000\n")
translate_adapter.heartbeat()
observer_read_budget = 0
local read_status, captured = translate_adapter.read_slot(0)
assert(read_status == "ok" and captured.body == "Hello, divers")
observer_read_budget = 0
local apply_text = "Hello, divers\n译文：各位潜兵，集合撤离"
local apply_status = translate_adapter.apply(captured, apply_text)
assert(apply_status == "called_confirmed" and native_spy_calls == 1)
assert(native_spy_key == 0x7518C954 and native_spy_text == apply_text)
assert(native_spy_widget == widget_slot_address + 0x110)
local registry = rawget(_G, TRANSLATE_PIN_TABLE)
assert(type(registry) == "table" and #registry.buffers == 1)
collectgarbage("collect")
assert(ffi.string(registry.buffers[1]) == native_spy_text)
assert(registry.bytes == #native_spy_text + 1)
local successful_spy_calls = native_spy_calls

-- 完整显示文本上限由翻译核心提供；超限、NUL与无效UTF-8不会分配或触发setter。
native_spy_calls = 0
rawset(_G, TRANSLATE_PIN_TABLE, nil)
prepare_widget_case({body = widget_body_bytes("maximum display source")})
observer_now = observer_now + 1
put_file(bridge_path, string.format("HD2CT1 %d\n", observer_now))
translate_adapter.heartbeat()
observer_read_budget = 0
local max_status, max_message = translate_adapter.read_slot(0)
assert(max_status == "ok")
observer_read_budget = 0
local oversized_status = translate_adapter.apply(
    max_message, string.rep("D", translate_core.MAX_DISPLAY_BYTES + 1))
local nul_status = translate_adapter.apply(max_message, "bad" .. string.char(0) .. "text")
local utf8_status = translate_adapter.apply(max_message, string.char(0xc0, 0xaf))
assert(oversized_status == "read_failed" and nul_status == "read_failed" and utf8_status == "read_failed")
assert(native_spy_calls == 0 and rawget(_G, TRANSLATE_PIN_TABLE) == nil)
local max_display_text = string.rep("M", translate_core.MAX_DISPLAY_BYTES)
local max_apply_status = translate_adapter.apply(max_message, max_display_text)
assert(max_apply_status == "called_confirmed" and native_spy_calls == 1)
assert(#native_spy_text == 17417 and native_spy_text == max_display_text)
local max_registry = rawget(_G, TRANSLATE_PIN_TABLE)
assert(max_registry.bytes == 17418 and #max_registry.buffers == 1)
collectgarbage("collect")
assert(ffi.string(max_registry.buffers[1]) == max_display_text,
    "maximum display buffer was not kept alive through native use")

-- 文本、event槽、owner/root、环metadata或property map漂移时都不能触发native setter。
local function apply_after_mutation(mutate)
    native_spy_calls = 0
    rawset(_G, TRANSLATE_PIN_TABLE, nil)
    prepare_widget_case({body = widget_body_bytes("same captured body")})
    observer_now = observer_now + 1
    put_file(bridge_path, string.format("HD2CT1 %d\n", observer_now))
    translate_adapter.heartbeat()
    observer_read_budget = 0
    local status_before, captured_before = translate_adapter.read_slot(0)
    assert(status_before == "ok")
    mutate(captured_before)
    observer_read_budget = 0
    local result = translate_adapter.apply(captured_before, "译文")
    return result, native_spy_calls
end

local changed_body, body_calls = apply_after_mutation(function()
    write_widget_record(widget_event_slot, WIDGET_EVENT, widget_body_bytes("changed body"))
end)
assert(changed_body == "stale" and body_calls == 0)

local changed_event, event_calls = apply_after_mutation(function()
    prepare_widget_case({event_slot = 5, next_index = 6, active_count = 2,
        body = widget_body_bytes("same captured body")})
end)
assert(changed_event == "stale" and event_calls == 0)

local changed_map, map_calls = apply_after_mutation(function()
    write_widget_map(14, {[13] = true}, widget_body + 1)
end)
assert((changed_map == "stale" or changed_map == "read_failed") and map_calls == 0)

local changed_root, root_calls = apply_after_mutation(function()
    local alternate = add_region(widget_root_alternate, widget_root_size,
        widget_root_alternate, 0x20000, 0x04)
    ffi.copy(alternate.data, widget_root_region.data, widget_root_size)
    local alternate_ring = widget_root_alternate + 0x4F7080
    local alternate_widget = widget_root_alternate + 0x14498 + 0x4390
    local alternate_map = alternate_widget + 0x220
    local entry = alternate_map + 8 + 13 * 0x18
    write_bytes(entry + 8, pack64(alternate_ring + widget_event_slot * 0x4B4 + 0xB4))
    write_bytes(widget_root_global, pack64(widget_root_alternate))
end)
assert(changed_root == "stale" and root_calls == 0)
observer_read_budget = 0
translate_adapter.test_scan_hint.plan = translate_adapter.scan_plan()
assert(type(translate_adapter.test_scan_hint.plan) == "table"
    and translate_adapter.test_scan_hint.plan.owner_id ~= translate_adapter.test_scan_hint.owner_before
    and translate_adapter.test_scan_hint.plan.reset_owner == true
    and #translate_adapter.test_scan_hint.plan.slots == 0,
    "root change did not produce an empty owner-change hint")
write_bytes(widget_root_global, pack64(widget_root))

local changed_ring, ring_calls = apply_after_mutation(function()
    write_bytes(widget_metadata, pack32(5) .. pack32(0))
end)
assert((changed_ring == "stale" or changed_ring == "read_failed") and ring_calls == 0)

-- setter紧前二次读心跳：apply捕获正文后若服务flag消失，setter spy保持零调用。
native_spy_calls = 0
prepare_widget_case({body = widget_body_bytes("heartbeat race source")})
observer_now = observer_now + 1
put_file(bridge_path, string.format("HD2CT1 %d\n", observer_now))
translate_adapter.heartbeat()
observer_read_budget = 0
local race_status, race_message = translate_adapter.read_slot(0)
assert(race_status == "ok")
read_mutation = function(address, count)
    if address == widget_body and count == 3 then files[bridge_path] = nil end
end
observer_read_budget = 0
local race_result = translate_adapter.apply(race_message, "不得回写")
read_mutation = nil
assert(race_result == "disabled" and native_spy_calls == 0)

-- pin表受512项与8MiB两重上限约束，达到上限后不释放或覆写旧buffer。
rawset(_G, TRANSLATE_PIN_TABLE, nil)
for _ = 1, 512 do assert(observer_translate_pin_text("x")) end
local count_capacity_buffer, count_capacity_status = observer_translate_pin_text("x")
local count_registry = rawget(_G, TRANSLATE_PIN_TABLE)
assert(count_capacity_buffer == nil and count_capacity_status == "capacity")
assert(#count_registry.buffers == 512 and count_registry.buffers[1] ~= nil)

rawset(_G, TRANSLATE_PIN_TABLE, nil)
assert(translate_core.MAX_DISPLAY_BYTES == 17417)
local one_max_display = string.rep("P", translate_core.MAX_DISPLAY_BYTES)
local max_pin = observer_translate_pin_text(one_max_display)
assert(max_pin ~= nil)
assert(observer_translate_pin_text(string.rep("O", translate_core.MAX_DISPLAY_BYTES + 1)) == nil)
assert(observer_translate_pin_text("bad" .. string.char(0) .. "text") == nil)
assert(observer_translate_pin_text(string.char(0xc0, 0xaf)) == nil)
local one_max_registry = rawget(_G, TRANSLATE_PIN_TABLE)
assert(one_max_registry.bytes == 17418 and #one_max_registry.buffers == 1)
collectgarbage("collect")
assert(ffi.string(one_max_registry.buffers[1]) == one_max_display)

rawset(_G, TRANSLATE_PIN_TABLE, nil)
local large_text = string.rep("B", translate_core.MAX_DISPLAY_BYTES)
for _ = 1, 481 do assert(observer_translate_pin_text(large_text)) end
local byte_capacity_buffer, byte_capacity_status = observer_translate_pin_text(large_text)
local byte_registry = rawget(_G, TRANSLATE_PIN_TABLE)
assert(byte_capacity_buffer == nil and byte_capacity_status == "capacity")
assert(byte_registry.bytes == 17418 * 481 and byte_registry.bytes <= 8 * 1024 * 1024)

RESULT = json_core.encode_json({
    status = "translate fake Win32/kernel checks ok",
    published = request_path,
    report_bytes = #sanitized,
    read_status = status,
    apply_status = apply_status,
    native_calls = successful_spy_calls,
    stale_results = {changed_body, changed_event, changed_map, changed_root, changed_ring},
    failed_race_status = race_result,
})
'''


TRANSLATE_SETTER_SPY = r'''
local native_spy_calls, native_spy_widget, native_spy_key, native_spy_text = 0, nil, nil, nil
local function observer_translate_setter(widget_argument, buffer)
    if not TRANSLATE_ENABLED or not observer_display_native_gate then return false end
    native_spy_calls = native_spy_calls + 1
    native_spy_widget = tonumber(ffi.cast("size_t", widget_argument))
    native_spy_key = OBSERVER_WIDGET_KEY
    native_spy_text = ffi.string(buffer)
    local map_address = native_spy_widget + 0x110
    local count_address = map_address + 0x158
    local count_region = assert(find_region(count_address))
    local count = tonumber(count_region.data[count_address - count_region.base])
    local entries_address = map_address + 8
    local buffer_address = tonumber(ffi.cast("size_t", ffi.cast("void *", buffer)))
    local found = 0
    for index = 0, count - 1 do
        local entry = entries_address + index * 0x18
        local entry_region = assert(find_region(entry))
        local key_bytes = ffi.string(entry_region.data + (entry - entry_region.base), 4)
        local key = key_bytes:byte(1) + key_bytes:byte(2) * 256
            + key_bytes:byte(3) * 65536 + key_bytes:byte(4) * 16777216
        if key == OBSERVER_WIDGET_KEY then
            found = found + 1
            write_bytes(entry + 4, pack32(1))
            write_bytes(entry + 8, pack64(buffer_address))
        end
    end
    assert(found == 1, "spy expected one unique body property")
    return true
end
'''


STARTUP_RETENTION_CHECKS = r'''
local ffi = require("ffi")
ffi.cdef[[__FFI_DECL__]]
__RETENTION_HELPER__

assert(ffi.sizeof("HD2Probe_WIN32_FIND_DATAW") == 592)
assert(ffi.offsetof("HD2Probe_WIN32_FIND_DATAW", "cFileName") == 44)
assert(ffi.offsetof("HD2Probe_WIN32_FIND_DATAW", "cAlternateFileName") == 564)

local local_app_units = {67, 58, 92, 29992, 25143, 92, 76, 111, 99, 97, 108}
local local_app = ffi.new("HD2Probe_U16[?]", #local_app_units + 1, local_app_units)
local local_app_text = "C:\\用户\\Local"
local app_root = local_app_text .. "\\HD2ChatTranslate"
local directories = {
    [app_root] = 0x10,
    [app_root .. "\\probe"] = 0x10,
    [app_root .. "\\observe"] = 0x10,
    [app_root .. "\\mailbox"] = 0x10,
    [app_root .. "\\native"] = 0x10,
}
local files, find_handles, delete_log = {}, {}, {}
local next_find_handle, closed_handles, last_error = 4096, 0, 0
local behavior = {enum_error_directory = nil, find_error_directory = nil, delete_error_path = nil}
local kernel = {}

local function wide_text(pointer)
    local bytes = {}
    for index = 0, 32767 do
        local code = tonumber(pointer[index])
        if code == 0 then return table.concat(bytes) end
        if code < 0x80 then
            bytes[#bytes + 1] = string.char(code)
        elseif code < 0x800 then
            bytes[#bytes + 1] = string.char(0xc0 + math.floor(code / 64), 0x80 + code % 64)
        else
            bytes[#bytes + 1] = string.char(0xe0 + math.floor(code / 4096),
                0x80 + math.floor(code / 64) % 64, 0x80 + code % 64)
        end
    end
    return nil
end

kernel.GetEnvironmentVariableW = function(_, buffer, _)
    for index, code in ipairs(local_app_units) do buffer[index - 1] = code end
    buffer[#local_app_units] = 0
    return #local_app_units
end
kernel.GetLastError = function() return last_error end
kernel.GetFileAttributesW = function(path)
    local name = wide_text(path)
    assert(name:find("用户", 1, true), "Unicode parent path was lost")
    if directories[name] then return directories[name] end
    if files[name] then return files[name].attributes end
    last_error = 2
    return 0xffffffff
end

local function copy_find_data(destination, item)
    destination[0].dwFileAttributes = item.attributes
    destination[0].ftLastWriteTime.dwHighDateTime = item.high or 0
    destination[0].ftLastWriteTime.dwLowDateTime = item.low or 0
    for index = 0, 259 do destination[0].cFileName[index] = 0 end
    for index = 1, #item.name do destination[0].cFileName[index - 1] = item.name:byte(index) end
end

local function enumerate_directory(directory)
    local found, prefix = {}, directory .. "\\"
    for path, item in pairs(files) do
        if path:sub(1, #prefix) == prefix then
            local name = path:sub(#prefix + 1)
            if not name:find("\\", 1, true) then found[#found + 1] = {
                name = name, attributes = item.attributes, high = item.high, low = item.low,
            } end
        end
    end
    for path, attributes in pairs(directories) do
        if path:sub(1, #prefix) == prefix then
            local name = path:sub(#prefix + 1)
            if not name:find("\\", 1, true) then found[#found + 1] = {
                name = name, attributes = attributes, high = 0, low = 0,
            } end
        end
    end
    table.sort(found, function(left, right) return left.name < right.name end)
    return found
end

kernel.FindFirstFileW = function(pattern, find_data)
    local search = wide_text(pattern)
    local directory = search:sub(1, -3)
    if behavior.find_error_directory == directory then last_error = 5; return ffi.cast("HD2Probe_HANDLE", -1) end
    local entries = enumerate_directory(directory)
    if #entries == 0 then last_error = 2; return ffi.cast("HD2Probe_HANDLE", -1) end
    next_find_handle = next_find_handle + 1
    local handle = ffi.cast("HD2Probe_HANDLE", next_find_handle)
    find_handles[next_find_handle] = {directory = directory, entries = entries, index = 1}
    copy_find_data(find_data, entries[1])
    return handle
end

local function find_record(handle)
    return find_handles[tonumber(ffi.cast("size_t", handle))]
end

kernel.FindNextFileW = function(handle, find_data)
    local record = find_record(handle)
    assert(record)
    if behavior.enum_error_directory == record.directory then last_error = 5; return 0 end
    if record.index >= #record.entries then last_error = 18; return 0 end
    record.index = record.index + 1
    copy_find_data(find_data, record.entries[record.index])
    return 1
end
kernel.FindClose = function(handle)
    local record = find_record(handle)
    if not record then return 0 end
    record.closed = true
    closed_handles = closed_handles + 1
    return 1
end
kernel.DeleteFileW = function(path)
    local name = wide_text(path)
    delete_log[#delete_log + 1] = name
    if behavior.delete_error_path == name or files[name] == nil then return 0 end
    files[name] = nil
    return 1
end

local function clear_files()
    for path in pairs(files) do files[path] = nil end
    delete_log = {}
    behavior.enum_error_directory = nil
    behavior.find_error_directory = nil
    behavior.delete_error_path = nil
end

local function put_file(directory, name, high, low, attributes)
    local path = directory .. "\\" .. name
    files[path] = {attributes = attributes or 0x80, high = high or 0, low = low or 0}
    return path
end

local function probe_name(epoch)
    return string.format("chat-probe-%d-%08x-%08x-%02d.json", epoch, epoch, epoch, epoch)
end
local function observe_name(epoch)
    return string.format("chat-observe-%d-%08x.json", epoch, epoch)
end
local function mailbox_name(epoch)
    return string.format("chat-translate-hd2ct_%d_%08x.json", epoch, epoch)
end
local function count_matching(directory, pattern)
    local total = 0
    for path, item in pairs(files) do
        if path:sub(1, #directory + 1) == directory .. "\\"
            and path:sub(#directory + 2):match(pattern)
            and math.floor(item.attributes / 0x10) % 2 == 0
            and math.floor(item.attributes / 0x40) % 2 == 0
            and math.floor(item.attributes / 0x400) % 2 == 0 then total = total + 1 end
    end
    return total
end
local function was_deleted(path)
    for _, deleted in ipairs(delete_log) do if deleted == path then return true end end
    return false
end

local probe_dir, observe_dir, mailbox_dir = app_root .. "\\probe", app_root .. "\\observe", app_root .. "\\mailbox"
local foreign_probe = put_file(probe_dir, "foreign.json", 9, 9)
local partial_probe = put_file(probe_dir, "chat-probe-99-00000001-00000002-01.json.partial", 9, 9)
local temp_probe = put_file(probe_dir, "chat-probe-99-00000001-00000002-01.json.tmp", 9, 9)
local directory_probe = probe_dir .. "\\chat-probe-98-00000001-00000002-01.json"
directories[directory_probe] = 0x10
local reparse_probe = put_file(probe_dir, "chat-probe-97-00000001-00000002-01.json", 9, 9, 0x400)
local probe_oldest, probe_second
for index = 1, 11 do
    local epoch = 100 - index
    local high = index == 1 and 1 or 2
    local low = index == 1 and 0xffffffff or index - 2
    local path = put_file(probe_dir, probe_name(epoch), high, low)
    if index == 1 then probe_oldest = path elseif index == 2 then probe_second = path end
end
for index = 1, 10 do put_file(observe_dir, observe_name(index), 3, index) end
local mailbox_ties = {}
for index = 1, 11 do mailbox_ties[index] = put_file(mailbox_dir, mailbox_name(index), 4, 7) end
table.sort(mailbox_ties)
local config_file = put_file(app_root, "config.json", 8, 8)
local bridge_file = put_file(mailbox_dir, "bridge.flag", 8, 8)
local request_file = put_file(mailbox_dir, "request.txt", 8, 8)
local response_file = put_file(mailbox_dir, "response.txt", 8, 8)
local native_file = put_file(app_root .. "\\native", "transport.dll", 8, 8)

local first = startup_report_retention.run(ffi, kernel, false, false)
assert(first.groups_checked == 3 and first.groups_skipped == 0)
assert(first.files_removed == 3 and first.delete_failures == 0)
assert(was_deleted(probe_oldest) and was_deleted(probe_second), "FILETIME high/low order was ignored")
assert(was_deleted(mailbox_ties[1]), "equal FILETIME did not use descending filename as a stable tie-break")
assert(not was_deleted(foreign_probe) and not was_deleted(partial_probe) and not was_deleted(temp_probe))
assert(not was_deleted(directory_probe) and not was_deleted(reparse_probe))
assert(not was_deleted(config_file) and not was_deleted(bridge_file) and not was_deleted(request_file)
    and not was_deleted(response_file) and not was_deleted(native_file))
assert(count_matching(probe_dir, "^chat%-probe%-.+%.json$") == 9)
assert(count_matching(observe_dir, "^chat%-observe%-.+%.json$") == 10)
assert(count_matching(mailbox_dir, "^chat%-translate%-hd2ct_.+%.json$") == 10)
assert(closed_handles == 3, "FindClose was not called for each complete enumeration")

clear_files()
directories[directory_probe] = nil
local empty_directories = startup_report_retention.run(ffi, kernel, false, false)
assert(empty_directories.groups_checked == 3 and empty_directories.files_removed == 0)
directories[observe_dir] = nil
local missing_directory = startup_report_retention.run(ffi, kernel, false, false)
assert(missing_directory.groups_checked == 2 and missing_directory.groups_skipped == 0)
directories[observe_dir] = 0x10

for index = 1, 10 do
    put_file(probe_dir, probe_name(index), 5, index)
    put_file(observe_dir, observe_name(index), 5, index)
    put_file(mailbox_dir, mailbox_name(index), 5, index)
end
local observe_reserved = startup_report_retention.run(ffi, kernel, true, false)
assert(observe_reserved.files_removed == 2)
assert(count_matching(probe_dir, "^chat%-probe%-.+%.json$") == 9)
assert(count_matching(observe_dir, "^chat%-observe%-.+%.json$") == 9)
assert(count_matching(mailbox_dir, "^chat%-translate%-hd2ct_.+%.json$") == 10)
put_file(probe_dir, probe_name(50), 6, 0)
put_file(observe_dir, observe_name(50), 6, 0)
local translate_reserved = startup_report_retention.run(ffi, kernel, true, true)
assert(translate_reserved.files_removed == 2)
assert(count_matching(probe_dir, "^chat%-probe%-.+%.json$") == 9)
assert(count_matching(observe_dir, "^chat%-observe%-.+%.json$") == 10)
assert(count_matching(mailbox_dir, "^chat%-translate%-hd2ct_.+%.json$") == 9)
put_file(probe_dir, probe_name(51), 7, 0)
put_file(mailbox_dir, mailbox_name(51), 7, 0)
assert(count_matching(probe_dir, "^chat%-probe%-.+%.json$") == 10)
assert(count_matching(observe_dir, "^chat%-observe%-.+%.json$") == 10)
assert(count_matching(mailbox_dir, "^chat%-translate%-hd2ct_.+%.json$") == 10)

clear_files()
local incomplete_old
for index = 1, 12 do
    put_file(probe_dir, probe_name(index), 8, index)
    local path = put_file(observe_dir, observe_name(index), 8, index)
    if index == 1 then incomplete_old = path end
end
behavior.enum_error_directory = observe_dir
local incomplete = startup_report_retention.run(ffi, kernel, false, false)
assert(incomplete.groups_skipped == 1 and not was_deleted(incomplete_old),
    "partial enumeration deleted entries from its group")
local closed_after_incomplete = closed_handles
assert(closed_after_incomplete >= 6, "FindClose did not run after an enumeration error")

clear_files()
for index = 1, 12 do put_file(observe_dir, observe_name(index), 8, index) end
behavior.find_error_directory = observe_dir
local denied = startup_report_retention.run(ffi, kernel, false, false)
assert(denied.groups_skipped == 1 and #delete_log == 0,
    "permission failure was not skipped for its group")

clear_files()
local failed_delete
for index = 1, 11 do
    local path = put_file(mailbox_dir, mailbox_name(index), 9, index)
    if index == 1 then failed_delete = path end
end
behavior.delete_error_path = failed_delete
local delete_failed = startup_report_retention.run(ffi, kernel, false, false)
assert(delete_failed.delete_failures == 1 and was_deleted(failed_delete) and files[failed_delete] ~= nil)
assert(find_handles[next_find_handle].closed and closed_handles >= closed_after_incomplete + 1,
    "enumeration handle stayed open after delete failure")

clear_files()
for index = 1, 12 do put_file(observe_dir, observe_name(index), 10, index) end
directories[observe_dir] = 0x410
local reparse_directory = startup_report_retention.run(ffi, kernel, false, false)
assert(reparse_directory.groups_skipped == 1 and #delete_log == 0,
    "reparse-point report directory was enumerated")
directories[observe_dir] = 0x10
directories[app_root] = 0x410
local reparse_root = startup_report_retention.run(ffi, kernel, false, false)
assert(reparse_root.groups_skipped == 3 and #delete_log == 0,
    "reparse-point application root was enumerated")
directories[app_root] = 0x10

local throwing_kernel = setmetatable({
    GetFileAttributesW = function() error("mock permission failure") end,
}, {__index = kernel})
local startup_survived = pcall(startup_report_retention.run, ffi, throwing_kernel, false, false)
assert(not startup_survived, "throwing Win32 mock did not reach the initializer pcall boundary")

RESULT = "startup retention mock checks ok"
'''


class ChatTranslateAdapterTests(unittest.TestCase):
    """用LuaJIT内存页和纯内存Win32文件mock测试受限adapter。"""

    @classmethod
    def setUpClass(cls) -> None:
        if LUA_DLL is None:
            raise RuntimeError("本机未提供LuaJIT lua51.dll，不能运行适配器fake-kernel测试")
        cls.lua = LuaJIT(LUA_DLL)

    def test_fake_filesystem_reader_and_native_setter_spy(self) -> None:
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        declaration = re.search(r"ffi\.cdef\[\[(.*?)\]\]", source, re.S)
        budget = re.search(r"local MAX_OBSERVER_READ = ([^\r\n]+)", source)
        self.assertIsNotNone(declaration)
        self.assertIsNotNone(budget)

        initialize_start = source.index("local function initialize_probe()")
        wide_start = source.index("    local function append_wide_ascii(", initialize_start)
        wide_end = source.index("    local function prepare_observer_paths(", wide_start)
        adapter_start = source.index("    local function new_observer_scratch()")
        adapter_end = source.index("    local adapter = {\n        hash_file", adapter_start)
        adapter = source[adapter_start:adapter_end]
        setter_start = adapter.index("    local function observer_translate_setter(")
        setter_end = adapter.index("    local function observer_translate_read_slot(", setter_start)
        adapter = adapter[:setter_start] + TRANSLATE_SETTER_SPY + adapter[setter_end:]

        prelude = r'''
local json_core = core
local translate_core = dofile([[TRANSLATE_CORE_PATH]])
local STANDALONE_ENABLED = false
local native_transport_api
local TRANSLATE_ENABLED = false
local DISPLAY_TEST_ENABLED = false
local observer_display_native_gate = false
local DISPLAY_TARGET_RVA = 0x1441CA0
local DISPLAY_TEST_TEXT = "聊天翻译测试成功"
local DISPLAY_PIN_TABLE = "__HD2_CHAT_DISPLAY_TEST_PINS_V1"
local DISPLAY_MAX_PINS = 16
local TRANSLATE_PIN_TABLE = "__HD2_CHAT_TRANSLATE_PINS_V1"
local TRANSLATE_MAX_PINS = 512
local TRANSLATE_MAX_PIN_BYTES = 8 * 1024 * 1024
local TRANSLATE_MAX_REQUEST_BYTES = 1023
local TRANSLATE_MAX_RESPONSE_BYTES = 16387
local TRANSLATE_MAX_REPORT_BYTES = 64 * 1024
local observer_faulted = false
local observer_output_sequence = 0
local observer_session_time = 0
local observer_session_nonce = 0
local observer_local_app, observer_local_app_length
local observer_directory, observer_directory_length, observer_request_path
local translate_mailbox_directory, translate_mailbox_directory_length
local translate_heartbeat_path, translate_report_path, translate_session_id
local translate_heartbeat_next_poll, translate_cached_heartbeat
local translate_heartbeat_fresh = false
local translate_owned_tokens = {}
local translate_file_sequence = 0
local translate_layout
translate_layout = {
    verified = true,
    instance = {
        prepare = function(context, target_row)
            return "ready", {context = context, target_row = target_row}
        end,
        verify_prepared = function() return "ready" end,
        reflow = function() return "called_confirmed" end,
        disable = function() end,
        stats = function()
            return {verified = true, reflows_confirmed = 1, failures = 0, rows_positioned = 1}
        end,
    },
    new = function() return translate_layout.instance end,
}

local function mock_wide(text)
    local value = ffi.new("HD2Probe_U16[?]", #text + 1)
    for index = 1, #text do value[index - 1] = text:byte(index) end
    value[#text] = 0
    return value
end

local function prepare_observer_paths()
    local path = "C:\\Mock\\LocalAppData"
    observer_local_app = mock_wide(path)
    observer_local_app_length = #path
    observer_session_nonce = 0x1234
end
'''
        script = LUA_OBSERVER_ADAPTER_HARNESS.replace(
            "__FFI_DECL__", declaration.group(1)
        ).replace(
            "__MAX_OBSERVER_READ__", budget.group(1).strip()
        ).replace(
            "local core = {SECTION = {rva = 4096, size = 34667155}}",
            "local core = dofile([[SCAN_CORE_PATH]])",
        ).replace(
            "__OBSERVER_ADAPTER__", prelude + source[wide_start:wide_end] + adapter
        ).replace(
            "__OBSERVER_TAKE_COMMAND__", ""
        )
        script = script.replace(
            "SCAN_CORE_PATH", (ROOT / "game" / "chat_probe_core.lua").as_posix()
        ).replace(
            "TRANSLATE_CORE_PATH", (ROOT / "game" / "chat_translate_core.lua").as_posix()
        )
        self.assertIn('RESULT = "adapter fake-kernel mocks ok"', script)
        # 将夹具和断言放在独立Lua prototype中，避免Lua 5.1的200-local限制
        # 与生产适配器定义共享同一主chunk。
        widget_fixture = WIDGET_ADAPTER_CHECKS.replace(
            'pack32(key) .. pack32(0) .. pack64(value)',
            'pack32(key) .. pack32(matching_indices[index] and 1 or 0) .. pack64(value)',
        )
        checks = "local function run_translate_checks()\n" + widget_fixture + "\n" + TRANSLATE_ADAPTER_CHECKS + "\nend\nrun_translate_checks()"
        script = script.replace('RESULT = "adapter fake-kernel mocks ok"', checks, 1)
        try:
            raw_result = self.lua.run(script)
        except RuntimeError as exc:
            line_match = re.search(r"\]:([0-9]+):", str(exc))
            if line_match:
                line_number = int(line_match.group(1))
                excerpt = "\n".join(
                    f"{index}: {script.splitlines()[index - 1]}"
                    for index in range(max(1, line_number - 3), min(len(script.splitlines()), line_number + 2) + 1)
                )
                raise RuntimeError(f"{exc}\nLua source near failure:\n{excerpt}") from exc
            raise
        result = json.loads(raw_result)
        self.assertEqual(result["status"], "translate fake Win32/kernel checks ok")
        self.assertEqual(result["apply_status"], "called_confirmed")
        self.assertEqual(result["native_calls"], 1, "confirmed apply must reach the fake setter exactly once")
        self.assertEqual(result["failed_race_status"], "disabled")
        self.assertEqual(result["stale_results"], ["stale", "stale", "stale", "stale", "stale"])
        self.assertLessEqual(result["report_bytes"], 64 * 1024)

        self.assertEqual(source.count("startup_report_retention.run"), 1)
        retention_start = source.index("--[[HD2_STARTUP_REPORT_RETENTION_BEGIN]]")
        retention_end = source.index("--[[HD2_STARTUP_REPORT_RETENTION_END]]", retention_start)
        retention_helper = source[retention_start:retention_end]
        retention_script = STARTUP_RETENTION_CHECKS.replace(
            "__FFI_DECL__", declaration.group(1)
        ).replace(
            "__RETENTION_HELPER__", retention_helper
        )
        try:
            retention_result = self.lua.run(retention_script)
        except RuntimeError as exc:
            line_match = re.search(r"\]:([0-9]+):", str(exc))
            if line_match:
                line_number = int(line_match.group(1))
                excerpt = "\n".join(
                    f"{index}: {retention_script.splitlines()[index - 1]}"
                    for index in range(max(1, line_number - 3), min(len(retention_script.splitlines()), line_number + 2) + 1)
                )
                raise RuntimeError(f"{exc}\nRetention Lua near failure:\n{excerpt}") from exc
            raise
        self.assertEqual(retention_result, "startup retention mock checks ok")




if __name__ == "__main__":
    unittest.main()
