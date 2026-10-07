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
M.SCAN_INTERVAL_MS = 1000
M.RESPONSE_POLL_MS = 200
M.REPORT_INTERVAL_MS = 5000

local MAX_SAFE_INTEGER = 9007199254740991
local SLOT_COUNT = 64
local MAX_RESPONSE_BYTES = 16387
local BASELINE_SCAN_INTERVAL_MS = 200

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

local function refresh_active_status(state)
    if not state.heartbeat_active then return end
    if state.baseline_active then
        set_status(state, "baseline", nil)
    elseif #state.pending > 0 then
        set_status(state, "pending", nil)
    else
        set_status(state, "ready", nil)
    end
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
    local interval_elapsed = state.last_report_ms == nil
        or (now_ms >= state.last_report_ms and now_ms - state.last_report_ms >= M.REPORT_INTERVAL_MS)
    if not force and not interval_elapsed then return end

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

local function clear_scan_plan(state)
    state.scan_plan_active = false
    state.scan_plan_slots = nil
    state.scan_plan_index = 1
end

local function baseline_scan_interval(state)
    return math.min(state.scan_interval_ms, BASELINE_SCAN_INTERVAL_MS)
end

local function reset_scan_owner(state, owner_id)
    clear_all_pending(state)
    state.seen = {}
    state.scan_owner_id = owner_id
    state.baseline_active = true
    state.baseline_remaining = SLOT_COUNT
    state.baseline_next_slot = 0
    state.next_slot = 0
    clear_scan_plan(state)
    state.next_scan_ms = add_saturated(state.last_now_ms or 0, baseline_scan_interval(state))
    set_status(state, "baseline", nil)
end

local function set_inactive(state, code)
    state.heartbeat_active = false
    state.heartbeat_uptime_ms = nil
    state.next_scan_ms = nil
    state.baseline_active = false
    state.baseline_remaining = SLOT_COUNT
    state.baseline_next_slot = 0
    clear_all_pending(state)
    state.seen = {}
    state.next_slot = 0
    state.scan_owner_id = nil
    clear_scan_plan(state)
    if not state.done then set_status(state, "inactive", code or "heartbeat_inactive") end
end

local function stop_state(state, code)
    if state.done then return end
    state.done = true
    state.heartbeat_active = false
    clear_all_pending(state)
    state.seen = {}
    set_status(state, "stopped", code)
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
        state.next_scan_ms = nil
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
    UNSUPPORTED_SERVICE = "暂不支持此翻译服务",
    AUTH_INVALID = "翻译凭据或签名无效",
    ACCESS_DENIED = "无权使用此翻译服务",
    QUOTA_EXCEEDED = "翻译额度不足",
    UNSUPPORTED_LANGUAGE = "不支持此语言",
    REQUEST_INVALID = "请求参数有误",
    SERVICE_ERROR = "翻译服务异常",
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
    local prefix = raw:sub(1, 3)
    if prefix == "OK\n" or prefix == "MT\n" then
        local text = raw:sub(4)
        if not M.valid_text(text, M.MAX_TRANSLATION_BYTES) then return "invalid" end
        return prefix == "MT\n" and "machine" or "ok", text
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
        if item.next_response_poll_ms ~= nil and now_ms < item.next_response_poll_ms then
            return
        end
        item.next_response_poll_ms = add_saturated(now_ms, state.response_poll_ms)
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
            elseif kind == "ok" and text == item.source_body then
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
    bump(state, "apply_attempts")
    -- 最多保留每个槽位的最近一次写回身份；扫描暂时失败或重连也不能重写同一消息。
    state.attempted_writes[item.message.widget_slot] = {
        owner_id = item.message.owner_id,
        event_slot = item.message.event_slot,
        body = item.message.body,
    }
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
        finish_item(state, item)
    elseif result == "deferred" then
        bump(state, "apply_deferred")
        -- 本条消息只尝试一次写回；预算不足也终止，不随游戏帧重试。
        finish_item(state, item)
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

