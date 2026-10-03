"""聊天适配器 Lua fake-kernel 共享夹具。"""

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
local MAX_OBSERVER_READ = __MAX_OBSERVER_READ__
local observer_read_budget = 0
local observer_active_cycle = nil
local observer_ids = {}
local observer_id_count = 0
local observer_next_id = 0
local process = ffi.cast("void *", 1)
local invalid_handle = ffi.cast("HD2Probe_HANDLE", -1)
local regions = {}
local query_calls, read_calls = 0, 0
local query_mutation, read_mutation
local query_fail_address, rpm_fail_on_call, rpm_short_on_call
local read_counts = {}
local observer_now, observer_next_command_poll = 0, 0
local observer_request_path = ffi.new("HD2Probe_U16[32]")
local request_bytes = ""
local request_available, request_read_success, request_delete_success = true, true, true
local create_file_calls, file_read_calls, file_close_calls, delete_file_calls = 0, 0, 0, 0
local fake_file_handle = ffi.cast("HD2Probe_HANDLE", 0x1234)

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
    if query_fail_address == numeric_address then return 0 end
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
    if rpm_fail_on_call == read_calls then return 0 end
    local region = find_region(address)
    if not region or address + length > region.base + region.size then return 0 end
    local copied = rpm_short_on_call == read_calls and math.max(0, length - 1) or length
    ffi.copy(destination, region.data + (address - region.base), copied)
    bytes_read[0] = copied
    return 1
end

function kernel.CreateFileW(_, access, share_mode, _, creation, attributes, _)
    create_file_calls = create_file_calls + 1
    assert(access == 0x80000000 and share_mode == 0x3 and creation == 3 and attributes == 0x80)
    if not request_available then return invalid_handle end
    return fake_file_handle
end

