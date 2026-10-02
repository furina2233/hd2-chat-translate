-- 纯翻译队列状态机；游戏内存、文件邮箱与原生调用全部由受限适配器负责。
local M = {}

M.MAX_SOURCE_BYTES = 1023
M.MAX_TRANSLATION_BYTES = 16384
M.DISPLAY_SEPARATOR = "\n译文："
M.MAX_DISPLAY_BYTES = M.MAX_SOURCE_BYTES + #M.DISPLAY_SEPARATOR + M.MAX_TRANSLATION_BYTES
M.MAX_PENDING = 32
M.MAX_SLOTS_PER_STEP = 4
M.MAX_SUBMITS_PER_STEP = 1
M.PENDING_TTL_MS = 60000
M.HEARTBEAT_FRESH_MS = 3000
M.HEARTBEAT_POLL_MS = 200
M.REPORT_INTERVAL_MS = 5000

local MAX_SAFE_INTEGER = 9007199254740991
local SLOT_COUNT = 64
local MAX_RESPONSE_BYTES = 16387

local STATUSES = {
    target_unverified = true,
    inactive = true,
    baseline = true,
    ready = true,
    pending = true,
    applying = true,
    stopped = true,
}

local STATUS_CODES = {
    target_unverified = true,
    invalid_session = true,
    adapter_missing = true,
    clock_error = true,
    invalid_clock = true,
    clock_reversed = true,
    heartbeat_inactive = true,
    heartbeat_error = true,
    heartbeat_invalid = true,
    heartbeat_stale = true,
    token_exhausted = true,
}

local COUNTER_NAMES = {
    "steps",
    "active_steps",
    "heartbeat_checks",
    "heartbeat_misses",
    "heartbeat_errors",
    "heartbeat_invalid",
    "heartbeat_stale",
    "slot_reads",
    "slot_empty",
    "slot_stale",
    "slot_event_filtered",
    "slot_read_failed",
    "slot_deferred",
    "invalid_messages",
    "duplicates",
    "pending_observations",
    "queue_full",
    "submit_throttled",
    "submit_attempts",
    "submit_failures",
    "submitted",
    "cancelled",
    "cancel_errors",
    "responses_waiting",
    "response_errors",
    "response_invalid",
    "translation_errors",
    "translation_unchanged",
    "translations_ready",
    "error_displays_ready",
    "expired",
    "apply_attempts",
    "apply_confirmed",
    "apply_unconfirmed",
    "apply_stale",
    "apply_deferred",
    "apply_capacity",
    "apply_read_failed",
    "apply_disabled",
    "apply_errors",
    "adapter_errors",
    "output_errors",
}

local function is_safe_integer(value)
    return type(value) == "number" and value == value
        and value ~= math.huge and value ~= -math.huge
        and value >= 0 and value <= MAX_SAFE_INTEGER
        and value == math.floor(value)
end

local function is_small_integer(value, minimum, maximum)
    return is_safe_integer(value) and value >= minimum and value <= maximum
end

local function add_saturated(value, amount)
    if amount <= 0 then return value end
    if value >= MAX_SAFE_INTEGER - amount then return MAX_SAFE_INTEGER end
    return value + amount
end

local function bump(state, name, amount)
    if state.counters[name] == nil then return end
    state.counters[name] = add_saturated(state.counters[name], amount or 1)
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

function M.valid_text(value, max_bytes)
    if type(value) ~= "string" or type(max_bytes) ~= "number"
        or max_bytes ~= max_bytes or max_bytes == math.huge or max_bytes == -math.huge
        or max_bytes < 1 or max_bytes ~= math.floor(max_bytes) then
        return false
    end
    local length = #value
    if length == 0 or length > max_bytes or value:find("\0", 1, true) then return false end
    return valid_utf8(value)
end

local function valid_session_id(value)
    if type(value) ~= "string" or #value < 1 or #value > 80 then return false end
    for index = 1, #value do
        local byte = value:byte(index)
        local alpha = (byte >= 0x41 and byte <= 0x5a) or (byte >= 0x61 and byte <= 0x7a)
        local digit = byte >= 0x30 and byte <= 0x39
        if not alpha and not digit and byte ~= 0x5f and byte ~= 0x2d then return false end
    end
    return true
end

