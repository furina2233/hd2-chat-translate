-- 只记录发送输入框的数值生命周期信息，不保留正文或进程地址。
local M = {}

M.DEFAULT_DURATION_MS = 5 * 60 * 1000
M.REPORT_INTERVAL_MS = 5 * 1000
M.MAX_EVENTS = 512
M.MAX_REPORT_BYTES = 64 * 1024
M.MAX_READ_BYTES_PER_PHASE = 4 * 1024

local MAX_SAFE_INTEGER = 9007199254740991

local function valid_integer(value, minimum, maximum)
    return type(value) == "number" and value == value
        and value ~= math.huge and value ~= -math.huge
        and value >= minimum and value <= maximum
        and value == math.floor(value)
end

local function bounded_integer(value, fallback, minimum, maximum)
    if not valid_integer(value, minimum, maximum) then return fallback end
    return value
end

local function new_array()
    return setmetatable({}, {__json_array = true})
end

local function clean_slots(value)
    local result = new_array()
    if type(value) ~= "table" then return result end
    for _, item in ipairs(value) do
        if #result >= 16 then break end
        if type(item) == "table"
            and valid_integer(item.slot, 0, 15)
            and valid_integer(item.rva, 0, MAX_SAFE_INTEGER) then
            result[#result + 1] = {slot = item.slot, rva = item.rva}
        end
    end
    return result
end

local function clean_sample(value)
    if type(value) ~= "table" then return nil end
    local slots = clean_slots(value.manager_slots)
    local input_slots = clean_slots(value.input_slots)
    return {
        generation = bounded_integer(value.generation, 0, 0, MAX_SAFE_INTEGER),
        root_changed = (value.root_changed == true or value.root_changed == 1) and 1 or 0,
        submit_flag = bounded_integer(value.submit_flag, -1, -1, 255),
        body_length = bounded_integer(value.body_length, -1, -2, 803),
        input_flags = bounded_integer(value.input_flags, -1, -1, 1099511627775),
        history_head = bounded_integer(value.history_head, -1, -1, 0xffffffff),
        history_count = bounded_integer(value.history_count, -1, -1, 0xffffffff),
        read_failures = bounded_integer(value.read_failures, 0, 0, 4096),
        read_bytes = bounded_integer(value.read_bytes, 0, 0, MAX_SAFE_INTEGER),
        manager_slots = slots,
        input_slots = input_slots,
    }
end

local function same_event_fields(left, right)
    if not left or not right then return false end
    return left.generation == right.generation
        and left.submit_flag == right.submit_flag
        and left.body_length == right.body_length
        and left.input_flags == right.input_flags
        and left.history_head == right.history_head
        and left.history_count == right.history_count
end

local function same_slots(left, right)
    if #left ~= #right then return false end
    for index = 1, #left do
        if left[index].slot ~= right[index].slot
            or left[index].rva ~= right[index].rva then return false end
    end
    return true
end

local function copy_counters(counters)
    local result = {}
    for key, value in pairs(counters) do result[key] = value end
    return result
end

function M.new(adapter, options)
    assert(type(adapter) == "table", "adapter required")
    assert(type(adapter.is_ready) == "function", "adapter.is_ready required")
    assert(type(adapter.now_ms) == "function", "adapter.now_ms required")
    assert(type(adapter.prepare_report) == "function", "adapter.prepare_report required")
    assert(type(adapter.sample) == "function", "adapter.sample required")
    assert(type(adapter.encode_json) == "function", "adapter.encode_json required")
    assert(type(adapter.write_report) == "function", "adapter.write_report required")
    options = options or {}
    assert(type(options) == "table", "options must be a table")
    local duration_ms = bounded_integer(
        options.duration_ms,
        M.DEFAULT_DURATION_MS,
        1,
        M.DEFAULT_DURATION_MS
    )
    local state = {
        started = false,
        done = false,
        status = "waiting_for_code_probe",
        reason = nil,
        started_ms = nil,
        deadline_ms = nil,
        last_report_ms = nil,
        dirty = false,
        frame = 0,
        last_pre = nil,
        last_phase = {},
        events = new_array(),
        manager_slots = new_array(),
        input_slots = new_array(),
        counters = {
            frames = 0,
            pre_samples = 0,
            post_samples = 0,
            pre_flag_count = 0,
            post_flag_count = 0,
            body_cleared_pairs = 0,
            root_changed = 0,
            read_failures = 0,
            max_read_bytes_per_phase = 0,
            events_dropped = 0,
        },
    }

    local function report()
        return {
            schema_version = 1,
            mode = "chat_outgoing_lifecycle_probe",
            status = state.status,
            reason = state.reason,
            started_ms = state.started_ms,
            deadline_ms = state.deadline_ms,
            counters = copy_counters(state.counters),
            events = state.events,
            manager_vtable_slots = state.manager_slots,
            input_vtable_slots = state.input_slots,
        }
    end

    local write_report

    local function stop(reason, now_ms)
        state.status = "stopped"
        state.reason = reason
        state.done = true
        state.dirty = true
        if state.started and now_ms ~= nil then
            local written, write_reason = write_report(now_ms, true)
            if not written then state.reason = write_reason end
        end
        return true
    end

    write_report = function(now_ms, force)
        if not force and (not state.dirty or now_ms - state.last_report_ms < M.REPORT_INTERVAL_MS) then
            return true
        end
        local current = report()
        local encode_ok, encoded = pcall(adapter.encode_json, current)
        if not encode_ok or type(encoded) ~= "string" then
            return false, "report_encode_failed"
        end
        local encode_attempts = 1
        while #encoded > M.MAX_REPORT_BYTES
            and #state.events > 1 and encode_attempts < 10 do
            local remove_count = math.floor((#state.events + 1) / 2)
            for _ = 1, remove_count do table.remove(state.events, 1) end
            state.counters.events_dropped = state.counters.events_dropped + remove_count
            state.dirty = true
            current = report()
            encode_ok, encoded = pcall(adapter.encode_json, current)
            if not encode_ok or type(encoded) ~= "string" then
                return false, "report_encode_failed"
            end
            encode_attempts = encode_attempts + 1
        end
        if #encoded > M.MAX_REPORT_BYTES then
            return false, "report_too_large"
        end
        local write_ok, written = pcall(adapter.write_report, current)
        if not write_ok or written == false then
            return false, "report_write_failed"
        end
        state.last_report_ms = now_ms
        state.dirty = false
        return true
    end

    local function finish(now_ms)
        state.status = "complete"
        state.reason = "deadline_reached"
        state.done = true
        state.dirty = true
        local written, write_reason = write_report(now_ms, true)
        if not written then
            state.status = "stopped"
            state.reason = write_reason
        end
        return true
    end

    local function append_event(phase, sample)
        if #state.events >= M.MAX_EVENTS then
            table.remove(state.events, 1)
            state.counters.events_dropped = state.counters.events_dropped + 1
            state.dirty = true
        end
        state.events[#state.events + 1] = {
            frame = state.frame,
            phase = phase,
            generation = sample.generation,
            submit_flag = sample.submit_flag,
            body_length = sample.body_length,
            input_flags = sample.input_flags,
            history_head = sample.history_head,
            history_count = sample.history_count,
        }
        state.dirty = true
    end

    local function record_sample(phase, sample)
        local counters = state.counters
        if phase == "addon_pre_update" then
            state.frame = state.frame + 1
            counters.frames = counters.frames + 1
            counters.pre_samples = counters.pre_samples + 1
            if sample.submit_flag == 1 then
                counters.pre_flag_count = counters.pre_flag_count + 1
            end
            state.last_pre = {
                frame = state.frame,
                snapshot = sample,
            }
        else
            if counters.frames == 0 then
                state.frame = 1
                counters.frames = 1
            end
            counters.post_samples = counters.post_samples + 1
            if sample.submit_flag == 1 then
                counters.post_flag_count = counters.post_flag_count + 1
            end
            if state.last_pre and state.last_pre.frame == state.frame then
                local before = state.last_pre.snapshot
                if before.submit_flag == 1 and sample.submit_flag == 0
                    and before.body_length > 0 and sample.body_length == 0 then
                    if before.generation == sample.generation then
                        counters.body_cleared_pairs = counters.body_cleared_pairs + 1
                    end
                end
            end
            state.last_pre = nil
        end
        if sample.root_changed == 1 then counters.root_changed = counters.root_changed + 1 end
        counters.read_failures = counters.read_failures + sample.read_failures
        if sample.read_bytes > counters.max_read_bytes_per_phase then
            counters.max_read_bytes_per_phase = sample.read_bytes
        end
        local previous = state.last_phase[phase]
        if not same_event_fields(previous, sample) then append_event(phase, sample) end
        state.last_phase[phase] = sample
        if not same_slots(state.manager_slots, sample.manager_slots) then
            state.manager_slots = sample.manager_slots
            state.dirty = true
        end
        if not same_slots(state.input_slots, sample.input_slots) then
            state.input_slots = sample.input_slots
            state.dirty = true
        end
        state.dirty = true
    end

    local function safe_now()
        local ok, value = pcall(adapter.now_ms)
        if not ok or not valid_integer(value, 0, MAX_SAFE_INTEGER) then return nil end
        return value
    end

    local function step(phase)
        if state.done then return true, report() end
        if phase ~= "addon_pre_update" and phase ~= "addon_post_update" then
            return stop("invalid_phase") , report()
        end
        if not state.started then
            local gate_ok, ready = pcall(adapter.is_ready)
            if not gate_ok then return stop("gate_failed"), report() end
            if ready ~= true then return false, report() end
            local start_time = safe_now()
            if start_time == nil then return stop("clock_failed"), report() end
            local prepare_ok, prepared = pcall(adapter.prepare_report)
            if not prepare_ok or prepared == false then
                return stop("report_setup_failed"), report()
            end
            state.started = true
            state.status = "running"
            state.reason = nil
            state.started_ms = start_time
            state.deadline_ms = start_time + duration_ms
            state.last_report_ms = start_time
            state.dirty = true
        end
        local now_ms = safe_now()
        if now_ms == nil then return stop("clock_failed", state.last_report_ms), report() end
        if now_ms >= state.deadline_ms then
            finish(now_ms)
            return true, report()
        end
        local sample_ok, raw_sample = pcall(adapter.sample, phase)
        if not sample_ok then return stop("sample_failed", now_ms), report() end
        local sample = clean_sample(raw_sample)
        if not sample then return stop("sample_failed", now_ms), report() end
        if sample.read_bytes > M.MAX_READ_BYTES_PER_PHASE then
            return stop("read_budget_exceeded", now_ms), report()
        end
        record_sample(phase, sample)
        local written, write_reason = write_report(now_ms, false)
        if not written then
            state.status = "stopped"
            state.reason = write_reason
            state.done = true
            return true, report()
        end
        return false, report()
    end

    local function safe_step(phase)
        local ok, done, snapshot = pcall(step, phase)
        if ok then return done, snapshot end
        local now_ms = safe_now() or state.last_report_ms
        stop("probe_failed", now_ms)
        return true, report()
    end

    local unpack_values = table.unpack or unpack
    local function pack_values(...)
        return {n = select("#", ...), ...}
    end

    local api = {
        step = safe_step,
        manifest = report,
        wrap_update = function(original_update)
            return function(...)
                if state.done then
                    if type(original_update) == "function" then return original_update(...) end
                    return
                end
                safe_step("addon_pre_update")
                if state.done then
                    if type(original_update) == "function" then return original_update(...) end
                    return
                end
                if type(original_update) ~= "function" then
                    safe_step("addon_post_update")
                    return
                end
                local results = pack_values(original_update(...))
                safe_step("addon_post_update")
                return unpack_values(results, 1, results.n)
            end
        end,
    }
    return api
end

return M