function kernel.ReadFile(file, buffer, length, received, _)
    file_read_calls = file_read_calls + 1
    assert(file == fake_file_handle and length == 65)
    if not request_read_success then return 0 end
    local count = math.min(#request_bytes, length)
    if count > 0 then ffi.copy(buffer, request_bytes, count) end
    received[0] = count
    return 1
end

function kernel.CloseHandle(file)
    file_close_calls = file_close_calls + 1
    assert(file == fake_file_handle)
    return 1
end

function kernel.DeleteFileW(_)
    delete_file_calls = delete_file_calls + 1
    if request_delete_success then request_available = false; return 1 end
    return 0
end

local function reset_request(bytes, available, read_success, delete_success)
    observer_now, observer_next_command_poll = 0, 0
    request_bytes = bytes or ""
    request_available = available ~= false
    request_read_success = read_success ~= false
    request_delete_success = delete_success ~= false
    create_file_calls, file_read_calls, file_close_calls, delete_file_calls = 0, 0, 0, 0
end

local function reset_counters()
    query_calls, read_calls = 0, 0
    read_counts = {}
    query_mutation, read_mutation = nil, nil
    query_fail_address, rpm_fail_on_call, rpm_short_on_call = nil, nil, nil
end

__OBSERVER_ADAPTER__
__OBSERVER_TAKE_COMMAND__

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

-- 命令读取使用固定白名单，只在读取成功且文件删除成功后返回标签。
local function check_command(bytes, expected)
    reset_request(bytes, true, true, true)
    local value = observer_take_command()
    assert(value == expected, "unexpected command result")
    assert(create_file_calls == 1 and file_read_calls == 1 and file_close_calls == 1 and delete_file_calls == 1)
end
for _, label in ipairs({"startup", "chat_closed", "chat_open", "chat_sent"}) do
    check_command(label, label)
    check_command(label .. "\n", label)
    check_command(label .. "\r\n", label)
end
check_command("unknown_label", nil)
check_command(string.char(0x73, 0x74, 0x61, 0x72, 0x74, 0, 0x75), nil)
check_command("startup" .. string.char(0xe4, 0xb8, 0xad), nil)
check_command("chat_open\r\nstartup", nil)
check_command(string.rep("x", 64), nil)
check_command(string.rep("x", 65), nil)
check_command("", nil)

reset_request("startup", true, false, true)
assert(observer_take_command() == nil)
assert(create_file_calls == 1 and file_read_calls == 1 and file_close_calls == 1 and delete_file_calls == 0)
reset_request("chat_open", true, true, false)
assert(observer_take_command() == nil)
assert(create_file_calls == 1 and file_read_calls == 1 and file_close_calls == 1 and delete_file_calls == 1)

-- 250ms轮询间隔从上一次检查时刻起算，窗口前不重复打开文件。
reset_request("chat_sent", false, true, true)
assert(observer_take_command() == nil and create_file_calls == 1)
request_available = true
observer_now = 249
assert(observer_take_command() == nil and create_file_calls == 1)
observer_now = 250
assert(observer_take_command() == "chat_sent" and create_file_calls == 2)

-- 建立假的只读模块页与聊天对象；root指针和history首槽按64槽环绕。
local module_global_page = math.floor((tonumber(module_base) + 0x347CE28) / 4096) * 4096
local owner_global_region = add_region(module_global_page, 4096, tonumber(module_base), 0x1000000, 0x02)
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
local owner, dispatch, controller, controller_bad = 0x200000, 0x300000, 0x400000, 0x410000
local owner_region = add_region(owner, 0x10000, owner, 0x20000, 0x04)
local dispatch_region = add_region(dispatch, 0x10000, dispatch, 0x20000, 0x04)
add_region(controller, 4096, controller, 0x20000, 0x04)
add_region(controller_bad, 4096, controller_bad, 0x20000, 0x04)
local dispatch_global_page = math.floor((tonumber(module_base) + 0x3326E68) / 4096) * 4096
local dispatch_global_region = add_region(dispatch_global_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local vtable = tonumber(module_base) + 0x3000000
local vtable_page = math.floor(vtable / 4096) * 4096
add_region(vtable_page, 4096, tonumber(module_base), 0x1000000, 0x02)
local stack_address = owner + 0x429C
local dispatch_count = dispatch + 5740
local dispatch_rows = dispatch + 5744
local old_hex_dispatch_count = dispatch + 0x5740
local old_hex_dispatch_rows = dispatch + 0x5744
local stack = pack32(11) .. pack32(22) .. string.rep("\0", 12) .. pack32(2)
write_bytes(owner_global, pack64(owner))
write_bytes(stack_address, stack)
write_bytes(tonumber(module_base) + 0x3326E68, pack64(dispatch))
write_bytes(dispatch_count, pack32(4))
write_bytes(dispatch_rows, pack64(controller) .. pack32(0x1234) .. pack32(0)
    .. pack64(0) .. pack32(0x2345) .. pack32(0)
    .. pack64(1) .. pack32(0x3456) .. pack32(0)
    .. pack64(controller_bad) .. pack32(0x4567) .. pack32(0))
write_bytes(controller, pack64(vtable))
write_bytes(controller_bad, pack64(0x710000))
write_bytes(vtable, pack64(tonumber(module_base) + 0x1000) .. pack64(tonumber(module_base) + 0x2000) .. string.rep("\0", 48))
reset_counters(); observer_read_budget = 0
local snapshot = observer_ui_snapshot()
assert(snapshot and snapshot.screen_depth == 2 and snapshot.screen_ids[1] == 11 and snapshot.screen_ids[2] == 22,
    string.format("snapshot nil q=%d r=%d budget=%d", query_calls, read_calls, observer_read_budget))
assert(snapshot.controllers[1].kind == 0x1234 and snapshot.controllers[1].object_id ~= controller)
assert(snapshot.controllers[1].vtable_rva == 0x3000000)
assert(#snapshot.controllers[1].vtable_functions == 2)
assert(#snapshot.controllers == 2, "null and low-address rows should be skipped")
assert(snapshot.controllers[2].kind == 0x4567 and snapshot.controllers[2].object_id ~= controller_bad)
assert(snapshot.controllers[2].vtable_rva == nil and snapshot.controllers[2].vtable_functions == nil)
assert(observer_read_budget <= MAX_OBSERVER_READ, "mixed UI snapshot exceeded the read budget")

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
-- 每次诊断夹具都恢复只读模块页和本进程模拟页的状态。
local function prepare_ui_case()
    owner_global_region.protect = 0x02
    owner_global_region.type = 0x1000000
    owner_global_region.allocation_base = tonumber(module_base)
    dispatch_global_region.protect = 0x02
    dispatch_global_region.type = 0x1000000
    dispatch_global_region.allocation_base = tonumber(module_base)
    owner_region.protect = 0x04
    owner_region.type = 0x20000
    owner_region.allocation_base = owner
    dispatch_region.protect = 0x04
    dispatch_region.type = 0x20000
    dispatch_region.allocation_base = dispatch
    write_bytes(owner_global, pack64(owner))
    write_bytes(stack_address, stack)
    write_bytes(tonumber(module_base) + 0x3326E68, pack64(dispatch))
    write_bytes(dispatch_count, pack32(0))
    -- 旧错误偏移放入过界数量与异常行数据，正确布局不应访问这些陷阱。
    write_bytes(old_hex_dispatch_count, pack32(65))
    write_bytes(old_hex_dispatch_rows, string.rep(string.char(0xa5), 64))
    reset_counters()
    observer_read_budget = 0
end

local function expect_ui_failure(expected_stage, expected_reason, expected_read_size)
    local snapshot, diagnostic = observer_ui_snapshot()
    assert(snapshot == nil and type(diagnostic) == "table", "missing sanitized UI diagnostic")
    assert(diagnostic.version == 1 and diagnostic.stage == expected_stage and diagnostic.reason == expected_reason,
        "unexpected UI diagnostic stage/reason")
    assert(diagnostic.read_size == expected_read_size and diagnostic.read_size >= 0
        and diagnostic.read_size <= 4096, "diagnostic read_size escaped its bound")
    assert(diagnostic.budget_used >= 0 and diagnostic.budget_used <= MAX_OBSERVER_READ,
        "diagnostic budget_used escaped its bound")
    return diagnostic
end

-- 正确十进制 count 为零时，即使旧十六进制偏移藏有 count=65，也应读取为空表快照。
prepare_ui_case()
local trap_snapshot = observer_ui_snapshot()
assert(trap_snapshot and trap_snapshot.screen_depth == 2 and #trap_snapshot.controllers == 0,
    "legacy hexadecimal dispatch trap affected the snapshot")
assert(observer_read_budget == 88 and read_counts[dispatch_count] == 2,
    "zero-count snapshot used an unexpected read budget")
assert(read_counts[dispatch_rows] == nil and read_counts[old_hex_dispatch_count] == nil
    and read_counts[old_hex_dispatch_rows] == nil, "dispatch read touched legacy trap offsets or rows")

prepare_ui_case()
write_bytes(owner_global, pack64(0))
expect_ui_failure("owner_ptr", "null_pointer", 8)

prepare_ui_case()
write_bytes(tonumber(module_base) + 0x3326E68, pack64(0))
expect_ui_failure("dispatch_ptr", "null_pointer", 8)

prepare_ui_case()
owner_global_region.protect = 0x20
expect_ui_failure("owner_ptr", "protection_denied", 8)

prepare_ui_case()
owner_global_region.allocation_base = tonumber(module_base) + 0x1000
expect_ui_failure("owner_ptr", "allocation_denied", 8)

prepare_ui_case()
write_bytes(stack_address, pack32(1) .. pack32(2) .. pack32(3) .. pack32(4) .. pack32(5) .. pack32(6))
expect_ui_failure("screen_depth", "value_out_of_range", 24)

prepare_ui_case()
write_bytes(dispatch_count, pack32(65))
local over_count_diag = expect_ui_failure("dispatch_count", "value_out_of_range", 4)
assert(over_count_diag.observed_count == 65, "diagnostic omitted the out-of-range u32 count")
assert(over_count_diag.budget_used == 44 and observer_read_budget == 44,
    "over-count rejection changed the reader budget")
assert(read_counts[dispatch_count] == 1 and read_counts[dispatch_rows] == nil,
    "over-count path read dispatch rows")

prepare_ui_case()
write_bytes(dispatch_count, pack32(0xffffffff))
local max_count_diag = expect_ui_failure("dispatch_count", "value_out_of_range", 4)
assert(max_count_diag.observed_count == 4294967295, "diagnostic lost the maximum u32 count")
assert(max_count_diag.budget_used == 44 and observer_read_budget == 44,
    "maximum-count rejection changed the reader budget")
assert(read_counts[dispatch_count] == 1 and read_counts[dispatch_rows] == nil,
    "maximum-count path read dispatch rows")

prepare_ui_case()
read_mutation = function(address, count)
    if address == owner_global and count == 2 then write_bytes(owner_global, pack64(0x210000)) end
end
expect_ui_failure("verify_owner", "value_changed", 8)

prepare_ui_case()
read_mutation = function(address, count)
    if address == stack_address and count == 2 then
        write_bytes(stack_address, pack32(11) .. pack32(99) .. string.rep("\0", 12) .. pack32(2))
    end
end
expect_ui_failure("verify_stack", "value_changed", 24)

prepare_ui_case()
read_mutation = function(address, count)
    if address == tonumber(module_base) + 0x3326E68 and count == 2 then
        write_bytes(tonumber(module_base) + 0x3326E68, pack64(0x310000))
    end
end
expect_ui_failure("verify_dispatch", "value_changed", 8)

prepare_ui_case()
read_mutation = function(address, count)
    if address == dispatch_count and count == 2 then write_bytes(dispatch_count, pack32(1)) end
end
expect_ui_failure("verify_count", "value_changed", 4)

prepare_ui_case()
write_bytes(dispatch_count, pack32(1))
write_bytes(dispatch_rows, pack64(0) .. pack32(0x1234) .. pack32(0))
read_mutation = function(address, count)
    if address == dispatch_rows and count == 2 then
        write_bytes(dispatch_rows, pack64(0) .. pack32(0x5678) .. pack32(0))
    end
end
expect_ui_failure("verify_rows", "value_changed", 16)

prepare_ui_case()
query_fail_address = owner_global
expect_ui_failure("owner_ptr", "virtual_query_failed", 8)

prepare_ui_case()
rpm_fail_on_call = 1
expect_ui_failure("owner_ptr", "read_failed", 8)

prepare_ui_case()
rpm_short_on_call = 1
expect_ui_failure("owner_ptr", "short_read", 8)

prepare_ui_case()
observer_read_budget = MAX_OBSERVER_READ - 4
expect_ui_failure("owner_ptr", "budget_exhausted", 8)
assert(query_calls == 0 and read_calls == 0, "budget refusal reached the fake kernel")
RESULT = "adapter fake-kernel mocks ok"

'''

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
'''
