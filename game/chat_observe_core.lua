-- 只读聊天正文与界面结构观察核心；不调用游戏函数、不保存原始正文或身份字节。
local M = {}

M.SLOTS_PER_STEP = 8
M.CYCLE_INTERVAL_MS = 500
M.REPORT_INTERVAL_MS = 5000
M.MAX_RUNTIME_MS = 30 * 60 * 1000
M.MAX_UI_SNAPSHOTS = 32
M.MAX_CONTROLLERS = 64
M.MAX_SCREEN_DEPTH = 5
M.MAX_VTABLE_FUNCTIONS = 8

local SLOT_COUNT = 64
local BODY_BYTES = 513
local IDENTITY_BYTES = 16
local FLAGS_BYTES = 8
local MAX_SAFE_INTEGER = 9007199254740991
local MAX_U32 = 4294967295
local MAX_IMAGE_RVA = 74727423
local CODE_SECTION_START = 0x1000
local CODE_SECTION_END = CODE_SECTION_START + 34667155
local TEST_ASCII = "HD2CT_PROBE_ASCII_01"
local TEST_CJK = "HD2CT_PROBE_中文_02"

local COMMANDS = {
    startup = true,
    chat_closed = true,
    chat_open = true,
    chat_sent = true,
}

local UI_DIAGNOSTIC_STAGES = {
    owner_ptr = true,
    stack_read = true,
    screen_depth = true,
    screen_id_decode = true,
    dispatch_ptr = true,
    count_read = true,
    dispatch_count = true,
    rows_read = true,
    row_decode = true,
    verify_owner = true,
    verify_stack = true,
    verify_dispatch = true,
    verify_count = true,
    verify_rows = true,
    sanitize = true,
    callback_error = true,
}

local UI_DIAGNOSTIC_REASONS = {
    null_pointer = true,
    pointer_range = true,
    invalid_address = true,
    invalid_length = true,
    budget_exhausted = true,
    virtual_query_failed = true,
    malformed_region = true,
    not_committed = true,
    protection_denied = true,
    allocation_denied = true,
    region_bounds = true,
    allocation_changed = true,
    region_changed = true,
    read_failed = true,
    short_read = true,
    value_out_of_range = true,
    value_changed = true,
    malformed_bytes = true,
    callback_error = true,
}

local function new_array()
    return setmetatable({}, {__json_array = true})
end

local function is_integer(value)
    return type(value) == "number" and value == value and value ~= math.huge
        and value ~= -math.huge and value >= 0 and value <= MAX_SAFE_INTEGER
        and value == math.floor(value)
end

local function is_u32(value)
    return is_integer(value) and value <= MAX_U32
end

local function add_saturated(value, amount)
    if amount <= 0 then return value end
    if value >= MAX_SAFE_INTEGER - amount then return MAX_SAFE_INTEGER end
    return value + amount
end

local function bump(state, name, amount)
    state.counters[name] = add_saturated(state.counters[name] or 0, amount or 1)
end

local function safe_call(state, name, ...)
    local callback = state.adapter[name]
    if type(callback) ~= "function" then
        bump(state, "adapter_errors")
        return false, nil, nil
    end
    local ok, result, second = pcall(callback, ...)
    if not ok then
        bump(state, "adapter_errors")
        return false, nil, nil
    end
    return true, result, second
end

local function valid_metadata(value)
    return type(value) == "table" and is_integer(value.owner_id)
        and is_integer(value.first) and value.first < SLOT_COUNT
        and is_integer(value.count) and value.count <= SLOT_COUNT
end

local function same_metadata(left, right)
    return valid_metadata(right) and left.owner_id == right.owner_id
        and left.first == right.first and left.count == right.count
end

local function valid_record(value)
    return type(value) == "table"
        and type(value.identity) == "string" and #value.identity == IDENTITY_BYTES
        and type(value.body) == "string" and #value.body == BODY_BYTES
        and type(value.flags) == "string" and #value.flags == FLAGS_BYTES
