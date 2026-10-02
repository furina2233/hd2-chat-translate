"""聊天回写适配器的 LuaJIT fake-kernel mock 回归。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT
from test_chat_observe import LUA_OBSERVER_ADAPTER_HARNESS
from test_chat_widgets import WIDGET_ADAPTER_CHECKS


TRANSLATE_ADAPTER_CHECKS = r'''
local files, directories, handles = {}, {}, {}
local create_log, move_log, delete_log = {}, {}, {}
local next_file_handle = 0x700000
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
native_init_status = 0
translate_heartbeat_fresh = true
native_transport_api = {
    last_status = function() error("PRIVATE_NATIVE_STATUS") end,
    enabled = function() return true end,
    retry_cancels = function() error("PRIVATE_NATIVE_CANCEL") end,
    submit = function() return true end,
    response = function() return "OK\ntranslated" end,
}
local standalone_heartbeat = translate_adapter.heartbeat()
assert(standalone_heartbeat == "HD2CT1 4101\n" and not observer_faulted)
local retry_error_submit, retry_error_code = translate_adapter.submit(token, "Standalone body")
assert(retry_error_submit == false and retry_error_code == "SUBMIT_EXCEPTION" and not observer_faulted)
native_transport_api.retry_cancels = function() return 0 end
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
assert(translate_adapter.output(sanitized_input))
local sanitized = files[report_path].data
assert(not sanitized:find(private_message, 1, true))
assert(not sanitized:find("PRIVATE_", 1, true))
assert(sanitized:find('"status":"stopped"', 1, true))
assert(sanitized:find('"baseline_remaining":0', 1, true))
assert(sanitized:find('"pending_count":0', 1, true))
assert(sanitized:find('"slot_event_filtered":7', 1, true))
assert(sanitized:find('"error_displays_ready":2', 1, true))
assert(not sanitized:find('"private_counter"', 1, true))

-- 本native spy只模拟已知setter对目标属性entry的写入，绝不调用game.dll。
native_spy_calls = 0
prepare_widget_case({body = widget_body_bytes("你好，潜兵")})
observer_now = 4900
put_file(bridge_path, "HD2CT1 4900\n")
translate_adapter.heartbeat()
observer_read_budget = 0
local chinese_status, chinese_message = translate_adapter.read_slot(0)
assert(chinese_status == "ok" and chinese_message.body == "你好，潜兵")
assert(translate_adapter.apply(chinese_message, chinese_message.body) == "stale")
assert(native_spy_calls == 0, "unchanged Chinese source reached the native setter")

prepare_widget_case({body = widget_body_bytes("Hello, divers")})
observer_now = 5000
put_file(bridge_path, "HD2CT1 5000\n")
translate_adapter.heartbeat()
observer_read_budget = 0
local read_status, captured = translate_adapter.read_slot(0)
assert(read_status == "ok" and captured.body == "Hello, divers")
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

        wide_start = source.index("    local function append_wide_ascii(")
        wide_end = source.index("    local function prepare_observer_paths(", wide_start)
        adapter_start = source.index("    local function observer_query_address(address)")
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
local native_init_status = 0
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
        fixture_end = WIDGET_ADAPTER_CHECKS.index("local function expect_widget_status(")
        widget_fixture = WIDGET_ADAPTER_CHECKS[:fixture_end].replace(
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




if __name__ == "__main__":
    unittest.main()