local function required_callbacks_present(adapter)
    local names = {"now_ms", "heartbeat", "read_slot", "submit", "response", "apply", "cancel"}
    for _, name in ipairs(names) do
        if type(adapter[name]) ~= "function" then return false end
    end
    return true
end

local function set_status(state, status, code)
    if not STATUSES[status] then status = "stopped" end
    if code ~= nil and not STATUS_CODES[code] then code = nil end
    state.status = status
    state.status_code = code
end

function M.manifest(state)
    local counters = {}
    for _, name in ipairs(COUNTER_NAMES) do
        counters[name] = state.counters[name] or 0
    end
    return {
        schema_version = 1,
        status = STATUSES[state.status] and state.status or "stopped",
        code = STATUS_CODES[state.status_code] and state.status_code or nil,
        done = state.done == true,
        heartbeat_active = state.heartbeat_active == true,
        baseline_remaining = math.max(0, math.min(SLOT_COUNT, state.baseline_remaining or 0)),
        pending_count = #state.pending,
        counters = counters,
    }
end

local function maybe_report(state, now_ms, force)
    local changed = state.last_report_status ~= state.status
        or state.last_report_code ~= state.status_code
    local interval_elapsed = state.last_report_ms == nil
        or (now_ms >= state.last_report_ms and now_ms - state.last_report_ms >= M.REPORT_INTERVAL_MS)
    if not force and not changed and not interval_elapsed then return end

    state.last_report_status = state.status
    state.last_report_code = state.status_code
    state.last_report_ms = now_ms
    if type(state.adapter.output) ~= "function" then return end
    local ok = pcall(state.adapter.output, M.manifest(state))
    if not ok then bump(state, "output_errors") end
end

local function same_identity(left, right)
    return type(left) == "table" and type(right) == "table"
        and left.owner_id == right.owner_id
        and left.event_slot == right.event_slot
        and left.body == right.body
end

local function valid_message(message, requested_slot)
    return type(message) == "table"
        and message.widget_slot == requested_slot
        and is_small_integer(message.widget_slot, 0, SLOT_COUNT - 1)
        and is_small_integer(message.event_slot, 0, SLOT_COUNT - 1)
        and is_safe_integer(message.owner_id) and message.owner_id > 0
        and M.valid_text(message.body, M.MAX_SOURCE_BYTES)
        and type(message.proof) == "table"
end

local function parse_heartbeat(raw)
    if type(raw) ~= "string" or #raw > 64 then return nil end
    local digits = raw:match("^HD2CT1 ([0-9]+)\n$")
    if not digits then return nil end
    local value = 0
    for index = 1, #digits do
        local digit = digits:byte(index) - 0x30
        if value > (MAX_SAFE_INTEGER - digit) / 10 then return nil end
        value = value * 10 + digit
    end
    return value
end

local function remove_pending(state, item)
    local index = nil
    for current = 1, #state.pending do
        if state.pending[current] == item then
            index = current
            break
        end
    end
    if not index then return false end
    table.remove(state.pending, index)
    if state.pending_by_slot[item.message.widget_slot] == item then
        state.pending_by_slot[item.message.widget_slot] = nil
    end
    if #state.pending == 0 then
        state.pending_cursor = 1
    elseif index < state.pending_cursor then
        state.pending_cursor = state.pending_cursor - 1
    elseif state.pending_cursor > #state.pending then
        state.pending_cursor = 1
    end
    return true
end

local function cancel_item(state, item)
    if item.cancel_called then return end
    item.cancel_called = true
    local ok = pcall(state.adapter.cancel, item.token)
    if ok then
        bump(state, "cancelled")
    else
        bump(state, "cancel_errors")
        bump(state, "adapter_errors")
    end
end

local function finish_item(state, item)
    if remove_pending(state, item) then cancel_item(state, item) end
end

local function cancel_slot(state, slot)
    local item = state.pending_by_slot[slot]
    if item then finish_item(state, item) end
end