end

local function continuation(byte)
    return byte ~= nil and byte >= 0x80 and byte <= 0xbf
end

local function valid_utf8(value)
    local index = 1
    local length = #value
    while index <= length do
        local first = value:byte(index)
        if first <= 0x7f then
            index = index + 1
        elseif first >= 0xc2 and first <= 0xdf then
            if not continuation(value:byte(index + 1)) then return false end
            index = index + 2
        elseif first == 0xe0 then
            local second, third = value:byte(index + 1, index + 2)
            if not second or second < 0xa0 or second > 0xbf or not continuation(third) then return false end
            index = index + 3
        elseif (first >= 0xe1 and first <= 0xec) or (first >= 0xee and first <= 0xef) then
            local second, third = value:byte(index + 1, index + 2)
            if not continuation(second) or not continuation(third) then return false end
            index = index + 3
        elseif first == 0xed then
            local second, third = value:byte(index + 1, index + 2)
            if not second or second < 0x80 or second > 0x9f or not continuation(third) then return false end
            index = index + 3
        elseif first == 0xf0 then
            local second, third, fourth = value:byte(index + 1, index + 3)
            if not second or second < 0x90 or second > 0xbf
                or not continuation(third) or not continuation(fourth) then return false end
            index = index + 4
        elseif first >= 0xf1 and first <= 0xf3 then
            local second, third, fourth = value:byte(index + 1, index + 3)
            if not continuation(second) or not continuation(third) or not continuation(fourth) then return false end
            index = index + 4
        elseif first == 0xf4 then
            local second, third, fourth = value:byte(index + 1, index + 3)
            if not second or second < 0x80 or second > 0x8f
                or not continuation(third) or not continuation(fourth) then return false end
            index = index + 4
        else
            return false
        end
    end
    return true
end

local function dense_array_length(value, limit)
    if type(value) ~= "table" then return nil end
    local count, maximum = 0, 0
    for key in pairs(value) do
        if not is_integer(key) or key < 1 then return nil end
        count = count + 1
        if key > maximum then maximum = key end
        if count > limit or maximum > limit then return nil end
    end
    if count ~= maximum then return nil end
    return count
end