local function normalize_scan_plan(plan)
    if type(plan) ~= "table" or not is_safe_integer(plan.owner_id) or plan.owner_id == 0
        or type(plan.slots) ~= "table"
        or (plan.reset_owner ~= nil and type(plan.reset_owner) ~= "boolean") then
        return nil
    end

    local count = 0
    for key in pairs(plan.slots) do
        if not is_small_integer(key, 1, SLOT_COUNT) then return nil end
        count = count + 1
    end
    if count > SLOT_COUNT then return nil end

    local slots = {}
    local seen = {}
    for index = 1, count do
        local slot = plan.slots[index]
        if not is_small_integer(slot, 0, SLOT_COUNT - 1) then return nil end
        if not seen[slot] then
            seen[slot] = true
            slots[#slots + 1] = slot
        end
    end
    return plan.owner_id, slots, plan.reset_owner == true
end

local function load_scan_plan(state)
    clear_scan_plan(state)
    if type(state.adapter.scan_plan) ~= "function" then return false end

    local ok, plan = pcall(state.adapter.scan_plan)
    if not ok then
        bump(state, "adapter_errors")
        return false
    end
    local owner_id, slots, reset_owner = normalize_scan_plan(plan)
    if not owner_id then
        bump(state, "invalid_messages")
        return false
    end

    if reset_owner or (state.scan_owner_id ~= nil and state.scan_owner_id ~= owner_id) then
        reset_scan_owner(state, owner_id)
        return false
    end
    state.scan_owner_id = owner_id
    state.scan_plan_slots = slots
    state.scan_plan_index = 1
    state.scan_plan_active = true
    return true
end

local function observe_scan_owner(state, owner_id)
    if state.scan_owner_id == nil then
        state.scan_owner_id = owner_id
        return true
    end
    if state.scan_owner_id == owner_id then return true end
    reset_scan_owner(state, owner_id)
    return false
end

local function scan_slots(state)
    local submitted_this_step = 0
    local slots_read = 0
    while slots_read < M.MAX_SLOTS_PER_STEP and not state.done do
        local planned = state.scan_plan_active
        if planned and (#state.pending >= M.MAX_PENDING
            or submitted_this_step >= M.MAX_SUBMITS_PER_STEP) then
            break
        end
        local slot
        if planned then
            if state.scan_plan_index > #state.scan_plan_slots then break end
            slot = state.scan_plan_slots[state.scan_plan_index]
        else
            slot = state.next_slot
        end
        local active, now_ms = ensure_active(state, false)
        if not active then return end
        local status, message = read_one_slot(state, slot)
        if status == "deferred" then
            bump(state, "slot_deferred")
            return
        end

        if planned then
            state.scan_plan_index = state.scan_plan_index + 1
        else
            state.next_slot = (slot + 1) % SLOT_COUNT
        end
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
            if not observe_scan_owner(state, message.owner_id) then return end
            if not same_identity(state.attempted_writes[slot], message) then
                state.attempted_writes[slot] = nil
            end
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
                if same_identity(state.seen[slot], message)
                    or same_identity(state.attempted_writes[slot], message) then
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
    if state.scan_plan_active and state.scan_plan_index > #state.scan_plan_slots then
        clear_scan_plan(state)
        local completed_ms = state.last_now_ms or 0
        if not is_safe_integer(state.next_scan_ms) or completed_ms >= state.next_scan_ms then
            state.next_scan_ms = add_saturated(completed_ms, state.scan_interval_ms)
        end
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
            if not observe_scan_owner(state, message.owner_id) then return end
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
    local scan_interval_ms = options.scan_interval_ms
    if not is_small_integer(scan_interval_ms, 0, 1000) then
        scan_interval_ms = M.SCAN_INTERVAL_MS
    end
    local response_poll_ms = options.response_poll_ms
    if not is_small_integer(response_poll_ms, 0, 60000) then
        response_poll_ms = M.RESPONSE_POLL_MS
    end
    local state = {
        adapter = adapter,
        counters = counters,
        scan_interval_ms = scan_interval_ms,
        response_poll_ms = response_poll_ms,
        next_scan_ms = nil,
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
        session_id = nil,
        token_counter = 0,
        pending = {},
        pending_by_slot = {},
        pending_cursor = 1,
        seen = {},
        attempted_writes = {},
        next_slot = 0,
        scan_owner_id = nil,
        scan_plan_slots = nil,
        scan_plan_index = 1,
        scan_plan_active = false,
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

function M.should_step(state, now_ms, previous_dispatch_ms)
    if not is_safe_integer(now_ms) then return false, "invalid_clock" end
    if previous_dispatch_ms ~= nil then
        if not is_safe_integer(previous_dispatch_ms) then return false, "invalid_clock" end
        if now_ms < previous_dispatch_ms then return false, "clock_reversed" end
    end
    if type(state) ~= "table" then return true end
    if state.last_now_ms ~= nil then
        if not is_safe_integer(state.last_now_ms) then return false, "invalid_clock" end
        if now_ms < state.last_now_ms then return false, "clock_reversed" end
    end
    if state.done or type(state.pending) ~= "table" or #state.pending > 0 then return true end
    if not is_safe_integer(state.last_report_ms) then return true end

    if state.scan_plan_active and type(state.scan_plan_slots) == "table"
        and is_small_integer(state.scan_plan_index, 1, SLOT_COUNT + 1)
        and state.scan_plan_index <= #state.scan_plan_slots then
        return true
    end

    local deadline = add_saturated(state.last_report_ms, M.REPORT_INTERVAL_MS)
    if not is_safe_integer(state.last_heartbeat_poll_ms) then return true end
    local heartbeat_deadline = add_saturated(state.last_heartbeat_poll_ms, M.HEARTBEAT_POLL_MS)
    if heartbeat_deadline < deadline then deadline = heartbeat_deadline end

    if state.heartbeat_active then
        if not is_safe_integer(state.next_scan_ms) then return true end
        if state.next_scan_ms < deadline then deadline = state.next_scan_ms end
        if not is_safe_integer(state.heartbeat_uptime_ms) then return true end
        local freshness_deadline = add_saturated(state.heartbeat_uptime_ms, M.HEARTBEAT_FRESH_MS)
        if freshness_deadline < deadline then deadline = freshness_deadline end
    end

    return now_ms >= deadline
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

    local now_ms = state.last_now_ms or 0
    local plan_pending = state.scan_plan_active and type(state.scan_plan_slots) == "table"
        and is_small_integer(state.scan_plan_index, 1, SLOT_COUNT + 1)
        and state.scan_plan_index <= #state.scan_plan_slots
    if not plan_pending and state.next_scan_ms ~= nil and now_ms < state.next_scan_ms then
        refresh_active_status(state)
        maybe_report(state, now_ms, false)
        return false
    end

    if state.baseline_active then
        state.next_scan_ms = add_saturated(now_ms, baseline_scan_interval(state))
        scan_baseline(state)
        if state.done then
            maybe_report(state, state.last_now_ms or 0, true)
            return true
        end
        maybe_report(state, state.last_now_ms or 0, false)
        return false
    end

    if not plan_pending then
        state.next_scan_ms = add_saturated(now_ms, state.scan_interval_ms)
        load_scan_plan(state)
        if state.baseline_active then
            state.next_scan_ms = add_saturated(state.last_now_ms or now_ms, baseline_scan_interval(state))
            scan_baseline(state)
            if state.done then
                maybe_report(state, state.last_now_ms or 0, true)
                return true
            end
            maybe_report(state, state.last_now_ms or 0, false)
            return false
        end
    end

    scan_slots(state)
    if state.done then
        maybe_report(state, state.last_now_ms or 0, true)
        return true
    end
    refresh_active_status(state)
    maybe_report(state, state.last_now_ms or 0, false)
    return false
end

function M.wrap_update_after(original_update, probe_step)
    local finished = false
    local function after_original(...)
        if not finished then
            local ok, done = pcall(probe_step)
            if not ok or done == true then finished = true end
        end
        return ...
    end
    return function(...)
        if type(original_update) == "function" then
            return after_original(original_update(...))
        end
        return after_original()
    end
end

return M