local function clear_all_pending(state)
    while #state.pending > 0 do
        local item = state.pending[#state.pending]
        remove_pending(state, item)
        cancel_item(state, item)
    end
end

local function set_inactive(state, code)
    state.heartbeat_active = false
    state.heartbeat_uptime_ms = nil
    state.baseline_active = false
    state.baseline_remaining = SLOT_COUNT
    state.baseline_next_slot = 0
    clear_all_pending(state)
    state.seen = {}
    state.next_slot = 0
    if not state.done then set_status(state, "inactive", code or "heartbeat_inactive") end
end

local function stop_state(state, code, now_ms)
    if state.done then return end
    state.done = true
    state.heartbeat_active = false
    clear_all_pending(state)
    state.seen = {}
    set_status(state, "stopped", code)
    maybe_report(state, now_ms or state.last_now_ms or 0, true)
end

local function read_now(state)
    local ok, value = pcall(state.adapter.now_ms)
    if not ok then
        bump(state, "adapter_errors")
        return nil, "clock_error"
    end
    if not is_safe_integer(value) then return nil, "invalid_clock" end
    if state.last_now_ms ~= nil and value < state.last_now_ms then return nil, "clock_reversed" end
    state.last_now_ms = value
    return value
end

local function cached_heartbeat_is_fresh(state, now_ms)
    local stamp = state.heartbeat_uptime_ms
    if not is_safe_integer(stamp) or now_ms < stamp then return false end
    return now_ms - stamp <= M.HEARTBEAT_FRESH_MS
end

local function poll_heartbeat(state, now_ms)
    bump(state, "heartbeat_checks")
    state.last_heartbeat_poll_ms = now_ms
    local ok, raw = pcall(state.adapter.heartbeat)
    if not ok then
        bump(state, "heartbeat_errors")
        bump(state, "adapter_errors")
        set_inactive(state, "heartbeat_error")
        return false
    end
    -- 时间戳与文件读取并发发布时，以读取完成后的单调时钟为准。
    local completed_ms, clock_error = read_now(state)
    if completed_ms == nil then
        stop_state(state, clock_error)
        return false
    end
    state.last_heartbeat_poll_ms = completed_ms
    if raw == nil then
        bump(state, "heartbeat_misses")
        set_inactive(state, "heartbeat_inactive")
        return false
    end
    local stamp = parse_heartbeat(raw)
    if stamp == nil then
        bump(state, "heartbeat_invalid")
        set_inactive(state, "heartbeat_invalid")
        return false
    end
    if stamp > completed_ms or completed_ms - stamp > M.HEARTBEAT_FRESH_MS then
        bump(state, "heartbeat_stale")
        set_inactive(state, "heartbeat_stale")
        return false
    end
    local was_active = state.heartbeat_active
    state.heartbeat_uptime_ms = stamp
    state.heartbeat_active = true
    if not was_active then
        state.baseline_active = true
        state.baseline_remaining = SLOT_COUNT
        state.baseline_next_slot = 0
    end
    if state.baseline_active then
        set_status(state, "baseline", nil)
    elseif #state.pending > 0 then
        set_status(state, "pending", nil)
    else
        set_status(state, "ready", nil)
    end
    return true
end

local function ensure_active(state, force_poll)
    local now_ms, clock_error = read_now(state)
    if now_ms == nil then
        stop_state(state, clock_error)
        return false, nil
    end
    local poll_due = state.last_heartbeat_poll_ms == nil
        or now_ms - state.last_heartbeat_poll_ms >= M.HEARTBEAT_POLL_MS
    if force_poll or poll_due then
        if not poll_heartbeat(state, now_ms) then return false, now_ms end
        now_ms = state.last_now_ms or now_ms
    elseif not cached_heartbeat_is_fresh(state, now_ms) then
        bump(state, "heartbeat_stale")
        set_inactive(state, "heartbeat_stale")
        return false, now_ms
    end
    return state.heartbeat_active == true, now_ms
end

local function make_token(state)
    if state.token_counter >= MAX_SAFE_INTEGER then return nil end
    state.token_counter = state.token_counter + 1
    local token = state.session_id .. "_" .. string.format("%.0f", state.token_counter)
    if #token > 128 then return nil end
    return token
end

local function add_pending(state, item)
    state.pending[#state.pending + 1] = item
    state.pending_by_slot[item.message.widget_slot] = item
end

local function mark_seen(state, message)
    state.seen[message.widget_slot] = {
        owner_id = message.owner_id,
        event_slot = message.event_slot,
        body = message.body,
    }
end

local function remember_submit_error(state, message, token, code)
    local item = {
        token = token,
        message = message,
        source_body = message.body,
        created_ms = state.last_now_ms,
        translation = nil,
        display_text = nil,
        cancel_called = true,
        owns_token = false,
    }
    item.display_text = item.source_body .. M.DISPLAY_SEPARATOR .. M.error_message(code)
    item.error_display = true
    add_pending(state, item)
    mark_seen(state, message)
    bump(state, "error_displays_ready")
end

local function submit_message(state, message)
    local token = make_token(state)
    if not token then
        stop_state(state, "token_exhausted")
        return false
    end
    bump(state, "submit_attempts")
    local ok, submitted, error_code = pcall(state.adapter.submit, token, message.body)
    if not ok then
        bump(state, "adapter_errors")
        bump(state, "submit_failures")
        remember_submit_error(state, message, token, "SUBMIT_EXCEPTION")
        return false
    end
    if submitted ~= true then
        bump(state, "submit_failures")
        remember_submit_error(state, message, token,
            error_code == "SUBMIT_EXCEPTION" and "SUBMIT_EXCEPTION" or "SUBMIT_FAILED")
        return false
    end
    add_pending(state, {
        token = token,
        message = message,
        source_body = message.body,
        created_ms = state.last_now_ms,
        translation = nil,
        display_text = nil,
        cancel_called = false,
    })
    mark_seen(state, message)
    bump(state, "submitted")
    return true
end

local ERROR_MESSAGES = {
    TIMEOUT = "请求超时",
    EXPIRED = "请求超时",
    NETWORK = "网络连接失败",
    BAD_RESPONSE = "返回内容无效",
    RESPONSE_TOO_LARGE = "返回内容过长",
    RATE_LIMITED = "请求太频繁，请稍后再试",
    BACKOFF = "请稍后重试",
    INVALID_URL = "接口地址无效",
    INVALID_CONFIG = "模型配置无效",
    INTERNAL = "翻译服务异常",
    RESPONSE_EXCEPTION = "翻译服务异常",
    CANCELLED = "请求已取消",
    SUBMIT_FAILED = "翻译请求未能提交",
    SUBMIT_EXCEPTION = "翻译服务异常",
}

local function error_message(code)
    if type(code) == "string" then
        local message = ERROR_MESSAGES[code]
        if message then return message end
        local digits = code:match("^HTTP_(%d%d%d)$")
        local status = digits and tonumber(digits) or nil
        if status and status >= 100 and status <= 599 then
            if status == 400 or status == 422 then return "请求参数有误" end
            if status == 401 then return "API 密钥无效" end
            if status == 403 then return "无权使用此接口" end
            if status == 404 then return "接口或模型不存在" end
            if status == 408 or status == 504 then return "请求超时" end
            if status == 429 then return "请求太频繁，请稍后再试" end
            if status >= 500 then return "服务暂时不可用" end
            return "请求失败"
        end
    end
    return "翻译失败，请稍后重试"
end

function M.error_message(code)
    return error_message(code)
end

local function set_error_display(state, item, code)
    item.display_text = item.source_body .. M.DISPLAY_SEPARATOR .. error_message(code)
    item.error_display = true
    bump(state, "error_displays_ready")
end

local function parse_response(raw)
    if type(raw) ~= "string" or #raw > MAX_RESPONSE_BYTES then return "invalid" end
    if raw:sub(1, 3) == "OK\n" then
        local text = raw:sub(4)
        if not M.valid_text(text, M.MAX_TRANSLATION_BYTES) then return "invalid" end
        return "ok", text
    end
    if raw:sub(1, 4) == "ERR\n" and #raw > 4 then return "error", raw:sub(5) end
    return "invalid"
end

local function next_pending(state)
    local count = #state.pending
    if count == 0 then return nil end
    if state.pending_cursor < 1 or state.pending_cursor > count then state.pending_cursor = 1 end
    local item = state.pending[state.pending_cursor]
    state.pending_cursor = state.pending_cursor + 1
    if state.pending_cursor > count then state.pending_cursor = 1 end
    return item
end

local function terminal_response_failure(state, item, counter)
    bump(state, counter)
    finish_item(state, item)
end

local function process_one_pending(state)
    local item = next_pending(state)
    if not item then return end

    local now_ms = state.last_now_ms or 0
    if now_ms >= item.created_ms and now_ms - item.created_ms >= M.PENDING_TTL_MS then
        bump(state, "expired")
        finish_item(state, item)
        return
    end

    if item.display_text == nil then
        local ok, raw = pcall(state.adapter.response, item.token)
        if not ok then
            bump(state, "adapter_errors")
            bump(state, "response_errors")
            set_error_display(state, item, "RESPONSE_EXCEPTION")
        elseif raw == nil then
            bump(state, "responses_waiting")
            return
        else
            local kind, text = parse_response(raw)
            if kind == "invalid" then
                bump(state, "response_invalid")
                set_error_display(state, item, "BAD_RESPONSE")
            elseif kind == "error" then
                bump(state, "translation_errors")
                set_error_display(state, item, text)
            elseif text == item.source_body then
                terminal_response_failure(state, item, "translation_unchanged")
                return
            else
                item.translation = text
                item.display_text = item.source_body .. M.DISPLAY_SEPARATOR .. text
                bump(state, "translations_ready")
            end
        end
    end

    local active, fresh_now = ensure_active(state, true)
    if not active then return end
    now_ms = fresh_now or state.last_now_ms or 0
    if now_ms >= item.created_ms and now_ms - item.created_ms >= M.PENDING_TTL_MS then
        bump(state, "expired")
        finish_item(state, item)
        return
    end

    set_status(state, "applying", nil)
    maybe_report(state, now_ms, false)
    bump(state, "apply_attempts")
    local ok, result = pcall(state.adapter.apply, item.message, item.display_text)
    if not ok then
        bump(state, "adapter_errors")
        terminal_response_failure(state, item, "apply_errors")
        return
    end

    if result == "called_confirmed" then
        bump(state, "apply_confirmed")
        finish_item(state, item)
    elseif result == "called_unconfirmed" then
        bump(state, "apply_unconfirmed")
        finish_item(state, item)
    elseif result == "stale" then
        bump(state, "apply_stale")
        state.seen[item.message.widget_slot] = nil
        finish_item(state, item)
    elseif result == "deferred" then
        bump(state, "apply_deferred")
    elseif result == "capacity" then
        bump(state, "apply_capacity")
        finish_item(state, item)
    elseif result == "read_failed" then
        bump(state, "apply_read_failed")
        finish_item(state, item)
    elseif result == "disabled" then
        bump(state, "apply_disabled")
        set_inactive(state, "heartbeat_inactive")
    else
        bump(state, "apply_errors")
        finish_item(state, item)
    end
end

local function read_one_slot(state, slot)
    bump(state, "slot_reads")
    local ok, status, message = pcall(state.adapter.read_slot, slot)
    if not ok then
        bump(state, "adapter_errors")
        status = "read_failed"
        message = nil
    end
    return status, message
end

local function handle_non_ok_slot(state, slot, status)
    cancel_slot(state, slot)
    state.seen[slot] = nil
    if status == "empty" then
        bump(state, "slot_empty")
    elseif status == "stale" then
        bump(state, "slot_stale")
    elseif status == "filtered_event" then
        -- 计数表示被拒绝的槽位采样次数，不代表去重后的通知数量。
        bump(state, "slot_event_filtered")
    elseif status == "deferred" then
        bump(state, "slot_deferred")
    else
        bump(state, "slot_read_failed")
    end
end

local function scan_slots(state)
    local submitted_this_step = 0
    local slots_read = 0
    while slots_read < M.MAX_SLOTS_PER_STEP and not state.done do
        local active, now_ms = ensure_active(state, false)
        if not active then return end
        local slot = state.next_slot
        local status, message = read_one_slot(state, slot)
        if status == "deferred" then
            bump(state, "slot_deferred")
            return
        end

        state.next_slot = (slot + 1) % SLOT_COUNT
        slots_read = slots_read + 1
        if status ~= "ok" then
            if status ~= "empty" and status ~= "stale" and status ~= "read_failed"
                and status ~= "filtered_event" then
                bump(state, "invalid_messages")
                status = "read_failed"
            end
            handle_non_ok_slot(state, slot, status)
        elseif not valid_message(message, slot) then
            bump(state, "invalid_messages")
            cancel_slot(state, slot)
            state.seen[slot] = nil
        else
            local pending = state.pending_by_slot[slot]
            local identity = {
                owner_id = message.owner_id,
                event_slot = message.event_slot,
                body = message.body,
            }
            if pending and same_identity(pending.message, message) then
                bump(state, "pending_observations")
            else
                if pending then
                    finish_item(state, pending)
                    state.seen[slot] = nil
                end
                if same_identity(state.seen[slot], message) then
                    bump(state, "duplicates")
                elseif #state.pending >= M.MAX_PENDING then
                    bump(state, "queue_full")
                elseif submitted_this_step >= M.MAX_SUBMITS_PER_STEP then
                    bump(state, "submit_throttled")
                else
                    local still_active = ensure_active(state, false)
                    if not still_active then return end
                    submitted_this_step = submitted_this_step + 1
                    submit_message(state, message)
                end
            end
        end
        if now_ms == nil then return end
    end
end

local function scan_baseline(state)
    local slots_read = 0
    while state.baseline_active and slots_read < M.MAX_SLOTS_PER_STEP and not state.done do
        local active = ensure_active(state, false)
        if not active then return end
        local slot = state.baseline_next_slot
        local status, message = read_one_slot(state, slot)
        if status == "deferred" then
            bump(state, "slot_deferred")
            return
        end

        state.baseline_next_slot = (slot + 1) % SLOT_COUNT
        state.baseline_remaining = math.max(0, state.baseline_remaining - 1)
        slots_read = slots_read + 1
        if status == "ok" and valid_message(message, slot) then
            state.seen[slot] = {
                owner_id = message.owner_id,
                event_slot = message.event_slot,
                body = message.body,
            }
        else
            if status == "ok" then
                bump(state, "invalid_messages")
                status = "read_failed"
            elseif status ~= "empty" and status ~= "stale" and status ~= "read_failed"
                and status ~= "filtered_event" then
                bump(state, "invalid_messages")
                status = "read_failed"
            end
            handle_non_ok_slot(state, slot, status)
        end
    end

    if state.baseline_remaining == 0 then
        state.baseline_active = false
        set_status(state, "ready", nil)
    else
        set_status(state, "baseline", nil)
    end
end

function M.new(adapter, options)
    adapter = type(adapter) == "table" and adapter or {}
    options = type(options) == "table" and options or {}
    local counters = {}
    for _, name in ipairs(COUNTER_NAMES) do counters[name] = 0 end
    local state = {
        adapter = adapter,
        counters = counters,
        status = "inactive",
        status_code = "heartbeat_inactive",
        done = false,
        heartbeat_active = false,
        baseline_active = false,
        baseline_remaining = SLOT_COUNT,
        baseline_next_slot = 0,
        heartbeat_uptime_ms = nil,
        last_heartbeat_poll_ms = nil,
        last_now_ms = nil,
        last_report_ms = nil,
        last_report_status = nil,
        last_report_code = nil,
        session_id = nil,
        token_counter = 0,
        pending = {},
        pending_by_slot = {},
        pending_cursor = 1,
        seen = {},
        next_slot = 0,
    }

    if options.target_verified ~= true then
        state.done = true
        set_status(state, "target_unverified", "target_unverified")
    elseif not valid_session_id(options.session_id) then
        state.done = true
        set_status(state, "stopped", "invalid_session")
    elseif not required_callbacks_present(adapter) then
        state.done = true
        set_status(state, "stopped", "adapter_missing")
    else
        state.session_id = options.session_id
    end
    return state
end

function M.step(state)
    if type(state) ~= "table" then return true end
    if state.done then
        maybe_report(state, state.last_now_ms or 0, false)
        return true
    end

    bump(state, "steps")
    local active = ensure_active(state, false)
    if state.done then
        maybe_report(state, state.last_now_ms or 0, true)
        return true
    end
    if not active then
        maybe_report(state, state.last_now_ms or 0, false)
        return false
    end

    bump(state, "active_steps")
    process_one_pending(state)
    if state.done then
        maybe_report(state, state.last_now_ms or 0, true)
        return true
    end
    if not state.heartbeat_active then
        maybe_report(state, state.last_now_ms or 0, false)
        return false
    end

    if state.baseline_active then
        scan_baseline(state)
        if state.done then
            maybe_report(state, state.last_now_ms or 0, true)
            return true
        end
        maybe_report(state, state.last_now_ms or 0, false)
        return false
    end

    scan_slots(state)
    if state.done then
        maybe_report(state, state.last_now_ms or 0, true)
        return true
    end
    if state.heartbeat_active then
        if #state.pending > 0 then
            set_status(state, "pending", nil)
        else
            set_status(state, "ready", nil)
        end
    end
    maybe_report(state, state.last_now_ms or 0, false)
    return false
end

return M