local function sanitize_snapshot(value, label, now_ms)
    if type(value) ~= "table" then return nil end
    local depth = value.screen_depth
    if not is_integer(depth) or depth > M.MAX_SCREEN_DEPTH then return nil end
    local screen_count = dense_array_length(value.screen_ids, M.MAX_SCREEN_DEPTH)
    if screen_count == nil or screen_count ~= depth then return nil end
    local controller_count = dense_array_length(value.controllers, M.MAX_CONTROLLERS)
    if controller_count == nil then return nil end

    local screen_ids = new_array()
    for index = 1, screen_count do
        local screen_id = value.screen_ids[index]
        if not is_u32(screen_id) then return nil end
        screen_ids[index] = screen_id
    end

    local controllers = new_array()
    for index = 1, controller_count do
        local controller = value.controllers[index]
        if type(controller) ~= "table" then return nil end
        local kind = controller.kind
        if not is_u32(kind) or not is_integer(controller.object_id) then return nil end
        local item = {
            kind = kind,
            object_id = controller.object_id,
        }
        if controller.vtable_rva ~= nil then
            if is_integer(controller.vtable_rva) and controller.vtable_rva <= MAX_IMAGE_RVA then
                item.vtable_rva = controller.vtable_rva
            end
        end
        if controller.vtable_functions ~= nil then
            local function_count = dense_array_length(controller.vtable_functions, 64)
            if function_count ~= nil then
                local functions = new_array()
                for function_index = 1, math.min(function_count, M.MAX_VTABLE_FUNCTIONS) do
                    local rva = controller.vtable_functions[function_index]
                    if is_integer(rva) and rva >= CODE_SECTION_START and rva < CODE_SECTION_END then
                        functions[#functions + 1] = rva
                    end
                end
                if #functions > 0 then item.vtable_functions = functions end
            end
        end
        controllers[index] = item
    end

    return {
        label = label,
        at_ms = now_ms,
        screen_depth = depth,
        screen_ids = screen_ids,
        controllers = controllers,
    }
end

local function sanitize_ui_diagnostic(value, label, now_ms)
    local clean = {
        version = 1,
        label = COMMANDS[label] and label or "startup",
        at_ms = is_integer(now_ms) and now_ms or 0,
    }
    local valid = type(value) == "table"
    if valid then
        valid = value.version == 1
            and UI_DIAGNOSTIC_STAGES[value.stage] == true
            and UI_DIAGNOSTIC_REASONS[value.reason] == true
    end

    local read_size = type(value) == "table" and value.read_size or nil
    local budget_used = type(value) == "table" and value.budget_used or nil
    local valid_read_size = is_integer(read_size) and read_size <= 4096
    local valid_budget = is_integer(budget_used) and budget_used <= 16 * 1024
    if not valid_read_size or not valid_budget then valid = false end

    if valid then
        clean.stage = value.stage
        clean.reason = value.reason
    else
        clean.stage = "sanitize"
        clean.reason = "malformed_bytes"
    end
    if valid_read_size then clean.read_size = read_size end
    if valid_budget then clean.budget_used = budget_used end
    return clean
end

local function record_ui_diagnostic(state, label, now_ms, value)
    local ok, clean = pcall(sanitize_ui_diagnostic, value, label, now_ms)
    if not ok then
        clean = {
            version = 1,
            label = COMMANDS[label] and label or "startup",
            at_ms = is_integer(now_ms) and now_ms or 0,
            stage = "sanitize",
            reason = "malformed_bytes",
        }
    end
    if #state.ui_diagnostics < M.MAX_UI_SNAPSHOTS then
        state.ui_diagnostics[#state.ui_diagnostics + 1] = clean
    else
        state.ui_diagnostics_dropped = add_saturated(state.ui_diagnostics_dropped, 1)
    end
end

local function stop_observer(state, reason)
    if state.active_cycle then
        state.active_cycle = nil
        bump(state, "cycles_discarded")
        bump(state, "discarded_stopped")
    end
    state.done = true
    state.status = "observer_stopped"
    state.stop_reason = reason
end

local function discard_cycle(state, reason)
    if not state.active_cycle then return end
    state.active_cycle = nil
    bump(state, "cycles_discarded")
    bump(state, "discarded_" .. reason)
end

local function capture_snapshot(state, label, now_ms)
    if state.snapshot_attempts >= M.MAX_UI_SNAPSHOTS then
        bump(state, "snapshots_dropped")
        return false
    end
    state.snapshot_attempts = state.snapshot_attempts + 1
    local ok, raw, diagnostic = safe_call(state, "ui_snapshot")
    if not ok then
        bump(state, "snapshot_failures")
        record_ui_diagnostic(state, label, now_ms, {
            version = 1,
            stage = "callback_error",
            reason = "callback_error",
            read_size = 0,
            budget_used = 0,
        })
        return false
    end
    if raw == nil then
        bump(state, "snapshot_failures")
        record_ui_diagnostic(state, label, now_ms, diagnostic)
        return false
    end
    local clean = sanitize_snapshot(raw, label, now_ms)
    if not clean then
        bump(state, "snapshot_failures")
        record_ui_diagnostic(state, label, now_ms, {
            version = 1,
            stage = "sanitize",
            reason = "malformed_bytes",
            read_size = 0,
            budget_used = 0,
        })
        return false
    end
    state.ui_snapshots[#state.ui_snapshots + 1] = clean
    state.snapshot_count = state.snapshot_count + 1
    return true
end

local function start_cycle(state, now_ms)
    bump(state, "cycle_attempts")
    state.next_cycle_ms = add_saturated(now_ms, M.CYCLE_INTERVAL_MS)
    local ok, metadata = safe_call(state, "begin_cycle")
    if not ok then
        bump(state, "cycle_begin_failures")
        return
    end
    if metadata == nil then
        bump(state, "empty_cycle_starts")
        return
    end
    if not valid_metadata(metadata) then
        bump(state, "invalid_cycle_metadata")
        bump(state, "cycles_discarded")
        bump(state, "discarded_invalid_metadata")
        return
    end
    state.active_cycle = {
        owner_id = metadata.owner_id,
        first = metadata.first,
        count = metadata.count,
        next_index = 0,
        body_bytes = 0,
        body_min = nil,
        body_max = 0,
        ascii_matches = 0,
        cjk_matches = 0,
    }
end

local function inspect_slot(state, cycle, slot)
    local first_ok, first = safe_call(state, "read_slot", slot)
    if not first_ok or first == nil then
        bump(state, "entry_read_failures")
        discard_cycle(state, "read_failure")
        return false
    end
    if not valid_record(first) then
        bump(state, "invalid_record_shapes")
        discard_cycle(state, "invalid_record")
        return false
    end
    local first_identity = first.identity
    local first_body = first.body
    local first_flags = first.flags
    first = nil

    local second_ok, second = safe_call(state, "read_slot", slot)
    if not second_ok or second == nil then
        bump(state, "entry_read_failures")
        discard_cycle(state, "read_failure")
        return false
    end
    if not valid_record(second) then
        bump(state, "invalid_record_shapes")
        discard_cycle(state, "invalid_record")
        return false
    end
    if first_identity ~= second.identity or first_body ~= second.body or first_flags ~= second.flags then
        bump(state, "unstable_entries")
        discard_cycle(state, "unstable_entry")
        return false
    end

    local terminator = first_body:find("\0", 1, true)
    if not terminator then
        bump(state, "missing_body_terminators")
        discard_cycle(state, "invalid_body")
        return false
    end
    local body_length = terminator - 1
    local body = first_body:sub(1, body_length)
    if not valid_utf8(body) then
        bump(state, "invalid_utf8_entries")
        discard_cycle(state, "invalid_body")
        return false
    end

    cycle.body_bytes = cycle.body_bytes + body_length
    cycle.body_min = cycle.body_min and math.min(cycle.body_min, body_length) or body_length
    cycle.body_max = math.max(cycle.body_max, body_length)
    if body == TEST_ASCII then cycle.ascii_matches = cycle.ascii_matches + 1 end
    if body == TEST_CJK then cycle.cjk_matches = cycle.cjk_matches + 1 end
    cycle.next_index = cycle.next_index + 1
    return true
end

local function finish_cycle(state, cycle)
    local ok, metadata = safe_call(state, "finish_cycle")
    if not ok then
        bump(state, "cycle_finish_failures")
        discard_cycle(state, "finish_failure")
        return
    end
    if not same_metadata(cycle, metadata) then
        bump(state, "metadata_changes")
        discard_cycle(state, "metadata_change")
        return
    end

    state.active_cycle = nil
    bump(state, "cycles_completed")
    bump(state, "entries_seen", cycle.count)
    bump(state, "body_bytes_total", cycle.body_bytes)
    bump(state, "ascii_matches", cycle.ascii_matches)
    bump(state, "cjk_matches", cycle.cjk_matches)
    if cycle.body_min ~= nil then
        state.body_bytes_min = state.body_bytes_min and math.min(state.body_bytes_min, cycle.body_min)
            or cycle.body_min
        state.body_bytes_max = math.max(state.body_bytes_max, cycle.body_max)
    end
    state.last_owner_anon_id = cycle.owner_id
    state.seen_ascii = state.counters.ascii_matches > 0
    state.seen_cjk = state.counters.cjk_matches > 0
end

local function advance_cycle(state)
    local cycle = state.active_cycle
    if not cycle then return end
    local slots_read = 0
    while state.active_cycle and cycle.next_index < cycle.count and slots_read < M.SLOTS_PER_STEP do
        local slot = (cycle.first + cycle.next_index) % SLOT_COUNT
        if not inspect_slot(state, cycle, slot) then return end
        slots_read = slots_read + 1
    end
    if state.active_cycle and cycle.next_index == cycle.count then
        finish_cycle(state, cycle)
    end
end

local function process_command(state, now_ms)
    local ok, command = safe_call(state, "take_command")
    if not ok then return false end
    if command == nil then return false end
    if type(command) ~= "string" or not COMMANDS[command] then
        bump(state, "invalid_commands")
        return false
    end
    capture_snapshot(state, command, now_ms)
    return true
end

function M.new(adapter)
    assert(type(adapter) == "table", "adapter required")
    for _, name in ipairs({
        "begin_cycle", "read_slot", "finish_cycle", "ui_snapshot", "take_command", "now_ms", "output",
    }) do
        assert(type(adapter[name]) == "function", name .. " required")
    end
    return {
        adapter = adapter,
        done = false,
        status = "observing",
        stop_reason = nil,
        started_ms = nil,
        last_now_ms = nil,
        next_cycle_ms = nil,
        last_output_ms = nil,
        last_reported_ascii = false,
        last_reported_cjk = false,
        startup_captured = false,
        active_cycle = nil,
        body_bytes_min = nil,
        body_bytes_max = 0,
        seen_ascii = false,
        seen_cjk = false,
        last_owner_anon_id = nil,
        ui_snapshots = new_array(),
        ui_diagnostics = new_array(),
        ui_diagnostics_dropped = 0,
        snapshot_count = 0,
        snapshot_attempts = 0,
        last_manifest = nil,
        counters = {
            cycle_attempts = 0,
            cycles_completed = 0,
            empty_cycle_starts = 0,
            cycle_begin_failures = 0,
            cycle_finish_failures = 0,
            invalid_cycle_metadata = 0,
            cycles_discarded = 0,
            discarded_stopped = 0,
            discarded_invalid_metadata = 0,
            discarded_read_failure = 0,
            discarded_invalid_record = 0,
            discarded_unstable_entry = 0,
            discarded_invalid_body = 0,
            discarded_finish_failure = 0,
            discarded_metadata_change = 0,
            metadata_changes = 0,
            entry_read_failures = 0,
            invalid_record_shapes = 0,
            unstable_entries = 0,
            missing_body_terminators = 0,
            invalid_utf8_entries = 0,
            entries_seen = 0,
            body_bytes_total = 0,
            ascii_matches = 0,
            cjk_matches = 0,
            snapshot_failures = 0,
            snapshots_dropped = 0,
            invalid_commands = 0,
            adapter_errors = 0,
            output_failures = 0,
            clock_failures = 0,
        },
    }
end

function M.manifest(state)
    local elapsed = 0
    if state.started_ms ~= nil and state.last_now_ms ~= nil and state.last_now_ms >= state.started_ms then
        elapsed = state.last_now_ms - state.started_ms
    end
    return {
        schema_version = 1,
        status = state.status,
        stop_reason = state.stop_reason,
        elapsed_ms = elapsed,
        cycles_completed = state.counters.cycles_completed or 0,
        cycles_discarded = state.counters.cycles_discarded or 0,
        cycle_attempts = state.counters.cycle_attempts or 0,
        empty_cycle_starts = state.counters.empty_cycle_starts or 0,
        cycle_begin_failures = state.counters.cycle_begin_failures or 0,
        cycle_finish_failures = state.counters.cycle_finish_failures or 0,
        invalid_cycle_metadata = state.counters.invalid_cycle_metadata or 0,
        discarded_stopped = state.counters.discarded_stopped or 0,
        discarded_invalid_metadata = state.counters.discarded_invalid_metadata or 0,
        discarded_read_failure = state.counters.discarded_read_failure or 0,
        discarded_invalid_record = state.counters.discarded_invalid_record or 0,
        discarded_unstable_entry = state.counters.discarded_unstable_entry or 0,
        discarded_invalid_body = state.counters.discarded_invalid_body or 0,
        discarded_finish_failure = state.counters.discarded_finish_failure or 0,
        discarded_metadata_change = state.counters.discarded_metadata_change or 0,
        metadata_changes = state.counters.metadata_changes or 0,
        entry_read_failures = state.counters.entry_read_failures or 0,
        invalid_record_shapes = state.counters.invalid_record_shapes or 0,
        unstable_entries = state.counters.unstable_entries or 0,
        missing_body_terminators = state.counters.missing_body_terminators or 0,
        invalid_utf8_entries = state.counters.invalid_utf8_entries or 0,
        entries_seen = state.counters.entries_seen or 0,
        body_bytes_total = state.counters.body_bytes_total or 0,
        body_bytes_min = state.body_bytes_min or 0,
        body_bytes_max = state.body_bytes_max or 0,
        owner_anon_id = state.last_owner_anon_id,
        seen_ascii = state.seen_ascii,
        seen_cjk = state.seen_cjk,
        ascii_matches = state.counters.ascii_matches or 0,
        cjk_matches = state.counters.cjk_matches or 0,
        snapshot_count = state.snapshot_count,
        snapshot_attempts = state.snapshot_attempts,
        snapshot_failures = state.counters.snapshot_failures or 0,
        snapshots_dropped = state.counters.snapshots_dropped or 0,
        adapter_errors = state.counters.adapter_errors or 0,
        output_failures = state.counters.output_failures or 0,
        invalid_commands = state.counters.invalid_commands or 0,
        clock_failures = state.counters.clock_failures or 0,
        ui_snapshots = state.ui_snapshots,
        ui_diagnostics = state.ui_diagnostics,
        ui_diagnostics_dropped = state.ui_diagnostics_dropped,
    }
end

local function report_if_due(state, now_ms, force)
    local seen_changed = state.seen_ascii ~= state.last_reported_ascii
        or state.seen_cjk ~= state.last_reported_cjk
    local elapsed = now_ms and state.last_output_ms and now_ms >= state.last_output_ms
        and now_ms - state.last_output_ms or nil
    local due = state.last_output_ms == nil or (elapsed ~= nil and elapsed >= M.REPORT_INTERVAL_MS)
    if not (force or seen_changed or due) then return end

    state.last_reported_ascii = state.seen_ascii
    state.last_reported_cjk = state.seen_cjk
    if now_ms ~= nil then state.last_output_ms = now_ms end
    local manifest = M.manifest(state)
    state.last_manifest = manifest
    local ok = pcall(state.adapter.output, manifest)
    if not ok then
        bump(state, "adapter_errors")
        bump(state, "output_failures")
    end
end

function M.step(state)
    if not state.done then
        local now_ok, now_ms = safe_call(state, "now_ms")
        if not now_ok or not is_integer(now_ms) then
            bump(state, "clock_failures")
            stop_observer(state, "clock_error")
            report_if_due(state, state.last_now_ms, true)
        elseif state.last_now_ms ~= nil and now_ms < state.last_now_ms then
            bump(state, "clock_failures")
            stop_observer(state, "clock_not_monotonic")
            report_if_due(state, state.last_now_ms, true)
        else
            state.last_now_ms = now_ms
            if state.started_ms == nil then
                state.started_ms = now_ms
                state.next_cycle_ms = now_ms
            end

            local force_output = false
            if now_ms - state.started_ms >= M.MAX_RUNTIME_MS then
                stop_observer(state, "runtime_limit")
                force_output = true
            else
                if not state.startup_captured then
                    state.startup_captured = true
                    capture_snapshot(state, "startup", now_ms)
                    force_output = true
                end
                if process_command(state, now_ms) then force_output = true end
                if not state.active_cycle and now_ms >= state.next_cycle_ms then
                    start_cycle(state, now_ms)
                end
                advance_cycle(state)
            end
            report_if_due(state, now_ms, force_output or state.done)
        end
    end
    return state.done, state.last_manifest
end

return M
