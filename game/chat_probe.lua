-- HD2聊天研究模块：包含代码扫描、只读观察、固定显示测试与本机翻译桥接模式。
local core = (function()
--[[HD2_CHAT_PROBE_CORE]]
end)()

local target_language_settings = (function()
--[[HD2CT_TARGET_LANGUAGE_SETTINGS]]
end)()
local target_language_settings_step
if type(target_language_settings) == "table"
    and type(target_language_settings.new) == "function" then
    local settings_ok, settings_step = pcall(target_language_settings.new)
    if settings_ok and type(settings_step) == "function" then
        target_language_settings_step = settings_step
    end
end
local target_language_settings_clock

--[[HD2_STARTUP_REPORT_RETENTION_BEGIN]]
local startup_report_retention = (function()
    local FILE_ATTRIBUTE_DIRECTORY = 0x10
    local FILE_ATTRIBUTE_DEVICE = 0x40
    local FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    local INVALID_FILE_ATTRIBUTES = 0xffffffff
    local ERROR_FILE_NOT_FOUND = 2
    local ERROR_PATH_NOT_FOUND = 3
    local ERROR_NO_MORE_FILES = 18
    local groups = {
        {directory = "probe", pattern = "^chat%-probe%-%d+%-%x+%-%x+%-%d+%.json$", reserve = "always"},
        {directory = "observe", pattern = "^chat%-observe%-%d+%-%x+%.json$", reserve = "observe"},
        {directory = "mailbox", pattern = "^chat%-translate%-hd2ct_%d+_%x+%.json$", reserve = "translate"},
    }

    local function append_wide_ascii(ffi, base, base_length, suffix)
        if type(base_length) ~= "number" or base_length < 1 or base_length ~= math.floor(base_length)
            or type(suffix) ~= "string" or suffix:find("[^%w_%.%-%*\\]")
            or base_length + #suffix + 1 > 32768 then return nil end
        local output = ffi.new("HD2Probe_U16[?]", base_length + #suffix + 1)
        ffi.copy(output, base, base_length * 2)
        for index = 1, #suffix do output[base_length + index - 1] = suffix:byte(index) end
        output[base_length + #suffix] = 0
        return output
    end

    local function wide_ascii_name(pointer)
        local bytes = {}
        for index = 0, 259 do
            local code = tonumber(pointer[index])
            if code == 0 then return table.concat(bytes) end
            if not code or code < 0x20 or code > 0x7e then return nil end
            bytes[#bytes + 1] = string.char(code)
        end
        return nil
    end

    local function matching_name(group, name)
        if type(name) ~= "string" or name == "" or name:find("/", 1, true)
            or name:find("\\", 1, true) or name:find("..", 1, true) then return false end
        return name:match(group.pattern) ~= nil
    end

    local function file_attributes(ffi, kernel, path)
        local value = tonumber(kernel.GetFileAttributesW(path))
        if not value or value == INVALID_FILE_ATTRIBUTES or value == -1 then
            return nil, tonumber(kernel.GetLastError())
        end
        return value
    end

    local function ordinary_file(attributes)
        return attributes and attributes ~= INVALID_FILE_ATTRIBUTES
            and math.floor(attributes / FILE_ATTRIBUTE_DIRECTORY) % 2 == 0
            and math.floor(attributes / FILE_ATTRIBUTE_REPARSE_POINT) % 2 == 0
            and math.floor(attributes / FILE_ATTRIBUTE_DEVICE) % 2 == 0
    end

    local function safe_directory(attributes)
        return attributes and attributes ~= INVALID_FILE_ATTRIBUTES
            and math.floor(attributes / FILE_ATTRIBUTE_DIRECTORY) % 2 == 1
            and math.floor(attributes / FILE_ATTRIBUTE_REPARSE_POINT) % 2 == 0
    end

    local function missing_path(error_code)
        return error_code == ERROR_FILE_NOT_FOUND or error_code == ERROR_PATH_NOT_FOUND
    end

    local function retain_group(ffi, kernel, app_root, root_length, group, keep, summary)
        local directory = append_wide_ascii(ffi, app_root, root_length, "\\" .. group.directory)
        if not directory then return "skipped" end
        local directory_attributes, directory_error = file_attributes(ffi, kernel, directory)
        if not directory_attributes then return missing_path(directory_error) and "absent" or "skipped" end
        if not safe_directory(directory_attributes) then return "skipped" end

        local search = append_wide_ascii(ffi, directory, root_length + #group.directory + 1, "\\*")
        if not search then return "skipped" end
        local find_data = ffi.new("HD2Probe_WIN32_FIND_DATAW[1]")
        local find_ok, handle = pcall(kernel.FindFirstFileW, search, find_data)
        if not find_ok then return "skipped" end
        local invalid_handle = ffi.cast("HD2Probe_HANDLE", -1)
        if handle == nil or handle == ffi.NULL or handle == invalid_handle then
            local error_ok, error_code = pcall(kernel.GetLastError)
            if error_ok and tonumber(error_code) == ERROR_FILE_NOT_FOUND then return "checked" end
            return "skipped"
        end

        local enumeration_ok, complete, entries = pcall(function()
            local found = {}
            local function capture_current()
                local name = wide_ascii_name(find_data[0].cFileName)
                if not matching_name(group, name) then return true end
                local enumerated_attributes = tonumber(find_data[0].dwFileAttributes)
                if not ordinary_file(enumerated_attributes) then return true end
                local path = append_wide_ascii(ffi, directory, root_length + #group.directory + 1,
                    "\\" .. name)
                if not path then return false end
                local current_attributes = file_attributes(ffi, kernel, path)
                if not current_attributes then return false end
                if ordinary_file(current_attributes) then
                    found[#found + 1] = {
                        name = name,
                        high = tonumber(find_data[0].ftLastWriteTime.dwHighDateTime),
                        low = tonumber(find_data[0].ftLastWriteTime.dwLowDateTime),
                    }
                end
                return true
            end

            if not capture_current() then return false, nil end
            while true do
                local next_result = kernel.FindNextFileW(handle, find_data)
                if next_result == 0 then
                    return tonumber(kernel.GetLastError()) == ERROR_NO_MORE_FILES, found
                end
                if not capture_current() then return false, nil end
            end
        end)
        local close_ok, close_result = pcall(kernel.FindClose, handle)
        if not enumeration_ok or complete ~= true or not close_ok or close_result == 0 then
            return "skipped"
        end

        table.sort(entries, function(left, right)
            if left.high ~= right.high then return left.high > right.high end
            if left.low ~= right.low then return left.low > right.low end
            return left.name > right.name
        end)
        for index = keep + 1, #entries do
            local path = append_wide_ascii(ffi, directory, root_length + #group.directory + 1,
                "\\" .. entries[index].name)
            local current_attributes = path and file_attributes(ffi, kernel, path) or nil
            if not ordinary_file(current_attributes) or kernel.DeleteFileW(path) == 0 then
                summary.delete_failures = summary.delete_failures + 1
            else
                summary.files_removed = summary.files_removed + 1
            end
        end
        return "checked"
    end

    local function run(ffi, kernel, observe_enabled, translate_enabled)
        local summary = {groups_checked = 0, groups_skipped = 0, files_removed = 0, delete_failures = 0}
        local environment_name = ffi.new("HD2Probe_U16[13]")
        local environment_value = "LOCALAPPDATA"
        for index = 1, #environment_value do environment_name[index - 1] = environment_value:byte(index) end
        local local_app = ffi.new("HD2Probe_U16[32768]")
        local local_app_length = tonumber(kernel.GetEnvironmentVariableW(environment_name, local_app, 32768))
        if not local_app_length or local_app_length == 0 or local_app_length >= 32768 then
            summary.groups_skipped = #groups
            return summary
        end

        local app_root = append_wide_ascii(ffi, local_app, local_app_length, "\\HD2ChatTranslate")
        if not app_root then summary.groups_skipped = #groups; return summary end
        local app_root_length = local_app_length + #"\\HD2ChatTranslate"
        local root_attributes, root_error = file_attributes(ffi, kernel, app_root)
        if not root_attributes then
            if not missing_path(root_error) then summary.groups_skipped = #groups end
            return summary
        end
        if not safe_directory(root_attributes) then summary.groups_skipped = #groups; return summary end

        for _, group in ipairs(groups) do
            local keep = 10
            if group.reserve == "always"
                or (group.reserve == "observe" and observe_enabled and not translate_enabled)
                or (group.reserve == "translate" and translate_enabled) then
                keep = 9
            end
            local ok, outcome = pcall(retain_group, ffi, kernel, app_root, app_root_length,
                group, keep, summary)
            if not ok or outcome == "skipped" then
                summary.groups_skipped = summary.groups_skipped + 1
            elseif outcome == "checked" then
                summary.groups_checked = summary.groups_checked + 1
            end
        end
        return summary
    end

    return {run = run}
end)()
--[[HD2_STARTUP_REPORT_RETENTION_END]]

local OBSERVE_ENABLED = false --[[HD2_CHAT_OBSERVER_ENABLED]]
local DISPLAY_TEST_ENABLED = false --[[HD2_CHAT_DISPLAY_TEST_ENABLED]]
local TRANSLATE_ENABLED = false --[[HD2_CHAT_TRANSLATE_ENABLED]]
local STANDALONE_ENABLED = false --[[HD2_CHAT_STANDALONE_ENABLED]]
local OUTGOING_PROBE_ENABLED = false --[[HD2_CHAT_OUTGOING_PROBE_ENABLED]]
local observer_core = (function()
--[[HD2_CHAT_OBSERVER_CORE]]
end)()
local translate_core = (function()
--[[HD2_CHAT_TRANSLATE_CORE]]
end)()
local native_http_factory = (function()
--[[HD2CT_NATIVE_MODULE]]
end)()

local translate_layout = (function()
    local HELPER_RVA = 0x18610C0
    local HELPER_SIGNATURE = "40534883ec20488bd94881c110010000e8cbd0beff0f57c0f30f11442430f30f108330010000f30f598320010000e8c96b8a00f30f11442434488bcb488b5424304883c4205be95560beff"
    local POSITION_RVA = 0x1860DA0
    local POSITION_SIGNATURE = "488954241053564883ec38488bda488bf14584c07513e8e568beff48899ecc0300004883c4385e5bc3"
    local GAP_RVA = 0x23C7554
    local HISTORY_HEAD_OFFSET = 0x13990
    local HISTORY_COUNT_OFFSET = 0x139C0
    local HISTORY_SLOTS_OFFSET = 0x4390
    local HISTORY_SLOT_SIZE = 0x3D8
    local HISTORY_SLOT_COUNT = 64
    local ROW_SIZE = HISTORY_SLOT_SIZE
    local ROW_HEIGHT_OFFSET = 0x10
    local ROW_SCALE_OFFSET = 0x20
    local ROW_POSITION_OFFSET = 0x3CC
    -- 位置缓存的8字节完全包含在整行中；整行核验同时覆盖其权限和allocation。
    assert(ROW_POSITION_OFFSET + 8 <= ROW_SIZE)
    local MAX_SAFE_ADDRESS = 0x7fffffffffff
    local MAX_COUNT = 9007199254740991

    local function finite(value)
        return type(value) == "number" and value == value
            and value ~= math.huge and value ~= -math.huge
    end

    local function safe_address(value)
        return finite(value) and value >= 0x10000 and value <= MAX_SAFE_ADDRESS
            and value == math.floor(value)
    end

    local function bytes_u32(bytes, offset)
        local a, b, c, d = bytes:byte(offset + 1, offset + 4)
        if not d then return nil end
        return a + b * 256 + c * 65536 + d * 16777216
    end

    local function read_float(ffi, bytes, offset, scratch)
        if type(bytes) ~= "string" or offset < 0 or offset + 4 > #bytes then return nil end
        local value = scratch or ffi.new("float[1]")
        ffi.copy(value, bytes:sub(offset + 1, offset + 4), 4)
        return tonumber(value[0])
    end

    local function valid_scale(value)
        return finite(value) and value >= 0.01 and value <= 16
    end

    local function valid_height(value)
        return finite(value) and value >= 0 and value <= 4096
    end

    local function read_status(reason)
        return reason == "budget_exhausted" and "deferred" or "read_failed"
    end

    local function add_count(value, amount)
        if value >= MAX_COUNT - amount then return MAX_COUNT end
        return value + amount
    end

    local function verify(ffi, read)
        if type(read) ~= "function" then return false end
        local helper = read(HELPER_RVA, #HELPER_SIGNATURE / 2, true)
        local position = read(POSITION_RVA, #POSITION_SIGNATURE / 2, true)
        local gap_bytes = read(GAP_RVA, 4, false)
        if helper ~= (HELPER_SIGNATURE:gsub("..", function(byte)
                return string.char(tonumber(byte, 16))
            end))
            or position ~= (POSITION_SIGNATURE:gsub("..", function(byte)
                return string.char(tonumber(byte, 16))
            end))
            or type(gap_bytes) ~= "string" or #gap_bytes ~= 4 then
            return false
        end
        local gap = read_float(ffi, gap_bytes, 0)
        if not finite(gap) or gap < 0 or gap > 2048 then return false end
        return true, gap
    end

    local function new(ffi, options)
        options = type(options) == "table" and options or {}
        local read = options.read
        local query = options.query
        local same_region = options.same_region
        local region_allowed = options.region_allowed
        local verify_context = options.verify_context
        local manager_for_root = options.manager_for_root
        local gap = options.gap
        local measure = options.measure
        local position = options.position
        local now_ms = options.now_ms
        local timing_enabled = type(now_ms) == "function"
        local float_scratch = ffi.new("float[1]")
        local verified = options.verified == true and finite(gap) and gap >= 0 and gap <= 2048
        local disabled = not verified
        local timing_names = {
            apply_prepare = true,
            apply_verify = true,
            apply_setter = true,
            apply_verify_apply = true,
            apply_reflow = true,
            apply_total = true,
            measure = true,
            position = true,
            read_slot = true,
            scan_plan = true,
            submit = true,
            response = true,
            report = true,
        }
        local timing_fields = {
            "apply_prepare", "apply_verify", "apply_setter", "apply_verify_apply",
            "apply_reflow", "apply_total", "measure", "position", "read_slot",
            "scan_plan", "submit", "response", "report",
        }
        local statistics = {
            reflows_confirmed = 0,
            failures = 0,
            rows_positioned = 0,
            positions_skipped = 0,
            last_failure_code = 0,
            last_history_head = 0,
            last_history_count = 0,
            last_geometry_slot = 0,
            last_scale_milli = 0,
            last_height_milli = 0,
            last_scale_class = 0,
            last_height_class = 0,
        }
        for _, name in ipairs(timing_fields) do
            statistics[name .. "_last_ms"] = 0
            statistics[name .. "_max_ms"] = 0
        end
        local api = {}

        local function timing_now()
            if type(now_ms) ~= "function" then return nil end
            local ok, value = pcall(now_ms)
            if not ok or not finite(value) or value < 0 then return nil end
            return value
        end

        local function timing_elapsed(started)
            if started == nil then return nil end
            local finished = timing_now()
            if finished == nil or finished < started then return nil end
            local elapsed = finished - started
            if not finite(elapsed) or elapsed < 0 or elapsed ~= math.floor(elapsed) then return nil end
            return elapsed
        end

        local function record_timing(name, milliseconds)
            if timing_names[name] ~= true or not finite(milliseconds)
                or milliseconds < 0 or milliseconds ~= math.floor(milliseconds)
                or milliseconds > MAX_SAFE_ADDRESS then
                return false
            end
            local last_field = name .. "_last_ms"
            local max_field = name .. "_max_ms"
            statistics[last_field] = milliseconds
            if milliseconds > statistics[max_field] then statistics[max_field] = milliseconds end
            return true
        end

        local function fail(status, code)
            if status ~= "deferred" then
                statistics.failures = add_count(statistics.failures, 1)
                statistics.last_failure_code = code or 12
            end
            return status
        end

        local function context_status(context)
            if type(context) ~= "table" or type(verify_context) ~= "function" then
                return "read_failed", 1
            end
            local ok, stable, reason = pcall(verify_context, context)
            if not ok then return "read_failed", 12 end
            if stable then return nil end
            return reason == "unstable" and "stale" or "read_failed", 1
        end

        local function read_exact(address, length)
            if type(read) ~= "function" then return nil, "read_failed" end
            return read(address, length)
        end

        local function read_history(manager)
            statistics.last_history_head = 0
            statistics.last_history_count = 0
            local address = manager + HISTORY_HEAD_OFFSET
            local bytes, reason = read_exact(address, 0x34)
            if not bytes then return nil, read_status(reason), 2 end
            local head = bytes_u32(bytes, 0)
            local count = bytes_u32(bytes, HISTORY_COUNT_OFFSET - HISTORY_HEAD_OFFSET)
            if head == nil or count == nil then return nil, "read_failed", 2 end
            statistics.last_history_head = head >= 65 and 65 or head
            statistics.last_history_count = count >= 65 and 65 or count
            if head >= HISTORY_SLOT_COUNT or count < 1 or count > HISTORY_SLOT_COUNT then
                return nil, "read_failed", 3
            end
            return {bytes = bytes, head = head, count = count}
        end

        local function verify_history(manager, expected)
            local current, status, code = read_history(manager)
            if not current then return status, code end
            if current.bytes ~= expected.bytes then return "stale", 9 end
            return nil
        end

        local function writable_range(address, length, expected_allocation)
            if not safe_address(address) or type(length) ~= "number" or length < 1
                or length ~= math.floor(length) or length - 1 > MAX_SAFE_ADDRESS - address
                or type(query) ~= "function" or type(same_region) ~= "function"
                or type(region_allowed) ~= "function" then
                return false, nil, "invalid_range"
            end
            local finish = address + length
            local cursor = address
            local allocation_base
            while cursor < finish do
                local region = query(cursor)
                local allowed = region and region_allowed(region)
                if not region or region.state ~= 0x1000
                    or (region.protect ~= 0x04 and region.protect ~= 0x08)
                    or not allowed
                    or not finite(region.base) or not finite(region.finish)
                    or not finite(region.allocation_base)
                    or region.base > cursor or region.finish <= cursor then
                    return false, nil, "invalid_region"
                end
                if allocation_base == nil then allocation_base = region.allocation_base end
                if region.allocation_base ~= allocation_base then
                    return false, nil, "allocation_boundary"
                end
                if expected_allocation ~= nil and region.allocation_base ~= expected_allocation then
                    return false, nil, "allocation_changed"
                end
                local region_finish = math.min(finish, region.finish)
                local verified_region = query(cursor)
                if not same_region(region, verified_region) then return false, nil, "region_changed" end
                if region_finish <= cursor then return false, nil, "invalid_region" end
                cursor = region_finish
            end
            return true, allocation_base
        end

        -- 仅用于三轮不含native调用的预检，按连续物理行分段；跨allocation逐行回退，双查发现区域变化即拒绝。
        local function writable_rows(rows)
            local index = 1
            while index <= #rows do
                local finish_index = index
                while finish_index < #rows
                    and rows[finish_index].address == rows[finish_index + 1].address + ROW_SIZE do
                    finish_index = finish_index + 1
                end

                local first = rows[finish_index]
                local last = rows[index]
                local length = last.address + ROW_SIZE - first.address
                local expected_allocation = first.allocation_base
                local one_expected_allocation = true
                for row_index = finish_index, index, -1 do
                    if rows[row_index].allocation_base ~= expected_allocation then
                        one_expected_allocation = false
                        break
                    end
                end

                local range_writable, allocation_base, range_error = false, nil, nil
                if one_expected_allocation then
                    range_writable, allocation_base, range_error = writable_range(first.address, length,
                        expected_allocation)
                end
                if range_writable then
                    for row_index = finish_index, index, -1 do
                        if rows[row_index].allocation_base == nil then
                            rows[row_index].allocation_base = allocation_base
                        end
                    end
                elseif range_error == "region_changed" or range_error == "allocation_changed" then
                    return false
                else
                    for row_index = finish_index, index, -1 do
                        local row = rows[row_index]
                        local row_writable, row_allocation = writable_range(
                            row.address, ROW_SIZE, row.allocation_base)
                        if not row_writable then return false end
                        if row.allocation_base == nil then row.allocation_base = row_allocation end
                    end
                end
                index = finish_index + 1
            end
            return true
        end

        local function capture_geometry_bytes(row, bytes)
            if type(bytes) ~= "string"
                or #bytes ~= ROW_SCALE_OFFSET - ROW_HEIGHT_OFFSET + 4 then
                return nil, "read_failed", 5
            end
            local height = read_float(ffi, bytes, 0, float_scratch)
            local scale = read_float(ffi, bytes, ROW_SCALE_OFFSET - ROW_HEIGHT_OFFSET, float_scratch)
            if not valid_scale(scale) or not valid_height(height) then
                local function metric_class(value, is_scale)
                    if not finite(value) then return 1 end
                    if value < 0 then return 2 end
                    if value == 0 then return 3 end
                    if is_scale and value < 0.01 then return 5 end
                    if (is_scale and value > 16) or (not is_scale and value > 4096) then return 5 end
                    return 4
                end
                local function metric_milli(value)
                    if finite(value) and value >= 0 and value <= 1000000 then
                        return math.floor(value * 1000 + 0.5)
                    end
                    return 0
                end
                statistics.last_geometry_slot = row.slot
                statistics.last_scale_milli = metric_milli(scale)
                statistics.last_height_milli = metric_milli(height)
                statistics.last_scale_class = metric_class(scale, true)
                statistics.last_height_class = metric_class(height, false)
                return nil, "read_failed", not valid_scale(scale) and 6 or 7
            end
            local scale_start = ROW_SCALE_OFFSET - ROW_HEIGHT_OFFSET + 1
            local scale_end = scale_start + 3
            return {bytes = bytes, scale_bytes = bytes:sub(scale_start, scale_end), scale = scale, height = height}
        end

        local function capture_geometry_rows(rows)
            local geometries = {}
            local index = 1
            while index <= #rows do
                local finish_index = index
                local allocation_base = rows[index].allocation_base
                if allocation_base ~= nil then
                    while finish_index < #rows and finish_index < index + 3
                        and rows[finish_index].address == rows[finish_index + 1].address + ROW_SIZE
                        and rows[finish_index + 1].allocation_base == allocation_base do
                        finish_index = finish_index + 1
                    end
                end

                local high_row = rows[index]
                local low_row = rows[finish_index]
                local span_length = high_row.address - low_row.address
                    + ROW_SCALE_OFFSET - ROW_HEIGHT_OFFSET + 4
                if span_length < 1 or span_length > 4096 then return nil, "read_failed", 5 end
                local bytes, reason = read_exact(low_row.address + ROW_HEIGHT_OFFSET, span_length)
                if not bytes then return nil, read_status(reason), 5 end
                if #bytes ~= span_length then return nil, "read_failed", 5 end

                for row_index = index, finish_index do
                    local row = rows[row_index]
                    local offset = row.address - low_row.address
                    local geometry_bytes = bytes:sub(offset + 1,
                        offset + ROW_SCALE_OFFSET - ROW_HEIGHT_OFFSET + 4)
                    local geometry, geometry_error, geometry_code = capture_geometry_bytes(row, geometry_bytes)
                    if not geometry then return nil, geometry_error, geometry_code end
                    geometries[row_index] = geometry
                end
                index = finish_index + 1
            end
            return geometries
        end

        local function check_context_and_history(context, manager, history)
            local status, code = context_status(context)
            if status then return status, code end
            return verify_history(manager, history)
        end

        function api.prepare(context, target_row)
            if disabled or not verified then return "disabled" end
            if not safe_address(target_row) then return fail("read_failed", 1) end
            local context_error, context_code = context_status(context)
            if context_error then return fail(context_error, context_code) end
            if not safe_address(context.root) or type(manager_for_root) ~= "function" then
                return fail("read_failed", 1)
            end
            local ok, manager = pcall(manager_for_root, context.root)
            if not ok then return fail("read_failed", 12) end
            if not safe_address(manager) then return fail("read_failed", 1) end
            local history, history_error, history_code = read_history(manager)
            if not history then return fail(history_error, history_code) end

            local rows = {}
            local target_found = false
            for index = 0, history.count - 1 do
                local slot = (history.head - index - 1) % HISTORY_SLOT_COUNT
                local address = manager + HISTORY_SLOTS_OFFSET + slot * HISTORY_SLOT_SIZE
                if not safe_address(address) then
                    return fail("read_failed", 4)
                end
                local row = {slot = slot, address = address}
                rows[#rows + 1] = row
                if address == target_row then target_found = true end
            end
            if not writable_rows(rows) then return fail("read_failed", 4) end
            local geometries, geometry_error, geometry_code = capture_geometry_rows(rows)
            if not geometries then return fail(geometry_error, geometry_code) end
            for index, row in ipairs(rows) do
                row.geometry = geometries[index]
            end
            if not target_found then return fail("stale", 8) end

            local status, status_code = check_context_and_history(context, manager, history)
            if status then return fail(status, status_code) end
            if not writable_rows(rows) then return fail("read_failed", 4) end
            geometries, geometry_error, geometry_code = capture_geometry_rows(rows)
            if not geometries then return fail(geometry_error, geometry_code) end
            for index, row in ipairs(rows) do
                if geometries[index].bytes ~= row.geometry.bytes then return fail("stale", 10) end
            end
            status, status_code = check_context_and_history(context, manager, history)
            if status then return fail(status, status_code) end
            return "ready", {
                context = context,
                manager = manager,
                history = history,
                rows = rows,
                target_row = target_row,
            }
        end

        function api.verify_prepared(snapshot)
            if disabled or not verified then return "disabled" end
            if type(snapshot) ~= "table" or type(snapshot.rows) ~= "table" then
                return fail("read_failed", 1)
            end
            local status, status_code = check_context_and_history(snapshot.context, snapshot.manager, snapshot.history)
            if status then return fail(status, status_code) end
            if not writable_rows(snapshot.rows) then return fail("read_failed", 4) end
            local geometries, geometry_error, geometry_code = capture_geometry_rows(snapshot.rows)
            if not geometries then return fail(geometry_error, geometry_code) end
            for index, row in ipairs(snapshot.rows) do
                if geometries[index].bytes ~= row.geometry.bytes then return fail("stale", 10) end
            end
            status, status_code = check_context_and_history(snapshot.context, snapshot.manager, snapshot.history)
            if status then return fail(status, status_code) end
            return "ready"
        end

        function api.reflow(snapshot, target_row)
            if disabled or not verified then return "called_unconfirmed" end
            if type(snapshot) ~= "table" or snapshot.target_row ~= target_row
                or type(snapshot.rows) ~= "table" or type(measure) ~= "function"
                or type(position) ~= "function" then
                disabled = true
                return fail("called_unconfirmed", 11)
            end

            local status, status_code = check_context_and_history(snapshot.context, snapshot.manager, snapshot.history)
            if status then disabled = true; return fail("called_unconfirmed", status_code or 11) end
            if not writable_rows(snapshot.rows) then
                disabled = true
                return fail("called_unconfirmed", 11)
            end
            local target
            for _, row in ipairs(snapshot.rows) do
                if row.address == target_row then target = row; break end
            end
            if not target then disabled = true; return fail("called_unconfirmed", 11) end

            local measure_started = timing_enabled and timing_now() or nil
            local measure_ok = pcall(measure, target.address)
            local measure_elapsed = measure_started ~= nil and timing_elapsed(measure_started) or nil
            if measure_elapsed ~= nil then record_timing("measure", measure_elapsed) end
            if not measure_ok then disabled = true; return fail("called_unconfirmed", 11) end

            if not writable_rows(snapshot.rows) then
                disabled = true
                return fail("called_unconfirmed", 11)
            end
            local geometries, geometry_error, geometry_code = capture_geometry_rows(snapshot.rows)
            if not geometries then disabled = true; return fail("called_unconfirmed", geometry_code) end
            for index, row in ipairs(snapshot.rows) do
                local geometry = geometries[index]
                if row == target then
                    if geometry.scale_bytes ~= row.geometry.scale_bytes then
                        disabled = true
                        return fail("called_unconfirmed", 10)
                    end
                    row.final_geometry = geometry
                elseif geometry.bytes ~= row.geometry.bytes then
                    disabled = true
                    return fail("called_unconfirmed", 10)
                else
                    row.final_geometry = geometry
                end
            end
            local y = 0
            for _, row in ipairs(snapshot.rows) do
                local geometry = row.final_geometry
                if not valid_scale(geometry.scale) or not valid_height(geometry.height)
                    or not finite(y) or y > 1000000 then
                    disabled = true
                    return fail("called_unconfirmed", 11)
                end
                local vector = ffi.new("float[2]")
                vector[0] = 0
                vector[1] = y
                local packed = ffi.cast("HD2Probe_U64 *", ffi.cast("void *", vector))[0]
                row.position_vector = vector
                row.position_packed = packed
                row.position_bytes = ffi.string(vector, 8)
                y = y + geometry.scale * geometry.height + gap
            end
            if not finite(y) or y > 1000000 then
                disabled = true
                return fail("called_unconfirmed", 11)
            end

            status, status_code = check_context_and_history(snapshot.context, snapshot.manager, snapshot.history)
            if status then disabled = true; return fail("called_unconfirmed", status_code or 11) end
            if not writable_rows(snapshot.rows) then
                disabled = true
                return fail("called_unconfirmed", 11)
            end

            local position_elapsed_ms = 0
            local position_timing_valid = timing_enabled
            for _, row in ipairs(snapshot.rows) do
                local cached = read_exact(row.address + ROW_POSITION_OFFSET, 8)
                if type(cached) ~= "string" or #cached ~= 8 then
                    disabled = true
                    return fail("called_unconfirmed", 11)
                end
                if row.address ~= target_row and cached == row.position_bytes then
                    local row_writable = writable_range(row.address, ROW_SIZE, row.allocation_base)
                    if not row_writable then
                        disabled = true
                        return fail("called_unconfirmed", 11)
                    end
                    statistics.positions_skipped = add_count(statistics.positions_skipped, 1)
                else
                    local row_writable = writable_range(row.address, ROW_SIZE, row.allocation_base)
                    if not row_writable then
                        disabled = true
                        return fail("called_unconfirmed", 11)
                    end
                    local position_ok
                    if position_timing_valid then
                        local position_started = timing_now()
                        position_ok = pcall(position, row.address, row.position_packed, 0)
                        local position_elapsed = timing_elapsed(position_started)
                        if position_elapsed == nil then
                            position_timing_valid = false
                        else
                            position_elapsed_ms = position_elapsed_ms + position_elapsed
                            if position_elapsed_ms > MAX_SAFE_ADDRESS then
                                position_elapsed_ms = MAX_SAFE_ADDRESS
                            end
                        end
                    else
                        position_ok = pcall(position, row.address, row.position_packed, 0)
                    end
                    if not position_ok then disabled = true; return fail("called_unconfirmed", 11) end
                    local positioned, reason = read_exact(row.address + ROW_POSITION_OFFSET, 8)
                    if not positioned or positioned ~= row.position_bytes then
                        disabled = true
                        return fail("called_unconfirmed", 11)
                    end
                    statistics.rows_positioned = add_count(statistics.rows_positioned, 1)
                end
            end

            if position_timing_valid then record_timing("position", position_elapsed_ms) end

            status, status_code = check_context_and_history(snapshot.context, snapshot.manager, snapshot.history)
            if status then disabled = true; return fail("called_unconfirmed", status_code or 11) end
            statistics.reflows_confirmed = add_count(statistics.reflows_confirmed, 1)
            return "called_confirmed"
        end

        function api.disable(count_failure, code)
            disabled = true
            if count_failure == true then
                statistics.failures = add_count(statistics.failures, 1)
                statistics.last_failure_code = code == 11 and 11 or 12
            end
        end

        function api.stats()
            local result = {
                verified = verified,
                reflows_confirmed = statistics.reflows_confirmed,
                failures = statistics.failures,
                rows_positioned = statistics.rows_positioned,
                positions_skipped = statistics.positions_skipped,
                last_failure_code = statistics.last_failure_code,
                last_history_head = statistics.last_history_head,
                last_history_count = statistics.last_history_count,
                last_geometry_slot = statistics.last_geometry_slot,
                last_scale_milli = statistics.last_scale_milli,
                last_height_milli = statistics.last_height_milli,
                last_scale_class = statistics.last_scale_class,
                last_height_class = statistics.last_height_class,
            }
            for _, name in ipairs(timing_fields) do
                result[name .. "_last_ms"] = statistics[name .. "_last_ms"]
                result[name .. "_max_ms"] = statistics[name .. "_max_ms"]
            end
            return result
        end

        api.record_timing = record_timing

        return api
    end

    return {verify = verify, new = new}
end)()

local function initialize_probe()
    local ffi = require("ffi")
    ffi.cdef[[
        typedef unsigned char HD2Probe_U8;
        typedef unsigned short HD2Probe_U16;
        typedef unsigned int HD2Probe_U32;
        typedef unsigned long long HD2Probe_U64;
        typedef int HD2Probe_I32;
        typedef size_t HD2Probe_SIZE_T;
        typedef void *HD2Probe_HANDLE;
        typedef void *HD2Probe_HMODULE;
        typedef void *HD2Probe_BCRYPT_HANDLE;
        typedef HD2Probe_BCRYPT_HANDLE HD2Probe_BCRYPT_ALG_HANDLE;
        typedef HD2Probe_BCRYPT_HANDLE HD2Probe_BCRYPT_HASH_HANDLE;
        typedef struct HD2Probe_MEMORY_BASIC_INFORMATION {
            void *BaseAddress;
            void *AllocationBase;
            HD2Probe_U32 AllocationProtect;
            HD2Probe_U16 PartitionId;
            HD2Probe_U16 Padding;
            HD2Probe_SIZE_T RegionSize;
            HD2Probe_U32 State;
            HD2Probe_U32 Protect;
            HD2Probe_U32 Type;
            HD2Probe_U32 Padding2;
        } HD2Probe_MEMORY_BASIC_INFORMATION;
        typedef struct HD2Probe_BY_HANDLE_FILE_INFORMATION {
            HD2Probe_U32 dwFileAttributes;
            HD2Probe_U32 ftCreationTimeLow;
            HD2Probe_U32 ftCreationTimeHigh;
            HD2Probe_U32 ftLastAccessTimeLow;
            HD2Probe_U32 ftLastAccessTimeHigh;
            HD2Probe_U32 ftLastWriteTimeLow;
            HD2Probe_U32 ftLastWriteTimeHigh;
            HD2Probe_U32 dwVolumeSerialNumber;
            HD2Probe_U32 nFileSizeHigh;
            HD2Probe_U32 nFileSizeLow;
            HD2Probe_U32 nNumberOfLinks;
            HD2Probe_U32 nFileIndexHigh;
            HD2Probe_U32 nFileIndexLow;
        } HD2Probe_BY_HANDLE_FILE_INFORMATION;
        typedef struct HD2Probe_FILETIME {
            HD2Probe_U32 dwLowDateTime;
            HD2Probe_U32 dwHighDateTime;
        } HD2Probe_FILETIME;
        typedef struct HD2Probe_WIN32_FIND_DATAW {
            HD2Probe_U32 dwFileAttributes;
            HD2Probe_FILETIME ftCreationTime;
            HD2Probe_FILETIME ftLastAccessTime;
            HD2Probe_FILETIME ftLastWriteTime;
            HD2Probe_U32 nFileSizeHigh;
            HD2Probe_U32 nFileSizeLow;
            HD2Probe_U32 dwReserved0;
            HD2Probe_U32 dwReserved1;
            HD2Probe_U16 cFileName[260];
            HD2Probe_U16 cAlternateFileName[14];
        } HD2Probe_WIN32_FIND_DATAW;

        HD2Probe_HANDLE GetCurrentProcess(void);
        HD2Probe_HMODULE GetModuleHandleA(const char *module_name);
        HD2Probe_HMODULE LoadLibraryExW(const HD2Probe_U16 *path, void *file, HD2Probe_U32 flags);
        void *GetProcAddress(HD2Probe_HMODULE module, const char *name);
        HD2Probe_U32 GetFileSize(HD2Probe_HANDLE file, HD2Probe_U32 *high);
        HD2Probe_U32 GetLastError(void);
        int GetFileInformationByHandle(HD2Probe_HANDLE file, HD2Probe_BY_HANDLE_FILE_INFORMATION *information);
        HD2Probe_U32 GetModuleFileNameW(HD2Probe_HMODULE module, HD2Probe_U16 *path, HD2Probe_U32 capacity);
        HD2Probe_SIZE_T VirtualQuery(const void *address, HD2Probe_MEMORY_BASIC_INFORMATION *information, HD2Probe_SIZE_T information_size);
        int ReadProcessMemory(HD2Probe_HANDLE process, const void *address, void *buffer, HD2Probe_SIZE_T length, HD2Probe_SIZE_T *bytes_read);
        HD2Probe_HANDLE CreateFileW(const HD2Probe_U16 *path, HD2Probe_U32 access, HD2Probe_U32 share_mode, void *security, HD2Probe_U32 creation, HD2Probe_U32 attributes, HD2Probe_HANDLE template_file);
        HD2Probe_HANDLE FindFirstFileW(const HD2Probe_U16 *pattern, HD2Probe_WIN32_FIND_DATAW *find_data);
        int FindNextFileW(HD2Probe_HANDLE find, HD2Probe_WIN32_FIND_DATAW *find_data);
        int FindClose(HD2Probe_HANDLE find);
        HD2Probe_U32 GetFileAttributesW(const HD2Probe_U16 *path);
        int ReadFile(HD2Probe_HANDLE file, void *buffer, HD2Probe_U32 length, HD2Probe_U32 *bytes_read, void *overlapped);
        int CloseHandle(HD2Probe_HANDLE handle);
        int CreateDirectoryA(const char *path, void *security);
        int CreateDirectoryW(const HD2Probe_U16 *path, void *security);
        HD2Probe_U32 GetEnvironmentVariableW(const HD2Probe_U16 *name, HD2Probe_U16 *buffer, HD2Probe_U32 capacity);
        HD2Probe_U64 GetTickCount64(void);
        int WriteFile(HD2Probe_HANDLE file, const void *buffer, HD2Probe_U32 length, HD2Probe_U32 *bytes_written, void *overlapped);
        int FlushFileBuffers(HD2Probe_HANDLE file);
        int DeleteFileW(const HD2Probe_U16 *path);
        int MoveFileExW(const HD2Probe_U16 *existing_path, const HD2Probe_U16 *new_path, HD2Probe_U32 flags);

        HD2Probe_I32 BCryptOpenAlgorithmProvider(HD2Probe_BCRYPT_ALG_HANDLE *algorithm, const HD2Probe_U16 *algorithm_id, const HD2Probe_U16 *implementation, HD2Probe_U32 flags);
        HD2Probe_I32 BCryptCreateHash(HD2Probe_BCRYPT_ALG_HANDLE algorithm, HD2Probe_BCRYPT_HASH_HANDLE *hash, HD2Probe_U8 *object_buffer, HD2Probe_U32 object_size, HD2Probe_U8 *secret, HD2Probe_U32 secret_size, HD2Probe_U32 flags);
        HD2Probe_I32 BCryptHashData(HD2Probe_BCRYPT_HASH_HANDLE hash, HD2Probe_U8 *data, HD2Probe_U32 length, HD2Probe_U32 flags);
        HD2Probe_I32 BCryptFinishHash(HD2Probe_BCRYPT_HASH_HANDLE hash, HD2Probe_U8 *digest, HD2Probe_U32 digest_size, HD2Probe_U32 flags);
        HD2Probe_I32 BCryptDestroyHash(HD2Probe_BCRYPT_HASH_HANDLE hash);
        HD2Probe_I32 BCryptCloseAlgorithmProvider(HD2Probe_BCRYPT_ALG_HANDLE algorithm, HD2Probe_U32 flags);
    ]]

    local kernel = ffi.load("kernel32.dll")
    target_language_settings_clock = function()
        return tonumber(kernel.GetTickCount64())
    end
    local retention_ok, retention_summary = pcall(
        startup_report_retention.run, ffi, kernel, OBSERVE_ENABLED, TRANSLATE_ENABLED)
    if not retention_ok then
        pcall(print, "[HD2 Chat Probe] local report retention skipped")
    elseif retention_summary.groups_skipped > 0 or retention_summary.delete_failures > 0 then
        pcall(print, "[HD2 Chat Probe] local report retention partial; skipped groups / delete failures / removed files:",
            retention_summary.groups_skipped, retention_summary.delete_failures, retention_summary.files_removed)
    end
    local bcrypt = ffi.load("bcrypt.dll")
    local module = kernel.GetModuleHandleA("game.dll")
    if module == nil then error("game.dll is not loaded") end
    local module_base = ffi.cast("size_t", module)
    local process = kernel.GetCurrentProcess()
    local invalid_handle = ffi.cast("HD2Probe_HANDLE", -1)
    local expected_image_size = core.SOURCE.size_of_image
    local expected_section_end = core.SECTION.rva + core.SECTION.size

    local function u16_ascii(text)
        local buffer = ffi.new("HD2Probe_U16[?]", #text + 1)
        for index = 1, #text do buffer[index - 1] = text:byte(index) end
        buffer[#text] = 0
        return buffer
    end

    local function open_sha256()
        local algorithm_out = ffi.new("HD2Probe_BCRYPT_ALG_HANDLE[1]")
        local algorithm_name = u16_ascii("SHA256")
        if bcrypt.BCryptOpenAlgorithmProvider(algorithm_out, algorithm_name, nil, 0) ~= 0 then
            return nil
        end
        local algorithm = algorithm_out[0]
        local hash_out = ffi.new("HD2Probe_BCRYPT_HASH_HANDLE[1]")
        if bcrypt.BCryptCreateHash(algorithm, hash_out, nil, 0, nil, 0, 0) ~= 0 then
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
            return nil
        end
        return algorithm, hash_out[0]
    end

    local function finish_sha256(algorithm, hash, digest_buffer)
        local ok = bcrypt.BCryptFinishHash(hash, digest_buffer, 32, 0) == 0
        bcrypt.BCryptDestroyHash(hash)
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
        if not ok then return nil end
        local pieces = {}
        for index = 0, 31 do pieces[#pieces + 1] = string.format("%02x", digest_buffer[index]) end
        return table.concat(pieces)
    end

    local function hash_module_file()
        local path = ffi.new("HD2Probe_U16[32768]")
        local path_length = kernel.GetModuleFileNameW(module, path, 32768)
        if path_length == 0 or path_length >= 32768 then return nil, "module path unavailable" end
        local file = kernel.CreateFileW(path, 0x80000000, 0x00000007, nil, 3, 0x80, nil)
        if file == nil or file == invalid_handle then return nil, "module file cannot be opened" end

        local algorithm, hash = open_sha256()
        if not algorithm then
            kernel.CloseHandle(file)
            return nil, "SHA-256 provider unavailable"
        end
        local buffer = ffi.new("HD2Probe_U8[8192]")
        local bytes_read = ffi.new("HD2Probe_U32[1]")
        local ok = true
        local total = 0
        while true do
            local requested = math.min(8192, core.SOURCE.disk_size + 1 - total)
            if requested <= 0 then break end
            bytes_read[0] = 0
            if kernel.ReadFile(file, buffer, requested, bytes_read, nil) == 0 then
                ok = false
                break
            end
            if bytes_read[0] == 0 then break end
            if bytes_read[0] > requested then
                ok = false
                break
            end
            total = total + tonumber(bytes_read[0])
            if total > core.SOURCE.disk_size then break end
            if bcrypt.BCryptHashData(hash, buffer, bytes_read[0], 0) ~= 0 then
                ok = false
                break
            end
        end
        kernel.CloseHandle(file)
        if not ok or total ~= core.SOURCE.disk_size then
            bcrypt.BCryptDestroyHash(hash)
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
            return nil, total, total > core.SOURCE.disk_size
                and "module file exceeds the approved size" or "module file size or read failed"
        end
        local digest = ffi.new("HD2Probe_U8[32]")
        local result = finish_sha256(algorithm, hash, digest)
        if not result then return nil, total, "module file hash failed" end
        return result, total
    end

    local function hash_bytes(data)
        local algorithm, hash = open_sha256()
        if not algorithm then error("SHA-256 provider unavailable") end
        local input = ffi.new("HD2Probe_U8[?]", #data)
        if #data > 0 then ffi.copy(input, data, #data) end
        local ok = bcrypt.BCryptHashData(hash, input, #data, 0) == 0
        local digest = ffi.new("HD2Probe_U8[32]")
        if not ok then
            bcrypt.BCryptDestroyHash(hash)
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
            error("candidate hash failed")
        end
        local result = finish_sha256(algorithm, hash, digest)
        if not result then error("candidate hash failed") end
        return result
    end

    local native_http_loader
    if STANDALONE_ENABLED and type(native_http_factory) == "function" then
        local loader_ok, loader = pcall(native_http_factory, ffi, kernel, bcrypt, hash_bytes, u16_ascii)
        if loader_ok and type(loader) == "table" and type(loader.load) == "function" then
            native_http_loader = loader
        end
    end

    local function query(rva)
        if type(rva) ~= "number" or rva ~= rva or rva < 0 or rva ~= math.floor(rva)
            or rva >= expected_image_size then return nil end
        local information = ffi.new("HD2Probe_MEMORY_BASIC_INFORMATION[1]")
        local address = ffi.cast("const void *", module_base + rva)
        local received = kernel.VirtualQuery(address, information, ffi.sizeof(information[0]))
        if received ~= ffi.sizeof(information[0]) then return nil end
        local item = information[0]
        local region_base = ffi.cast("size_t", item.BaseAddress)
        if region_base < module_base then return nil end
        local start_rva = tonumber(region_base - module_base)
        local region_size = tonumber(item.RegionSize)
        if start_rva > expected_image_size or region_size <= 0
            or region_size > expected_image_size - start_rva then return nil end
        return {
            start_rva = start_rva,
            size = region_size,
            allocation_base = ffi.cast("size_t", item.AllocationBase) == module_base,
            type = tonumber(item.Type),
            state = tonumber(item.State),
            protect = tonumber(item.Protect),
        }
    end

    local function is_readable(protect)
        return protect == 0x02 or protect == 0x04 or protect == 0x08
            or protect == 0x20 or protect == 0x40 or protect == 0x80
    end

    local function is_executable(protect)
        return protect == 0x20 or protect == 0x40 or protect == 0x80
    end

    local function same_query(a, b)
        return a and b and a.start_rva == b.start_rva and a.size == b.size
            and a.allocation_base == b.allocation_base and a.type == b.type
            and a.state == b.state and a.protect == b.protect
    end

    local function read_memory(rva, length, executable)
        if type(rva) ~= "number" or type(length) ~= "number"
            or rva ~= rva or length ~= length or rva < 0 or length <= 0
            or rva ~= math.floor(rva) or length ~= math.floor(length)
            or rva > expected_image_size or length > expected_image_size - rva then return nil end
        if executable and (rva < core.SECTION.rva or rva >= expected_section_end
            or length > expected_section_end - rva) then return nil end
        local finish = rva + length
        local output = ffi.new("HD2Probe_U8[?]", length)
        local cursor = rva
        while cursor < finish do
            local region = query(cursor)
            if not region or not region.allocation_base or region.type ~= 0x1000000
                or region.state ~= 0x1000 or not is_readable(region.protect)
                or (executable and not is_executable(region.protect)) then return nil end
            local region_end = region.start_rva + region.size
            if region.start_rva > cursor or region_end <= cursor then return nil end
            local page_end = math.floor(cursor / 4096 + 1) * 4096
            local chunk_end = math.min(finish, region_end, page_end)
            if chunk_end <= cursor then return nil end
            local rechecked = query(cursor)
            if not same_query(region, rechecked) then return nil end
            local chunk_length = chunk_end - cursor
            local bytes_read = ffi.new("HD2Probe_SIZE_T[1]")
            local destination = output + (cursor - rva)
            local source = ffi.cast("const void *", module_base + cursor)
            if kernel.ReadProcessMemory(process, source, destination, chunk_length, bytes_read) == 0
                or tonumber(bytes_read[0]) ~= chunk_length then return nil end
            cursor = chunk_end
        end
        return ffi.string(output, length)
    end

    local function output_manifest(encoded)
        local local_app = os.getenv("LOCALAPPDATA")
        if not local_app or local_app == "" then error("LOCALAPPDATA unavailable") end
        local parent = local_app .. "\\HD2ChatTranslate"
        local directory = parent .. "\\probe"
        kernel.CreateDirectoryA(parent, nil)
        kernel.CreateDirectoryA(directory, nil)
        local nonce = math.floor((os.clock() * 1000000) % 0x7fffffff)
        local identity = tostring({}):gsub("[^0-9a-fA-F]", "")
        local file, partial_path, final_path
        for attempt = 1, 16 do
            local stem = string.format("chat-probe-%d-%08x-%s-%02d", os.time(), nonce, identity, attempt)
            partial_path = directory .. "\\" .. stem .. ".json.partial"
            final_path = directory .. "\\" .. stem .. ".json"
            local existing = io.open(partial_path, "rb")
            if existing then existing:close() end
            local final_existing = io.open(final_path, "rb")
            if final_existing then final_existing:close() end
            if not existing and not final_existing then
                file = io.open(partial_path, "wb")
                if file then break end
            end
        end
        if not file then error("unique manifest path unavailable") end
        local ok, write_error = file:write(encoded)
        local close_ok, close_error = file:close()
        if not ok or close_ok == nil then
            os.remove(partial_path)
            error(write_error or close_error or "manifest write failed")
        end
        local renamed, rename_error = os.rename(partial_path, final_path)
        if not renamed then
            os.remove(partial_path)
            error(rename_error or "manifest rename failed")
        end
    end

    local observer_read_budget = 0
    local observer_display_native_gate = false
    local DISPLAY_TARGET_RVA = 0x1441CA0
    local DISPLAY_TARGET_PREFIX = "40534883ec20488bd94881c110010000e8fb84ffff"
    local DISPLAY_TEST_TEXT = "聊天翻译测试成功"
    local DISPLAY_PIN_TABLE = "__HD2_CHAT_DISPLAY_TEST_PINS_V1"
    local DISPLAY_MAX_PINS = 16
    local TRANSLATE_PIN_TABLE = "__HD2_CHAT_TRANSLATE_PINS_V1"
    local TRANSLATE_MAX_PINS = 512
    local TRANSLATE_MAX_PIN_BYTES = 8 * 1024 * 1024
    local TRANSLATE_MAX_REQUEST_BYTES = 1023
    local TRANSLATE_MAX_RESPONSE_BYTES = 16387
    local TRANSLATE_MAX_REPORT_BYTES = 64 * 1024
    local observer_now = 0
    local observer_next_command_poll = 0
    local observer_faulted = false
    local observer_output_sequence = 0
    local observer_session_time = os.time()
    local observer_session_nonce = 0
    local observer_active_cycle = nil
    local observer_ids = {}
    local observer_id_count = 0
    local observer_next_id = 0
    local observer_local_app
    local observer_local_app_length
    local observer_directory
    local observer_directory_length
    local observer_request_path
    local translate_mailbox_directory
    local translate_mailbox_directory_length
    local translate_heartbeat_path
    local translate_report_path
    local translate_session_id
    local translate_heartbeat_next_poll = 0
    local translate_cached_heartbeat
    local translate_heartbeat_fresh = false
    local translate_owned_tokens = {}
    local translate_file_sequence = 0
    local native_transport_api

    local MAX_OBSERVER_ADDRESS = 0x7fffffffffff
    local MAX_OBSERVER_READ = TRANSLATE_ENABLED and 256 * 1024 or 16 * 1024
    local FULL_APPLY_READ_RESERVE = 224 * 1024
    local OBSERVER_IMAGE_SIZE = expected_image_size

    local function append_wide_ascii(base, base_length, suffix)
        if type(suffix) ~= "string" or suffix:find("[^%w_%.%-\\]") then return nil end
        local length = base_length + #suffix
        if length + 1 > 32768 then return nil end
        local output = ffi.new("HD2Probe_U16[?]", length + 1)
        if base_length > 0 then ffi.copy(output, base, base_length * 2) end
        for index = 1, #suffix do output[base_length + index - 1] = suffix:byte(index) end
        output[length] = 0
        return output, length
    end

    local function prepare_observer_paths()
        local name = u16_ascii("LOCALAPPDATA")
        local buffer = ffi.new("HD2Probe_U16[32768]")
        local length = kernel.GetEnvironmentVariableW(name, buffer, 32768)
        if length == 0 or length >= 32768 then error("observer path unavailable") end
        observer_local_app = buffer
        observer_local_app_length = tonumber(length)

        local parent, parent_length = append_wide_ascii(buffer, observer_local_app_length, "\\HD2ChatTranslate")
        local directory, directory_length = append_wide_ascii(buffer, observer_local_app_length, "\\HD2ChatTranslate\\observe")
        if not parent or not directory then error("observer path unavailable") end
        kernel.CreateDirectoryW(parent, nil)
        kernel.CreateDirectoryW(directory, nil)
        observer_directory = directory
        observer_directory_length = directory_length
        observer_request_path = append_wide_ascii(directory, directory_length, "\\request.txt")
        if not observer_request_path then error("observer path unavailable") end

        local uptime = tonumber(kernel.GetTickCount64())
        if not uptime or uptime < 0 then error("observer clock unavailable") end
        observer_session_nonce = math.floor((uptime * 48271 + (observer_session_time % 2147483647)) % 2147483647)
    end

    local function new_observer_scratch()
        return {
            output = ffi.new("HD2Probe_U8[4096]"),
            bytes_read = ffi.new("HD2Probe_SIZE_T[1]"),
            information = ffi.new("HD2Probe_MEMORY_BASIC_INFORMATION[1]"),
            decode = ffi.new("HD2Probe_SIZE_T[1]"),
        }
    end

    local observer_scratch_pool

    local function acquire_observer_scratch()
        if observer_scratch_pool == nil then
            observer_scratch_pool = new_observer_scratch()
        end
        if not observer_scratch_pool.busy then
            observer_scratch_pool.busy = true
            return observer_scratch_pool, true
        end
        -- 同步回调重入时使用独立临时区，不能覆盖外层正在读取的数据。
        return new_observer_scratch(), false
    end

    local function with_observer_scratch(callback, ...)
        local scratch, pooled = acquire_observer_scratch()
        local ok, first, second = pcall(callback, scratch, ...)
        if pooled then scratch.busy = false end
        if not ok then error(first, 0) end
        return first, second
    end

    local function observer_query_address_with_scratch(scratch, address)
        if type(address) ~= "number" or address ~= address or address == math.huge or address == -math.huge
            or address < 0x10000 or address > MAX_OBSERVER_ADDRESS or address ~= math.floor(address) then
            return nil, "invalid_address"
        end
        local information = scratch.information
        local pointer = ffi.cast("const void *", ffi.cast("size_t", address))
        local received = kernel.VirtualQuery(pointer, information, ffi.sizeof(information[0]))
        if received ~= ffi.sizeof(information[0]) then return nil, "virtual_query_failed" end
        local item = information[0]
        local region_base = tonumber(ffi.cast("size_t", item.BaseAddress))
        local allocation_base = tonumber(ffi.cast("size_t", item.AllocationBase))
        local region_size = tonumber(item.RegionSize)
        if not region_base or not allocation_base or not region_size or region_size <= 0
            or region_base < 0x10000 or region_base > MAX_OBSERVER_ADDRESS
            or region_size > MAX_OBSERVER_ADDRESS - region_base + 1 then return nil, "malformed_region" end
        return {
            base = region_base,
            finish = region_base + region_size,
            allocation_base = allocation_base,
            state = tonumber(item.State),
            protect = tonumber(item.Protect),
            type = tonumber(item.Type),
        }, nil
    end

    local function observer_query_address(address)
        return with_observer_scratch(observer_query_address_with_scratch, address)
    end

    local function same_observer_region(left, right)
        return left and right and left.base == right.base and left.finish == right.finish
            and left.allocation_base == right.allocation_base and left.state == right.state
            and left.protect == right.protect and left.type == right.type
    end

    local module_base_number = tonumber(module_base)

    local function observer_region_allowed(region)
        if not region then return false, "malformed_region" end
        if region.state ~= 0x1000 then return false, "not_committed" end
        if region.protect ~= 0x02 and region.protect ~= 0x04 and region.protect ~= 0x08 then
            return false, "protection_denied"
        end
        if region.type == 0x20000 then return true end
        if region.type == 0x1000000 and region.allocation_base == module_base_number then return true end
        return false, "allocation_denied"
    end

    local function observer_read_with_scratch(scratch, address, length)
        if type(length) ~= "number" or length ~= length or length ~= math.floor(length)
            or length < 1 or length > 4096 then
            return nil, "invalid_length"
        end
        if length > MAX_OBSERVER_READ - observer_read_budget then
            return nil, "budget_exhausted"
        end
        observer_read_budget = observer_read_budget + length
        if type(address) ~= "number" or address ~= address or address == math.huge or address == -math.huge
            or address ~= math.floor(address) then return nil, "invalid_address" end
        if address < 0x10000 or address > MAX_OBSERVER_ADDRESS
            or length - 1 > MAX_OBSERVER_ADDRESS - address then return nil, "pointer_range" end

        local finish = address + length
        local cursor = address
        local first_allocation = nil
        local output = scratch.output
        while cursor < finish do
            local region, query_reason = observer_query_address_with_scratch(scratch, cursor)
            if not region then return nil, query_reason end
            local allowed, allowed_reason = observer_region_allowed(region)
            if not allowed then return nil, allowed_reason end
            if first_allocation == nil then first_allocation = region.allocation_base
            elseif first_allocation ~= region.allocation_base then return nil, "allocation_changed" end
            if region.base > cursor or region.finish <= cursor then return nil, "region_bounds" end

            local page_finish = (math.floor(cursor / 4096) + 1) * 4096
            local chunk_finish = math.min(finish, region.finish, page_finish)
            if chunk_finish <= cursor then return nil, "region_bounds" end
            local verified, verified_reason = observer_query_address_with_scratch(scratch, cursor)
            if not verified then return nil, verified_reason end
            local verified_allowed, verified_allowed_reason = observer_region_allowed(verified)
            if not verified_allowed then return nil, verified_allowed_reason end
            if not same_observer_region(region, verified) then return nil, "region_changed" end
            if verified.allocation_base ~= first_allocation then return nil, "allocation_changed" end

            local chunk_length = chunk_finish - cursor
            scratch.bytes_read[0] = 0
            local destination = output + (cursor - address)
            local source = ffi.cast("const void *", ffi.cast("size_t", cursor))
            if kernel.ReadProcessMemory(process, source, destination, chunk_length, scratch.bytes_read) == 0 then
                return nil, "read_failed"
            end
            if tonumber(scratch.bytes_read[0]) ~= chunk_length then return nil, "short_read" end
            cursor = chunk_finish
        end
        return ffi.string(output, length), nil
    end

    local function observer_read(address, length)
        return with_observer_scratch(observer_read_with_scratch, address, length)
    end

    local function observer_read_pointer_with_scratch(scratch, address)
        local bytes, reason = observer_read_with_scratch(scratch, address, 8)
        if not bytes then return nil, reason end
        ffi.copy(scratch.decode, bytes, 8)
        local number = tonumber(scratch.decode[0])
        if not number or number ~= math.floor(number) then return nil, "malformed_bytes" end
        if number == 0 then return nil, "null_pointer" end
        if number < 0x10000 or number > MAX_OBSERVER_ADDRESS then return nil, "pointer_range" end
        return number, nil
    end

    local function observer_read_pointer(address)
        return with_observer_scratch(observer_read_pointer_with_scratch, address)
    end

    local function observer_u32(bytes, offset)
        local a, b, c, d = bytes:byte(offset + 1, offset + 4)
        if not d then return nil end
        return a + b * 256 + c * 65536 + d * 16777216
    end

    local function observer_u64_with_scratch(scratch, bytes, offset)
        if not bytes or offset < 0 or offset + 8 > #bytes then return nil end
        ffi.copy(scratch.decode, bytes:sub(offset + 1, offset + 8), 8)
        local number = tonumber(scratch.decode[0])
        if not number or number < 0 or number > MAX_OBSERVER_ADDRESS or number ~= math.floor(number) then return nil end
        return number
    end

    local function observer_u64(bytes, offset)
        return with_observer_scratch(observer_u64_with_scratch, bytes, offset)
    end

    local function observer_add(address, offset)
        if type(address) ~= "number" or type(offset) ~= "number" or offset < 0
            or offset ~= math.floor(offset) or address < 0x10000 or address > MAX_OBSERVER_ADDRESS
            or offset > MAX_OBSERVER_ADDRESS - address then return nil end
        return address + offset
    end

    local function observer_root_snapshot()
        local root_address = observer_add(module_base_number, 0x347CEF0)
        if not root_address then return nil end
        local root = observer_read_pointer(root_address)
        if not root then return nil end
        local chat = observer_add(root, 0xC418)
        if not chat then return nil end
        local metadata_address = observer_add(chat, 0x9590)
        if not metadata_address then return nil end
        local metadata = observer_read(metadata_address, 8)
        if not metadata then return nil end
        local first = observer_u32(metadata, 0)
        local count = observer_u32(metadata, 4)
        if not first or not count or first >= 64 or count > 64 then return nil end
        return root, chat, first, count
    end

    local function observer_same_cycle(cycle)
        if not cycle then return false end
        local root, chat, first, count = observer_root_snapshot()
        return root == cycle.root and chat == cycle.chat and first == cycle.first and count == cycle.count
    end

    local function observer_anon_id(address)
        local existing = observer_ids[address]
        if existing then return existing end
        if observer_id_count >= 256 then
            observer_ids = {}
            observer_id_count = 0
        end
        observer_next_id = observer_next_id + 1
        observer_id_count = observer_id_count + 1
        observer_ids[address] = observer_next_id
        return observer_next_id
    end

    local function observer_begin_cycle()
        observer_active_cycle = nil
        local root, chat, first, count = observer_root_snapshot()
        if not root then return nil end
        local verify_root, verify_chat, verify_first, verify_count = observer_root_snapshot()
        if root ~= verify_root or chat ~= verify_chat or first ~= verify_first or count ~= verify_count then return nil end
        local cycle = {
            root = root,
            chat = chat,
            first = first,
            count = count,
            owner_id = observer_anon_id(root),
        }
        observer_active_cycle = cycle
        return {owner_id = cycle.owner_id, first = first, count = count}
    end

    local function observer_read_slot(slot)
        local cycle = observer_active_cycle
        if not cycle or type(slot) ~= "number" or slot ~= math.floor(slot) or slot < 0 or slot >= 64 then return nil end
        local distance = (slot - cycle.first) % 64
        if distance >= cycle.count or not observer_same_cycle(cycle) then return nil end
        local slot_base = observer_add(cycle.chat, slot * 0x228)
        if not slot_base then return nil end
        local timestamp_address = observer_add(slot_base, 0xB90)
        local body_address = observer_add(slot_base, 0xBA0)
        local flags_address = observer_add(slot_base, 0xDA0)
        if not timestamp_address or not body_address or not flags_address then return nil end
        local timestamp = observer_read(timestamp_address, 8)
        local body = observer_read(body_address, 513)
        local flags = observer_read(flags_address, 8)
        if not timestamp or not body or not flags then return nil end
        return {identity = timestamp .. body:sub(1, 8), body = body, flags = flags}
    end

    local OBSERVER_WIDGET_KEY = 0x7518C954
    local OBSERVER_WIDGET_EVENT = 0x1C12037F
    local OBSERVER_WIDGET_ASCII = "HD2CT_PROBE_ASCII_01"
    local OBSERVER_WIDGET_CJK = "HD2CT_PROBE_中文_02"
    local translate_scan_state = {background_next_slot = 0, latest_retries = 0}

    local function observer_widget_context()
        local root_global = observer_add(module_base_number, 0x346D538)
        if not root_global then return nil end
        local root = observer_read_pointer(root_global)
        if not root then return nil end
        local ring = observer_add(root, 0x4F7080)
        if not ring then return nil end
        local metadata_address = observer_add(ring, 0x12D00)
        if not metadata_address then return nil end
        local metadata = observer_read(metadata_address, 8)
        if not metadata then return nil end
        local next_index = observer_u32(metadata, 0)
        local active_count = observer_u32(metadata, 4)
        if next_index == nil or active_count == nil then return nil end
        return {
            root_global = root_global,
            root = root,
            ring = ring,
            metadata_address = metadata_address,
            metadata = metadata,
            next_index = next_index,
            active_count = active_count,
        }
    end

    local function observer_widget_verify_context(context)
        local verify_root = observer_read_pointer(context.root_global)
        if not verify_root then return false, "read_failed" end
        if verify_root ~= context.root then return false, "unstable" end
        local verify_metadata = observer_read(context.metadata_address, 8)
        if not verify_metadata then return false, "read_failed" end
        if verify_metadata ~= context.metadata then return false, "unstable" end
        return true
    end

    local function observer_widget_verify_base(context, count_address, count_bytes, entries_address, entries_bytes)
        local stable, reason = observer_widget_verify_context(context)
        if not stable then return false, reason end
        local verify_count = observer_read(count_address, 1)
        if not verify_count then return false, "read_failed" end
        if verify_count ~= count_bytes then return false, "unstable" end
        local verify_entries = ""
        if #entries_bytes > 0 then
            verify_entries = observer_read(entries_address, #entries_bytes)
            if not verify_entries then return false, "read_failed" end
        end
        if verify_entries ~= entries_bytes then return false, "unstable" end
        return true
    end

    local function observer_widget_verify_values(
        context, count_address, count_bytes, entries_address, entries_bytes,
        event_address, event_bytes, body_address, body_bytes
    )
        local stable, reason = observer_widget_verify_base(
            context, count_address, count_bytes, entries_address, entries_bytes)
        if not stable then return false, reason end
        local verify_event = observer_read(event_address, 4)
        if not verify_event then return false, "read_failed" end
        if verify_event ~= event_bytes then return false, "unstable" end
        if body_bytes ~= nil then
            local verify_body = observer_read(body_address, 1024)
            if not verify_body then return false, "read_failed" end
            if verify_body ~= body_bytes then return false, "unstable" end
        end
        return true
    end

    local function observer_widget_read_widget_slot(slot, allow_body)
        if type(slot) ~= "number" or slot ~= math.floor(slot) or slot < 0 or slot >= 64 then
            return "read_failed"
        end
        -- 为UI、环形历史和二次核验预留总预算，预算不足时不读、不推进槽位。
        if observer_read_budget > MAX_OBSERVER_READ - 4096 then return "deferred" end

        local context = observer_widget_context()
        if not context then return "read_failed" end
        if context.next_index >= 64 or context.active_count > 64 then
            local stable, reason = observer_widget_verify_context(context)
            if not stable then return reason end
            return "count_out_of_range"
        end
        if context.active_count == 0 then
            local stable, reason = observer_widget_verify_context(context)
            if not stable then return reason end
            return "empty"
        end

        local manager = observer_add(context.root, 0x14498)
        local widget_offset = 0x4390 + slot * 0x3D8
        local widget = manager and observer_add(manager, widget_offset)
        local map = widget and observer_add(widget, 0x220)
        local count_address = map and observer_add(map, 0x158)
        local entries_address = map and observer_add(map, 8)
        if not manager or not widget or not map or not count_address or not entries_address then
            return "read_failed"
        end

        local count_bytes = observer_read(count_address, 1)
        if not count_bytes then return "read_failed" end
        local count = count_bytes:byte(1)
        if count == nil then return "read_failed" end
        if count > 14 then
            local stable, reason = observer_widget_verify_base(
                context, count_address, count_bytes, entries_address, "")
            if not stable then return reason end
            return "count_out_of_range"
        end

        local entries_bytes = ""
        if count > 0 then
            entries_bytes = observer_read(entries_address, count * 0x18)
            if not entries_bytes then return "read_failed" end
        end
        if count == 0 then
            local stable, reason = observer_widget_verify_base(
                context, count_address, count_bytes, entries_address, entries_bytes)
            if not stable then return reason end
            return "empty"
        end

        local matching_count = 0
        local value_pointer
        local matching_index
        for index = 0, count - 1 do
            local entry_offset = index * 0x18
            local key = observer_u32(entries_bytes, entry_offset)
            if key == nil then return "read_failed" end
            if key == OBSERVER_WIDGET_KEY then
                matching_count = matching_count + 1
                if matching_count == 1 then
                    value_pointer = observer_u64(entries_bytes, entry_offset + 8)
                    matching_index = index
                end
            end
        end

        local result_status
        local event_slot
        if allow_body and matching_count == 1
            and observer_u32(entries_bytes, matching_index * 0x18 + 4) ~= 1 then
            result_status = "property_type_mismatch"
        elseif matching_count > 1 then
            result_status = "ambiguous_key"
        elseif matching_count == 0 then
            result_status = "key_missing"
        else
            if type(value_pointer) == "number" and value_pointer == math.floor(value_pointer) then
                local stride = 0x4B4
                local delta = value_pointer - context.ring - 0xB4
                if delta == math.floor(delta) and delta >= 0 and delta <= 63 * stride
                    and delta % stride == 0 then
                    local candidate = delta / stride
                    local record = observer_add(context.ring, candidate * stride)
                    local expected_pointer = record and observer_add(record, 0xB4)
                    if not record or not expected_pointer then return "read_failed" end
                    if expected_pointer == value_pointer then event_slot = candidate end
                end
            end
            if event_slot == nil then
                result_status = "pointer_outside"
            elseif (context.next_index - 1 - event_slot) % 64 >= context.active_count then
                result_status = "inactive"
            end
        end

        if result_status ~= nil then
            local stable, reason = observer_widget_verify_base(
                context, count_address, count_bytes, entries_address, entries_bytes)
            if not stable then return reason end
            return result_status
        end

        local record = observer_add(context.ring, event_slot * 0x4B4)
        local event_address = record and observer_add(record, 0)
        local body_address = record and observer_add(record, 0xB4)
        if not record or not event_address or not body_address or body_address ~= value_pointer then
            return "pointer_outside"
        end
        local event_bytes = observer_read(event_address, 4)
        if not event_bytes then return "read_failed" end
        local event_code = observer_u32(event_bytes, 0)
        if event_code == nil then return "read_failed" end
        if event_code ~= OBSERVER_WIDGET_EVENT then
            local stable, reason = observer_widget_verify_values(
                context, count_address, count_bytes, entries_address, entries_bytes,
                event_address, event_bytes, nil, nil)
            if not stable then return reason end
            return "event_type_mismatch"
        end

        local body_bytes = observer_read(body_address, 1024)
        if not body_bytes then return "read_failed" end
        local stable, reason = observer_widget_verify_values(
            context, count_address, count_bytes, entries_address, entries_bytes,
            event_address, event_bytes, body_address, body_bytes)
        if not stable then return reason end

        local terminator = body_bytes:find("\0", 1, true)
        if not terminator then return "missing_terminator" end
        local body = body_bytes:sub(1, terminator - 1)
        if allow_body then
            local owner_id = observer_anon_id(context.root)
            if not owner_id then return "read_failed" end
            return "ok", {
                widget_slot = slot,
                event_slot = event_slot,
                owner_id = owner_id,
                body = body,
                proof = {
                    context = context,
                    widget = widget,
                    map = map,
                    count_address = count_address,
                    count_bytes = count_bytes,
                    entries_address = entries_address,
                    entries_bytes = entries_bytes,
                    key_index = matching_index,
                    event_slot = event_slot,
                    event_address = event_address,
                    event_bytes = event_bytes,
                    body_address = body_address,
                    body_bytes = body_bytes,
                    body = body,
                },
            }
        end
        if body == OBSERVER_WIDGET_ASCII then
            return "ascii", {
                event_slot = event_slot,
                owner_anon_id = observer_anon_id(context.root),
            }, {
                context = context,
                widget = widget,
                map = map,
                count_address = count_address,
                count_bytes = count_bytes,
                entries_address = entries_address,
                entries_bytes = entries_bytes,
                key_index = matching_index,
                event_slot = event_slot,
            }
        end
        if body == OBSERVER_WIDGET_CJK then
            return "cjk", {
                event_slot = event_slot,
                owner_anon_id = observer_anon_id(context.root),
            }
        end
        return "no_match"
    end

    local function observer_display_setter(widget_argument, buffer)
        if not DISPLAY_TEST_ENABLED or not observer_display_native_gate then return false end
        local target = ffi.cast(
            "void (*)(void *, HD2Probe_U32, const char *)",
            ffi.cast("size_t", module_base) + DISPLAY_TARGET_RVA
        )
        target(
            ffi.cast("void *", widget_argument),
            OBSERVER_WIDGET_KEY,
            ffi.cast("const char *", buffer)
        )
        return true
    end

    local function observer_display_pin_buffer()
        local pins = rawget(_G, DISPLAY_PIN_TABLE)
        if pins == nil then
            pins = {}
            rawset(_G, DISPLAY_PIN_TABLE, pins)
        end
        if type(pins) ~= "table" then return nil end
        local pin_count = #pins
        if pin_count < 0 or pin_count >= DISPLAY_MAX_PINS then return nil end

        local buffer = ffi.new("HD2Probe_U8[?]", #DISPLAY_TEST_TEXT + 1)
        for index = 1, #DISPLAY_TEST_TEXT do
            buffer[index - 1] = DISPLAY_TEST_TEXT:byte(index)
        end
        -- FFI 数组初始为零，末字节保留为 C 字符串终止符。
        pins[pin_count + 1] = buffer
        return buffer
    end

    local function observer_display_verify_property(proof, buffer)
        local stable = observer_widget_verify_context(proof.context)
        if not stable then return false end
        local count_bytes = observer_read(proof.count_address, 1)
        if not count_bytes or count_bytes ~= proof.count_bytes then return false end
        local entries_bytes = observer_read(proof.entries_address, #proof.entries_bytes)
        if not entries_bytes or #entries_bytes ~= #proof.entries_bytes then return false end

        local pointer_address = tonumber(ffi.cast("size_t", ffi.cast("void *", buffer)))
        if not pointer_address then return false end
        local matching_count = 0
        local matching_index
        for index = 0, count_bytes:byte(1) - 1 do
            local entry_offset = index * 0x18
            local key = observer_u32(entries_bytes, entry_offset)
            if key == nil then return false end
            local entry_start = entry_offset + 1
            local entry_end = entry_offset + 0x18
            if key == OBSERVER_WIDGET_KEY then
                matching_count = matching_count + 1
                matching_index = index
                if observer_u32(entries_bytes, entry_offset + 4) ~= 1
                    or observer_u64(entries_bytes, entry_offset + 8) ~= pointer_address then
                    return false
                end
                if entries_bytes:sub(entry_start, entry_start + 3)
                    ~= proof.entries_bytes:sub(entry_start, entry_start + 3)
                    or entries_bytes:sub(entry_start + 20, entry_end)
                    ~= proof.entries_bytes:sub(entry_start + 20, entry_end) then
                    return false
                end
            elseif entries_bytes:sub(entry_start, entry_end)
                ~= proof.entries_bytes:sub(entry_start, entry_end) then
                return false
            end
        end
        if matching_count ~= 1 or matching_index ~= proof.key_index then return false end
        return true
    end

    local function observer_display_replace_ascii_widget(slot, event_slot, owner_anon_id)
        if not DISPLAY_TEST_ENABLED or not observer_display_native_gate then return "target_unverified", false end
        if observer_read_budget > MAX_OBSERVER_READ - 4096 then return "deferred", false end

        local status, sample, proof = observer_widget_read_widget_slot(slot)
        if status == "deferred" then return "deferred", false end
        if status == "read_failed" then return "read_failed", false end
        if status ~= "ascii" or type(sample) ~= "table" or type(proof) ~= "table"
            or sample.event_slot ~= event_slot or sample.owner_anon_id ~= owner_anon_id
            or proof.event_slot ~= event_slot then
            return "stale", false
        end

        local widget_argument = observer_add(proof.widget, 0x110)
        if not widget_argument then return "read_failed", false end
        local buffer = observer_display_pin_buffer()
        if not buffer then return "read_failed", false end

        if not observer_display_setter(widget_argument, buffer) then
            return "target_unverified", false
        end
        if observer_display_verify_property(proof, buffer) then
            return "called_confirmed", true
        end
        return "called_unconfirmed", false
    end

    local function observer_translate_prepare_paths()
        prepare_observer_paths()
        local parent, parent_length = append_wide_ascii(
            observer_local_app, observer_local_app_length, "\\HD2ChatTranslate")
        local mailbox, mailbox_length = append_wide_ascii(
            observer_local_app, observer_local_app_length, "\\HD2ChatTranslate\\mailbox")
        if not parent or not mailbox then error("translation mailbox path unavailable") end
        kernel.CreateDirectoryW(parent, nil)
        kernel.CreateDirectoryW(mailbox, nil)
        translate_mailbox_directory = mailbox
        translate_mailbox_directory_length = mailbox_length
        translate_heartbeat_path = append_wide_ascii(mailbox, mailbox_length, "\\bridge.flag")
        if not translate_heartbeat_path then error("translation heartbeat path unavailable") end

        translate_session_id = string.format("hd2ct_%d_%08x", observer_session_time, observer_session_nonce)
        if #translate_session_id > 80 or translate_session_id:find("[^A-Za-z0-9_%-]") then
            error("translation session unavailable")
        end
        translate_report_path = append_wide_ascii(
            mailbox, mailbox_length, "\\chat-translate-" .. translate_session_id .. ".json")
        if not translate_report_path then error("translation report path unavailable") end
    end

    local function observer_translate_path(suffix)
        return append_wide_ascii(translate_mailbox_directory, translate_mailbox_directory_length, suffix)
    end

    local function observer_translate_read_file(path, maximum)
        local file = kernel.CreateFileW(path, 0x80000000, 0x7, nil, 3, 0x80, nil)
        if file == nil or file == invalid_handle then return nil end
        local buffer = ffi.new("HD2Probe_U8[?]", maximum)
        local bytes_read = ffi.new("HD2Probe_U32[1]")
        local read_ok = kernel.ReadFile(file, buffer, maximum, bytes_read, nil)
        local close_ok = kernel.CloseHandle(file) ~= 0
        if read_ok == 0 or not close_ok then return nil end
        local length = tonumber(bytes_read[0])
        if not length or length > maximum then return nil end
        return ffi.string(buffer, length)
    end

    local function observer_translate_heartbeat_is_fresh(raw)
        translate_heartbeat_fresh = false
        if type(raw) ~= "string" or #raw == 0 or #raw > 64 then return false end
        local digits = raw:match("^HD2CT1 ([0-9]+)\n$")
        if not digits then return false end
        local started = tonumber(digits)
        if not started or started ~= math.floor(started) or started > observer_now then return false end
        translate_heartbeat_fresh = observer_now - started <= 3000
        return translate_heartbeat_fresh
    end

    local function observer_translate_heartbeat(force)
        if STANDALONE_ENABLED then
            if not force and observer_now < translate_heartbeat_next_poll then
                observer_translate_heartbeat_is_fresh(translate_cached_heartbeat)
                return translate_cached_heartbeat
            end
            local read_completed_ms = tonumber(kernel.GetTickCount64())
            if not read_completed_ms or read_completed_ms < 0
                or read_completed_ms ~= math.floor(read_completed_ms)
                or read_completed_ms < observer_now then
                error("translation clock failure")
            end
            observer_now = read_completed_ms
            translate_heartbeat_next_poll = observer_now + 250
            translate_cached_heartbeat = nil
            if native_transport_api then
                translate_cached_heartbeat = string.format("HD2CT1 %.0f\n", observer_now)
            end
            observer_translate_heartbeat_is_fresh(translate_cached_heartbeat)
            return translate_cached_heartbeat
        end
        if not force and observer_now < translate_heartbeat_next_poll then
            observer_translate_heartbeat_is_fresh(translate_cached_heartbeat)
            return translate_cached_heartbeat
        end
        translate_cached_heartbeat = observer_translate_read_file(translate_heartbeat_path, 65)
        -- 用读取完成后的同一单调时钟检查时间戳，避免发布中的新心跳被旧时刻误判。
        local read_completed_ms = tonumber(kernel.GetTickCount64())
        if not read_completed_ms or read_completed_ms < 0
            or read_completed_ms ~= math.floor(read_completed_ms)
            or read_completed_ms < observer_now then
            error("translation clock failure")
        end
        observer_now = read_completed_ms
        translate_heartbeat_next_poll = observer_now + 250
        if translate_cached_heartbeat and #translate_cached_heartbeat > 64 then
            translate_cached_heartbeat = nil
        end
        observer_translate_heartbeat_is_fresh(translate_cached_heartbeat)
        return translate_cached_heartbeat
    end

    local function observer_translate_refresh_for_setter()
        local now_ms = tonumber(kernel.GetTickCount64())
        if not now_ms or now_ms < 0 or now_ms ~= math.floor(now_ms)
            or now_ms < observer_now then
            return false
        end
        observer_now = now_ms
        observer_translate_heartbeat(true)
        return translate_heartbeat_fresh
    end

    local function observer_translate_valid_token(token)
        if type(token) ~= "string" or #token < 3 or #token > 128 or not translate_session_id then
            return false
        end
        local prefix = translate_session_id .. "_"
        if token:sub(1, #prefix) ~= prefix then return false end
        local counter = token:sub(#prefix + 1)
        if counter == "" or counter:find("[^0-9]") then return false end
        local numeric = tonumber(counter)
        return numeric ~= nil and numeric > 0 and numeric == math.floor(numeric)
            and numeric <= 9007199254740991 and string.format("%.0f", numeric) == counter
    end

    local function observer_translate_publish_request(token, body)
        if STANDALONE_ENABLED then
            if not TRANSLATE_ENABLED or not translate_heartbeat_fresh
                or not observer_translate_valid_token(token)
                or not native_transport_api
                or type(body) ~= "string" or #body == 0 or #body > TRANSLATE_MAX_REQUEST_BYTES
                or not translate_core or type(translate_core.valid_text) ~= "function"
                or not translate_core.valid_text(body, TRANSLATE_MAX_REQUEST_BYTES) then
                return false
            end
            local ok, submitted = pcall(function()
                return native_transport_api.submit(token, body)
            end)
            if not ok then return false, "SUBMIT_EXCEPTION" end
            return submitted == true
        end
        if not TRANSLATE_ENABLED or not translate_heartbeat_fresh
            or not observer_translate_valid_token(token)
            or type(body) ~= "string" or #body == 0 or #body > TRANSLATE_MAX_REQUEST_BYTES
            or not translate_core or type(translate_core.valid_text) ~= "function"
            or not translate_core.valid_text(body, TRANSLATE_MAX_REQUEST_BYTES)
            or translate_owned_tokens[token] then
            return false
        end
        local final_path = observer_translate_path("\\" .. token .. ".req")
        if not final_path then return false end

        for attempt = 1, 16 do
            translate_file_sequence = translate_file_sequence + 1
            local temporary_path = observer_translate_path(string.format(
                "\\.%s.req.%08x.%02d.tmp", token, translate_file_sequence, attempt))
            if not temporary_path then return false end
            local file = kernel.CreateFileW(temporary_path, 0x40000000, 0, nil, 1, 0x80, nil)
            if file ~= nil and file ~= invalid_handle then
                local bytes_written = ffi.new("HD2Probe_U32[1]")
                local write_ok = kernel.WriteFile(
                    file, ffi.cast("const char *", body), #body, bytes_written, nil)
                local flush_ok = write_ok ~= 0 and tonumber(bytes_written[0]) == #body
                    and kernel.FlushFileBuffers(file) ~= 0
                local close_ok = kernel.CloseHandle(file) ~= 0
                if flush_ok and close_ok and kernel.MoveFileExW(temporary_path, final_path, 0x8) ~= 0 then
                    translate_owned_tokens[token] = true
                    return true
                end
                kernel.DeleteFileW(temporary_path)
                return false
            end
        end
        return false
    end

    local function observer_translate_read_response(token)
        if STANDALONE_ENABLED then
            if not observer_translate_valid_token(token) or not native_transport_api then
                return nil
            end
            local ok, response = pcall(native_transport_api.response, token)
            if not ok then return "ERR\nRESPONSE_EXCEPTION" end
            return response
        end
        if not translate_owned_tokens[token] or not observer_translate_valid_token(token) then return nil end
        local path = observer_translate_path("\\" .. token .. ".res")
        if not path then return nil end
        local raw = observer_translate_read_file(path, TRANSLATE_MAX_RESPONSE_BYTES + 1)
        if not raw then return nil end
        if #raw > TRANSLATE_MAX_RESPONSE_BYTES then return "" end
        return raw
    end

    local function observer_translate_cancel(token)
        if STANDALONE_ENABLED then
            if not native_transport_api then return false end
            if token == nil then return native_transport_api.cancel(nil) == true end
            if not observer_translate_valid_token(token) then return false end
            return native_transport_api.cancel(token) == true
        end
        if not translate_owned_tokens[token] or not observer_translate_valid_token(token) then return false end
        local request_path = observer_translate_path("\\" .. token .. ".req")
        local response_path = observer_translate_path("\\" .. token .. ".res")
        if request_path then kernel.DeleteFileW(request_path) end
        if response_path then kernel.DeleteFileW(response_path) end
        translate_owned_tokens[token] = nil
        return true
    end

    local function observer_translate_pin_text(text)
        local max_display_bytes = translate_core and translate_core.MAX_DISPLAY_BYTES
        if type(text) ~= "string" or #text == 0 or type(max_display_bytes) ~= "number"
            or #text > max_display_bytes
            or not translate_core or type(translate_core.valid_text) ~= "function"
            or not translate_core.valid_text(text, max_display_bytes) then
            return nil, "read_failed"
        end
        local registry = rawget(_G, TRANSLATE_PIN_TABLE)
        if registry == nil then
            registry = {buffers = {}, bytes = 0}
            rawset(_G, TRANSLATE_PIN_TABLE, registry)
        end
        if type(registry) ~= "table" or type(registry.buffers) ~= "table"
            or type(registry.bytes) ~= "number" or registry.bytes < 0
            or registry.bytes ~= math.floor(registry.bytes) then
            return nil, "capacity"
        end
        local pin_count = #registry.buffers
        local byte_count = #text + 1
        if pin_count >= TRANSLATE_MAX_PINS
            or registry.bytes > TRANSLATE_MAX_PIN_BYTES - byte_count then
            return nil, "capacity"
        end

        local buffer = ffi.new("HD2Probe_U8[?]", byte_count)
        ffi.copy(buffer, text, #text)
        -- 数组的最后一字节由FFI零初始化，登记后始终保留本次译文指针。
        registry.buffers[pin_count + 1] = buffer
        registry.bytes = registry.bytes + byte_count
        return buffer
    end

    local function observer_translate_verify_apply(proof, buffer)
        local context = proof.context
        local root_before = observer_read_pointer(context.root_global)
        if root_before ~= context.root then return false end
        local metadata_before = observer_read(context.metadata_address, 8)
        if not metadata_before then return false end
        local next_index = observer_u32(metadata_before, 0)
        local active_count = observer_u32(metadata_before, 4)
        if next_index == nil or next_index >= 64 or active_count == nil or active_count > 64
            or (next_index - 1 - proof.event_slot) % 64 >= active_count then
            return false
        end

        local record = observer_add(context.ring, proof.event_slot * 0x4B4)
        local event_address = record and observer_add(record, 0)
        local body_address = record and observer_add(record, 0xB4)
        if not record or not event_address or not body_address then return false end
        local event_before = observer_read(event_address, 4)
        local body_before = observer_read(body_address, 1024)
        if not event_before or observer_u32(event_before, 0) ~= OBSERVER_WIDGET_EVENT
            or not body_before or body_before ~= proof.body_bytes then
            return false
        end
        local terminator = body_before:find("\0", 1, true)
        if not terminator or body_before:sub(1, terminator - 1) ~= proof.body then return false end

        local count_before = observer_read(proof.count_address, 1)
        if not count_before or count_before ~= proof.count_bytes then return false end
        local entries_before = observer_read(proof.entries_address, #proof.entries_bytes)
        if not entries_before or #entries_before ~= #proof.entries_bytes then return false end

        local root_after = observer_read_pointer(context.root_global)
        local metadata_after = observer_read(context.metadata_address, 8)
        local event_after = observer_read(event_address, 4)
        local body_after = observer_read(body_address, 1024)
        if root_after ~= root_before or metadata_after ~= metadata_before
            or event_after ~= event_before or body_after ~= body_before then
            return false
        end
        local count_after = observer_read(proof.count_address, 1)
        local entries_after = observer_read(proof.entries_address, #proof.entries_bytes)
        if not count_after or count_after ~= count_before
            or not entries_after or entries_after ~= entries_before then
            return false
        end

        local pointer_address = tonumber(ffi.cast("size_t", ffi.cast("void *", buffer)))
        if not pointer_address then return false end
        local matching_count = 0
        local matching_index
        local entry_count = count_after:byte(1)
        if not entry_count or entry_count == 0 or entry_count > 14
            or #entries_after ~= entry_count * 0x18 then
            return false
        end
        for index = 0, entry_count - 1 do
            local entry_offset = index * 0x18
            local key = observer_u32(entries_after, entry_offset)
            if key == nil then return false end
            local entry_start = entry_offset + 1
            local entry_end = entry_offset + 0x18
            if key == OBSERVER_WIDGET_KEY then
                matching_count = matching_count + 1
                matching_index = index
                if observer_u32(entries_after, entry_offset + 4) ~= 1
                    or observer_u64(entries_after, entry_offset + 8) ~= pointer_address
                    or entries_after:sub(entry_start, entry_start + 3)
                        ~= proof.entries_bytes:sub(entry_start, entry_start + 3)
                    or entries_after:sub(entry_start + 20, entry_end)
                        ~= proof.entries_bytes:sub(entry_start + 20, entry_end) then
                    return false
                end
            elseif entries_after:sub(entry_start, entry_end)
                ~= proof.entries_bytes:sub(entry_start, entry_end) then
                return false
            end
        end
        return matching_count == 1 and matching_index == proof.key_index
    end

    local function observer_translate_setter(widget_argument, buffer)
        if not TRANSLATE_ENABLED or not observer_display_native_gate then return false end
        local target = ffi.cast(
            "void (*)(void *, HD2Probe_U32, const char *)",
            ffi.cast("size_t", module_base) + DISPLAY_TARGET_RVA
        )
        target(
            ffi.cast("void *", widget_argument),
            OBSERVER_WIDGET_KEY,
            ffi.cast("const char *", buffer)
        )
        return true
    end

    local function observer_translate_read_slot(slot)
        if not TRANSLATE_ENABLED or not observer_translate_heartbeat_is_fresh(translate_cached_heartbeat) then
            return "stale"
        end
        local status, message = observer_widget_read_widget_slot(slot, true)
        if status == "deferred" then return "deferred" end
        if status == "unstable" then return "stale" end
        if status == "read_failed" or status == "count_out_of_range" then return "read_failed" end
        if status == "event_type_mismatch" then return "filtered_event" end
        if status ~= "ok" or type(message) ~= "table" then return "empty" end
        if type(message.body) ~= "string" or #message.body == 0
            or #message.body > TRANSLATE_MAX_REQUEST_BYTES
            or not translate_core or type(translate_core.valid_text) ~= "function"
            or not translate_core.valid_text(message.body, TRANSLATE_MAX_REQUEST_BYTES) then
            return "empty"
        end
        return "ok", message
    end

    local function observer_translate_scan_plan()
        if not TRANSLATE_ENABLED then return nil end
        local context = observer_widget_context()
        if not context or context.next_index >= 64 or context.active_count > 64 then return nil end
        local owner_id = observer_anon_id(context.root)
        local owner_changed = translate_scan_state.owner_id ~= nil
            and translate_scan_state.owner_id ~= owner_id

        if context.active_count == 0 then
            local stable = observer_widget_verify_context(context)
            if not stable then return nil end
            translate_scan_state.owner_id = owner_id
            translate_scan_state.head = nil
            translate_scan_state.count = nil
            translate_scan_state.ring_next = context.next_index
            translate_scan_state.ring_count = context.active_count
            translate_scan_state.latest_retries = 0
            if owner_changed then translate_scan_state.background_next_slot = 0 end
            return {owner_id = owner_id, reset_owner = owner_changed, slots = {}}
        end

        local manager = observer_add(context.root, 0x14498)
        local history_address = manager
            and observer_add(manager, 0x13990)
        if not history_address then return nil end
        local first_history = observer_read(history_address, 0x34)
        if not first_history then return nil end
        local first_head = observer_u32(first_history, 0)
        local first_count = observer_u32(first_history, 0x30)
        local second_history = observer_read(history_address, 0x34)
        if not second_history then return nil end
        local head = observer_u32(second_history, 0)
        local count = observer_u32(second_history, 0x30)
        if first_head ~= head or first_count ~= count or head == nil or head >= 64
            or count == nil or count > 64 then
            return nil
        end
        local stable = observer_widget_verify_context(context)
        if not stable then return nil end

        local previous_head = translate_scan_state.head
        local previous_count = translate_scan_state.count
        local previous_ring_next = translate_scan_state.ring_next
        local previous_ring_count = translate_scan_state.ring_count
        local slots = {}
        local included = {}
        local function add_slot(slot)
            if #slots >= 64 or included[slot] then return end
            included[slot] = true
            slots[#slots + 1] = slot
        end
        local function add_all_slots()
            add_slot((head + 63) % 64)
            for slot = 0, 63 do add_slot(slot) end
        end

        if owner_changed then
            translate_scan_state.owner_id = owner_id
            translate_scan_state.head = head
            translate_scan_state.count = count
            translate_scan_state.ring_next = context.next_index
            translate_scan_state.ring_count = context.active_count
            translate_scan_state.latest_retries = 0
            translate_scan_state.background_next_slot = 0
            return {owner_id = owner_id, reset_owner = true, slots = slots}
        end

        local resync = previous_head == nil or previous_count == nil
            or previous_ring_next == nil or previous_ring_count == nil
        local delta = 0
        if not resync then
            delta = (head - previous_head) % 64
            local expected_count = math.min(64, previous_count + delta)
            if (count == 0) ~= (previous_count == 0)
                or count < previous_count or count ~= expected_count then
                resync = true
            elseif delta == 0 and count == 64 and previous_count == 64
                and (context.next_index ~= previous_ring_next
                    or context.active_count ~= previous_ring_count) then
                resync = true
            end
        end

        local history_changed = previous_head == nil or previous_count == nil
            or head ~= previous_head or count ~= previous_count
        local event_changed = previous_ring_next == nil or previous_ring_count == nil
            or context.next_index ~= previous_ring_next
            or context.active_count ~= previous_ring_count
        local latest_retries = translate_scan_state.latest_retries or 0
        local metadata_changed = history_changed or event_changed

        if resync then
            add_all_slots()
        else
            for offset = 0, delta - 1 do add_slot((previous_head + offset) % 64) end
            if metadata_changed or latest_retries > 0 then add_slot((head + 63) % 64) end
        end

        local background_next_slot = translate_scan_state.background_next_slot
        local function history_slot_active(slot)
            return count > 0 and (head - 1 - slot) % 64 < count
        end
        if not resync then
            for offset = 0, 1 do
                local slot = (background_next_slot + offset) % 64
                if count == 0 or history_slot_active(slot) then add_slot(slot) end
            end
        end

        local next_latest_retries
        if metadata_changed then
            next_latest_retries = 2
        elseif latest_retries > 0 then
            next_latest_retries = latest_retries - 1
        else
            next_latest_retries = 0
        end

        translate_scan_state.owner_id = owner_id
        translate_scan_state.head = head
        translate_scan_state.count = count
        translate_scan_state.ring_next = context.next_index
        translate_scan_state.ring_count = context.active_count
        translate_scan_state.latest_retries = next_latest_retries
        translate_scan_state.background_next_slot = (background_next_slot + 2) % 64
        return {owner_id = owner_id, slots = slots}
    end

    local function observer_translate_apply(message, text)
        local function timing_start()
            local ok, value = pcall(kernel.GetTickCount64)
            if not ok then return nil end
            value = tonumber(value)
            if type(value) ~= "number" or value ~= value or value == math.huge
                or value == -math.huge or value < 0 then
                return nil
            end
            return value
        end

        local function timing_finish(layout, name, started)
            if started == nil then return end
            local finished = timing_start()
            if finished == nil or finished < started then return end
            local elapsed = finished - started
            if elapsed < 0 or elapsed ~= math.floor(elapsed)
                or type(layout.record_timing) ~= "function" then
                return
            end
            pcall(layout.record_timing, name, elapsed)
        end

        if not TRANSLATE_ENABLED or not observer_display_native_gate then return "disabled" end
        if observer_read_budget > MAX_OBSERVER_READ - FULL_APPLY_READ_RESERVE then return "deferred" end
        observer_translate_heartbeat(true)
        if not translate_heartbeat_fresh then return "stale" end
        local max_display_bytes = translate_core and translate_core.MAX_DISPLAY_BYTES
        if type(message) ~= "table" or type(message.proof) ~= "table"
            or type(message.proof.context) ~= "table"
            or type(message.body) ~= "string" or #message.body == 0
            or not translate_core or type(translate_core.valid_text) ~= "function"
            or not translate_core.valid_text(message.body, TRANSLATE_MAX_REQUEST_BYTES)
            or type(text) ~= "string" or type(max_display_bytes) ~= "number"
            or not translate_core.valid_text(text, max_display_bytes) then
            return "read_failed"
        end
        if text == message.body then return "stale" end

        local status, current = observer_widget_read_widget_slot(message.widget_slot, true)
        if status == "deferred" then return "deferred" end
        if status == "read_failed" or status == "count_out_of_range" then return "read_failed" end
        if status ~= "ok" or type(current) ~= "table" or type(current.proof) ~= "table"
            or current.widget_slot ~= message.widget_slot
            or current.event_slot ~= message.event_slot
            or current.owner_id ~= message.owner_id
            or current.body ~= message.body
            or current.proof.context.root ~= message.proof.context.root
            or current.proof.widget ~= message.proof.widget
            or current.proof.map ~= message.proof.map
            or current.proof.key_index ~= message.proof.key_index then
            return "stale"
        end

        local layout = translate_layout.instance
        if type(layout) ~= "table" or type(layout.prepare) ~= "function"
            or type(layout.reflow) ~= "function" then return "disabled" end
        local prepare_started = timing_start()
        local layout_status, layout_snapshot = layout.prepare(
            current.proof.context, current.proof.widget)
        timing_finish(layout, "apply_prepare", prepare_started)
        if layout_status == "deferred" then return "deferred" end
        if layout_status == "stale" then return "stale" end
        if layout_status == "read_failed" then return "read_failed" end
        if layout_status ~= "ready" or type(layout_snapshot) ~= "table" then return "disabled" end

        local buffer, pin_status = observer_translate_pin_text(text)
        if not buffer then return pin_status == "capacity" and "capacity" or "read_failed" end
        local widget_argument = observer_add(current.proof.widget, 0x110)
        if not widget_argument then return "read_failed" end
        -- 原生调用前刷新单调时钟和固定心跳，失效时绝不进入游戏函数。
        if not observer_translate_refresh_for_setter() then return "disabled" end

        local verify_started = timing_start()
        local prepared_ok, prepared_status = pcall(layout.verify_prepared, layout_snapshot)
        if not prepared_ok then
            timing_finish(layout, "apply_verify", verify_started)
            if type(layout.disable) == "function" then layout.disable(true) end
            return "read_failed"
        end
        if prepared_status == "deferred" then
            timing_finish(layout, "apply_verify", verify_started)
            return "deferred"
        end
        if prepared_status == "stale" then
            timing_finish(layout, "apply_verify", verify_started)
            return "stale"
        end
        if prepared_status ~= "ready" then
            timing_finish(layout, "apply_verify", verify_started)
            return "read_failed"
        end

        local values_ok, values_stable, values_reason = pcall(
            observer_widget_verify_values,
            current.proof.context,
            current.proof.count_address,
            current.proof.count_bytes,
            current.proof.entries_address,
            current.proof.entries_bytes,
            current.proof.event_address,
            current.proof.event_bytes,
            current.proof.body_address,
            current.proof.body_bytes
        )
        timing_finish(layout, "apply_verify", verify_started)
        if not values_ok then
            if type(layout.disable) == "function" then layout.disable(true) end
            return "read_failed"
        end
        if not values_stable then
            if values_reason == "unstable" then return "stale" end
            if values_reason == "budget_exhausted" then return "deferred" end
            return "read_failed"
        end

        local setter_started = timing_start()
        local setter_ok, setter_result = pcall(observer_translate_setter, widget_argument, buffer)
        timing_finish(layout, "apply_setter", setter_started)
        if not setter_ok or setter_result ~= true then
            if type(layout.disable) == "function" then layout.disable(true, 11) end
            return "called_unconfirmed"
        end
        local verify_apply_started = timing_start()
        local verify_ok, verify_result = pcall(observer_translate_verify_apply, current.proof, buffer)
        timing_finish(layout, "apply_verify_apply", verify_apply_started)
        if not verify_ok or not verify_result then
            if type(layout.disable) == "function" then layout.disable(true, 11) end
            return "called_unconfirmed"
        end
        local reflow_started = timing_start()
        local reflow_ok, reflow_status = pcall(layout.reflow, layout_snapshot, current.proof.widget)
        timing_finish(layout, "apply_reflow", reflow_started)
        if not reflow_ok then
            if type(layout.disable) == "function" then layout.disable(true) end
            return "called_unconfirmed"
        end
        if reflow_status ~= "called_confirmed" then
            if type(layout.disable) == "function" then layout.disable() end
            return "called_unconfirmed"
        end
        return "called_confirmed"
    end

    local function observer_finish_cycle()
        local cycle = observer_active_cycle
        if not observer_same_cycle(cycle) then return nil end
        observer_active_cycle = nil
        return {owner_id = cycle.owner_id, first = cycle.first, count = cycle.count}
    end

    local function observer_path_with_suffix(suffix)
        return append_wide_ascii(observer_directory, observer_directory_length, suffix)
    end

    local function observer_write_report(manifest)
        local encoded = core.encode_json(manifest)
        if type(encoded) ~= "string" or #encoded > 256 * 1024 then error("observer report limit") end
        kernel.CreateDirectoryW(append_wide_ascii(observer_local_app, observer_local_app_length, "\\HD2ChatTranslate"), nil)
        kernel.CreateDirectoryW(observer_directory, nil)

        local final_suffix = string.format("\\chat-observe-%d-%08x.json", observer_session_time, observer_session_nonce)
        local final_path = observer_path_with_suffix(final_suffix)
        if not final_path then error("observer report path") end
        local bytes = ffi.new("HD2Probe_U8[?]", #encoded)
        if #encoded > 0 then ffi.copy(bytes, encoded, #encoded) end
        local written = ffi.new("HD2Probe_U32[1]")
        local file, temporary_path
        for attempt = 1, 16 do
            observer_output_sequence = observer_output_sequence + 1
            local suffix = string.format("\\chat-observe-%d-%08x-%08x-%02d.tmp",
                observer_session_time, observer_session_nonce, observer_output_sequence, attempt)
            temporary_path = observer_path_with_suffix(suffix)
            if not temporary_path then error("observer temporary path") end
            file = kernel.CreateFileW(temporary_path, 0x40000000, 0, nil, 1, 0x80, nil)
            if file ~= nil and file ~= invalid_handle then break end
            file = nil
        end
        if not file then error("observer temporary file unavailable") end

        local write_ok = kernel.WriteFile(file, bytes, #encoded, written, nil) ~= 0
            and tonumber(written[0]) == #encoded
        local flush_ok = write_ok and kernel.FlushFileBuffers(file) ~= 0
        local close_ok = kernel.CloseHandle(file) ~= 0
        if not write_ok or not flush_ok or not close_ok then
            kernel.DeleteFileW(temporary_path)
            error("observer report write failed")
        end
        if kernel.MoveFileExW(temporary_path, final_path, 0x1 + 0x8) == 0 then
            kernel.DeleteFileW(temporary_path)
            error("observer report replace failed")
        end
        return true
    end

    local TRANSLATE_REPORT_STATUSES = {
        target_unverified = true,
        inactive = true,
        baseline = true,
        ready = true,
        pending = true,
        applying = true,
        stopped = true,
    }
    local TRANSLATE_REPORT_CODES = {
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
    local TRANSLATE_REPORT_COUNTERS = {
        "steps", "active_steps", "heartbeat_checks", "heartbeat_misses", "heartbeat_errors",
        "heartbeat_invalid", "heartbeat_stale", "slot_reads", "slot_empty", "slot_stale",
        "slot_event_filtered",
        "slot_read_failed", "slot_deferred", "invalid_messages", "duplicates", "pending_observations",
        "queue_full", "submit_throttled", "submit_attempts", "submit_failures", "submitted", "cancelled",
        "cancel_errors", "responses_waiting", "response_errors", "response_invalid", "translation_errors",
        "translation_unchanged", "translations_ready", "expired", "apply_attempts", "apply_confirmed",
        "error_displays_ready",
        "apply_unconfirmed", "apply_stale", "apply_deferred", "apply_capacity", "apply_read_failed",
        "apply_disabled", "apply_errors", "adapter_errors", "output_errors",
    }

    local function observer_translate_safe_count(value, maximum)
        return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
            and value >= 0 and value == math.floor(value) and value <= maximum
    end

    local function observer_translate_sanitize_report(manifest)
        local value = type(manifest) == "table" and manifest or {}
        local status = TRANSLATE_REPORT_STATUSES[value.status] and value.status or "stopped"
        local code = TRANSLATE_REPORT_CODES[value.code] and value.code or nil
        local counters = {}
        local raw_counters = type(value.counters) == "table" and value.counters or {}
        for _, name in ipairs(TRANSLATE_REPORT_COUNTERS) do
            local count = raw_counters[name]
            counters[name] = observer_translate_safe_count(count, 9007199254740991) and count or 0
        end
        local report = {
            schema_version = 1,
            mode = STANDALONE_ENABLED and "standalone" or "companion",
            status = status,
            code = code,
            transport = STANDALONE_ENABLED and "in_process_winhttp" or "companion_mailbox",
            done = value.done == true,
            heartbeat_active = value.heartbeat_active == true,
            baseline_remaining = observer_translate_safe_count(value.baseline_remaining, 64)
                and value.baseline_remaining or 0,
            pending_count = observer_translate_safe_count(value.pending_count, 32) and value.pending_count or 0,
            counters = counters,
            layout = {
                verified = translate_layout.verified == true,
                reflows_confirmed = 0,
                failures = 0,
                rows_positioned = 0,
                positions_skipped = 0,
                last_failure_code = 0,
                last_history_head = 0,
                last_history_count = 0,
                last_geometry_slot = 0,
                last_scale_milli = 0,
                last_height_milli = 0,
                last_scale_class = 0,
                last_height_class = 0,
            },
        }
        if type(translate_layout.instance) == "table"
            and type(translate_layout.instance.stats) == "function" then
            local stats_ok, stats = pcall(translate_layout.instance.stats)
            if stats_ok and type(stats) == "table" then
                report.layout.verified = stats.verified == true
                for _, name in ipairs({"reflows_confirmed", "failures", "rows_positioned", "positions_skipped"}) do
                    local count = stats[name]
                    report.layout[name] = observer_translate_safe_count(count, 9007199254740991)
                        and count or 0
                end
                for _, name in ipairs({
                    "apply_prepare", "apply_verify", "apply_setter", "apply_verify_apply",
                    "apply_reflow", "apply_total", "measure", "position", "read_slot",
                    "scan_plan", "submit", "response", "report",
                }) do
                    for _, suffix in ipairs({"last_ms", "max_ms"}) do
                        local field = name .. "_" .. suffix
                        local milliseconds = stats[field]
                        report.layout[field] = observer_translate_safe_count(milliseconds, 9007199254740991)
                            and milliseconds or 0
                    end
                end
                for _, field in ipairs({
                    {"last_failure_code", 12},
                    {"last_history_head", 65},
                    {"last_history_count", 65},
                    {"last_geometry_slot", 63},
                    {"last_scale_milli", 1000000000},
                    {"last_height_milli", 1000000000},
                    {"last_scale_class", 5},
                    {"last_height_class", 5},
                }) do
                    local count = stats[field[1]]
                    report.layout[field[1]] = observer_translate_safe_count(count, field[2])
                        and count or 0
                end
            end
        end
        return report
    end

    local function observer_translate_write_report(manifest)
        local sanitized = observer_translate_sanitize_report(manifest)
        local encoded = core.encode_json(sanitized)
        if type(encoded) ~= "string" or #encoded > TRANSLATE_MAX_REPORT_BYTES then
            error("translation report limit")
        end
        local bytes = ffi.new("HD2Probe_U8[?]", math.max(#encoded, 1))
        if #encoded > 0 then ffi.copy(bytes, encoded, #encoded) end
        local written = ffi.new("HD2Probe_U32[1]")
        local file, temporary_path
        for attempt = 1, 16 do
            translate_file_sequence = translate_file_sequence + 1
            temporary_path = observer_translate_path(string.format(
                "\\.chat-translate-%s-%08x-%02d.tmp", translate_session_id,
                translate_file_sequence, attempt))
            if not temporary_path then error("translation temporary path unavailable") end
            file = kernel.CreateFileW(temporary_path, 0x40000000, 0, nil, 1, 0x80, nil)
            if file ~= nil and file ~= invalid_handle then break end
            file = nil
        end
        if not file then error("translation temporary file unavailable") end
        local write_ok = kernel.WriteFile(file, bytes, #encoded, written, nil) ~= 0
            and tonumber(written[0]) == #encoded
        local close_ok = kernel.CloseHandle(file) ~= 0
        if not write_ok or not close_ok then
            kernel.DeleteFileW(temporary_path)
            error("translation report write failed")
        end
        if kernel.MoveFileExW(temporary_path, translate_report_path, 0x1) == 0 then
            kernel.DeleteFileW(temporary_path)
            error("translation report replace failed")
        end
        return true
    end

    local function observer_take_command()
        if observer_now < observer_next_command_poll then return nil end
        observer_next_command_poll = observer_now + 250
        local file = kernel.CreateFileW(observer_request_path, 0x80000000, 0x3, nil, 3, 0x80, nil)
        if file == nil or file == invalid_handle then return nil end
        local buffer = ffi.new("HD2Probe_U8[65]")
        local received = ffi.new("HD2Probe_U32[1]")
        local ok = kernel.ReadFile(file, buffer, 65, received, nil) ~= 0
        kernel.CloseHandle(file)
        if not ok then return nil end
        if kernel.DeleteFileW(observer_request_path) == 0 then return nil end
        local length = tonumber(received[0])
        if not length or length < 1 or length > 64 then return nil end
        local command = ffi.string(buffer, length)
        if command:sub(-2) == "\r\n" then command = command:sub(1, -3)
        elseif command:sub(-1) == "\n" then command = command:sub(1, -2) end
        if command == "" then return nil end
        -- 按字节拒绝非ASCII、NUL和多行内容，避免Lua 5.1含NUL模式解析异常。
        for index = 1, #command do
            local byte = command:byte(index)
            if byte == 0 or byte == 10 or byte == 13 or byte > 0x7f then return nil end
        end
        if command == "startup" or command == "chat_closed" or command == "chat_open" or command == "chat_sent" then
            return command
        end
        return nil
    end

    local function observer_ui_failure(stage, reason, read_size, observed_count)
        if type(read_size) ~= "number" or read_size < 0 or read_size > 4096 then read_size = 0 end
        if type(reason) ~= "string" then reason = "read_failed" end
        local diagnostic = {
            version = 1,
            stage = stage,
            reason = reason,
            read_size = read_size,
            budget_used = observer_read_budget,
        }
        if type(observed_count) == "number" and observed_count == math.floor(observed_count)
            and observed_count >= 0 and observed_count <= 0xffffffff then
            diagnostic.observed_count = observed_count
        end
        return nil, diagnostic
    end

    local function observer_ui_snapshot()
        local owner_global = observer_add(module_base_number, 0x347CE28)
        if not owner_global then return observer_ui_failure("owner_ptr", "pointer_range", 0) end
        local owner, owner_reason = observer_read_pointer(owner_global)
        if not owner then return observer_ui_failure("owner_ptr", owner_reason, 8) end
        local stack_address = observer_add(owner, 0x429C)
        if not stack_address then return observer_ui_failure("stack_read", "pointer_range", 0) end
        local stack_bytes, stack_reason = observer_read(stack_address, 24)
        if not stack_bytes then return observer_ui_failure("stack_read", stack_reason, 24) end
        local depth = observer_u32(stack_bytes, 20)
        if depth == nil then return observer_ui_failure("screen_depth", "malformed_bytes", 24) end
        if depth > 5 then return observer_ui_failure("screen_depth", "value_out_of_range", 24) end
        local screen_ids = {}
        for index = 0, depth - 1 do
            local screen_id = observer_u32(stack_bytes, index * 4)
            if screen_id == nil then
                return observer_ui_failure("screen_id_decode", "malformed_bytes", 24)
            end
            screen_ids[#screen_ids + 1] = screen_id
        end

        local dispatch_global = observer_add(module_base_number, 0x3326E68)
        if not dispatch_global then return observer_ui_failure("dispatch_ptr", "pointer_range", 0) end
        local dispatch, dispatch_reason = observer_read_pointer(dispatch_global)
        if not dispatch then return observer_ui_failure("dispatch_ptr", dispatch_reason, 8) end
        -- 文档中的5740与5744是十进制偏移，按原始数值读取。
        local count_address = observer_add(dispatch, 5740)
        if not count_address then return observer_ui_failure("count_read", "pointer_range", 0) end
        local count_bytes, count_reason = observer_read(count_address, 4)
        if not count_bytes then return observer_ui_failure("count_read", count_reason, 4) end
        local count = observer_u32(count_bytes, 0)
        if count == nil then return observer_ui_failure("dispatch_count", "malformed_bytes", 4) end
        if count > 64 then
            return observer_ui_failure("dispatch_count", "value_out_of_range", 4, count)
        end

        local rows_bytes = ""
        local rows_address
        if count > 0 then
            rows_address = observer_add(dispatch, 5744)
            if not rows_address then
                return observer_ui_failure("rows_read", "pointer_range", 0)
            end
            rows_bytes, count_reason = observer_read(rows_address, count * 16)
            if not rows_bytes then
                return observer_ui_failure("rows_read", count_reason, count * 16)
            end
        end

        local controllers = {}
        for index = 0, count - 1 do
            local row_offset = index * 16
            local controller_address = observer_u64(rows_bytes, row_offset)
            local kind = observer_u32(rows_bytes, row_offset + 8)
            if kind == nil then
                return observer_ui_failure("row_decode", "malformed_bytes", count * 16)
            end
            -- 控制器表的空指针或非法地址跳过；vptr只作可选只读采样。
            if controller_address and controller_address >= 0x10000 then
                local item = {
                    kind = kind,
                    object_id = observer_anon_id(controller_address),
                }
                local vtable = observer_read_pointer(controller_address)
                if vtable and vtable >= module_base_number
                    and vtable < module_base_number + OBSERVER_IMAGE_SIZE then
                    item.vtable_rva = vtable - module_base_number
                    local table_bytes = observer_read(vtable, 64)
                    if table_bytes then
                        local functions = {}
                        for function_index = 0, 7 do
                            local address = observer_u64(table_bytes, function_index * 8)
                            if address and address >= module_base_number + core.SECTION.rva
                                and address < module_base_number + expected_section_end then
                                functions[#functions + 1] = address - module_base_number
                            end
                        end
                        if #functions > 0 then item.vtable_functions = functions end
                    end
                end
                controllers[#controllers + 1] = item
            end
        end

        local owner_verify, verify_reason = observer_read_pointer(owner_global)
        if not owner_verify then return observer_ui_failure("verify_owner", verify_reason, 8) end
        if owner_verify ~= owner then
            return observer_ui_failure("verify_owner", "value_changed", 8)
        end
        local stack_verify
        stack_verify, verify_reason = observer_read(stack_address, 24)
        if not stack_verify then return observer_ui_failure("verify_stack", verify_reason, 24) end
        if stack_verify ~= stack_bytes then
            return observer_ui_failure("verify_stack", "value_changed", 24)
        end
        local dispatch_verify
        dispatch_verify, verify_reason = observer_read_pointer(dispatch_global)
        if not dispatch_verify then return observer_ui_failure("verify_dispatch", verify_reason, 8) end
        if dispatch_verify ~= dispatch then
            return observer_ui_failure("verify_dispatch", "value_changed", 8)
        end
        local count_verify
        count_verify, verify_reason = observer_read(count_address, 4)
        if not count_verify then return observer_ui_failure("verify_count", verify_reason, 4) end
        local verified_count = observer_u32(count_verify, 0)
        if verified_count == nil then
            return observer_ui_failure("verify_count", "malformed_bytes", 4)
        end
        if verified_count ~= count then
            return observer_ui_failure("verify_count", "value_changed", 4)
        end
        local rows_verify = ""
        if count > 0 then
            rows_verify, verify_reason = observer_read(rows_address, count * 16)
            if not rows_verify then
                return observer_ui_failure("verify_rows", verify_reason, count * 16)
            end
        end
        if rows_verify ~= rows_bytes then
            return observer_ui_failure("verify_rows", "value_changed", count * 16)
        end
        return {screen_depth = depth, screen_ids = screen_ids, controllers = controllers}, nil
    end

    local function make_observer_adapter()
        prepare_observer_paths()
        local adapter = {}
        local function protect(callback)
            return function(...)
                if observer_faulted then return nil end
                local ok, first, second, third = pcall(callback, ...)
                if not ok then
                    observer_faulted = true
                    error("observer adapter failure")
                end
                return first, second, third
            end
        end
        adapter.now_ms = protect(function()
            observer_now = tonumber(kernel.GetTickCount64())
            if not observer_now or observer_now < 0 or observer_now ~= math.floor(observer_now) then
                error("observer clock failure")
            end
            return observer_now
        end)
        adapter.begin_cycle = protect(observer_begin_cycle)
        adapter.read_slot = protect(observer_read_slot)
        adapter.read_widget_slot = protect(observer_widget_read_widget_slot)
        adapter.replace_ascii_widget = protect(observer_display_replace_ascii_widget)
        adapter.finish_cycle = protect(observer_finish_cycle)
        adapter.ui_snapshot = protect(observer_ui_snapshot)
        adapter.take_command = protect(observer_take_command)
        adapter.output = protect(observer_write_report)
        return adapter
    end

    local function make_translate_adapter(target_verified)
        observer_translate_prepare_paths()
        translate_layout.instance = translate_layout.new(ffi, {
            read = observer_read,
            query = observer_query_address,
            same_region = same_observer_region,
            region_allowed = observer_region_allowed,
            verify_context = observer_widget_verify_context,
            manager_for_root = function(root) return observer_add(root, 0x14498) end,
            gap = translate_layout.gap,
            verified = translate_layout.verified == true,
            now_ms = function() return tonumber(kernel.GetTickCount64()) end,
            measure = function(row)
                local target = ffi.cast(
                    "void (*)(void *)",
                    ffi.cast("size_t", module_base) + 0x18610C0
                )
                target(ffi.cast("void *", row))
            end,
            position = function(row, packed, flag)
                if flag ~= 0 then error("unsupported position flag") end
                local target = ffi.cast(
                    "void (*)(void *, HD2Probe_U64, HD2Probe_U8)",
                    ffi.cast("size_t", module_base) + 0x1860DA0
                )
                target(ffi.cast("void *", row), packed, 0)
            end,
        })
        if STANDALONE_ENABLED then
            native_transport_api = nil
            if target_verified == true and native_http_loader then
                local load_ok, api = pcall(native_http_loader.load)
                if load_ok and type(api) == "table" then
                    native_transport_api = api
                end
            end
        end
        local adapter = {}
        local function timing_start()
            local ok, value = pcall(kernel.GetTickCount64)
            if not ok then return nil end
            value = tonumber(value)
            if not observer_translate_safe_count(value, 9007199254740991) then return nil end
            return value
        end
        local function timing_finish(name, started)
            if started == nil then return end
            local finished = timing_start()
            if finished == nil or finished < started then return end
            local elapsed = finished - started
            local layout = translate_layout.instance
            if type(layout) == "table" and type(layout.record_timing) == "function" then
                pcall(layout.record_timing, name, elapsed)
            end
        end
        local function protect(callback, phase_name)
            return function(...)
                if observer_faulted then return nil end
                local started = phase_name and timing_start() or nil
                local ok, first, second, third = pcall(callback, ...)
                if phase_name then timing_finish(phase_name, started) end
                if not ok then
                    observer_faulted = true
                    error("translation adapter failure")
                end
                return first, second, third
            end
        end
        adapter.now_ms = protect(function()
            observer_now = tonumber(kernel.GetTickCount64())
            if not observer_now or observer_now < 0 or observer_now ~= math.floor(observer_now) then
                error("translation clock failure")
            end
            return observer_now
        end)
        -- 心跳依据当前传输方式读取对应的文件规则。
        adapter.heartbeat = protect(function() return observer_translate_heartbeat(true) end)
        adapter.read_slot = protect(observer_translate_read_slot, "read_slot")
        adapter.scan_plan = function(...)
            if observer_faulted then return nil end
            local started = timing_start()
            local ok, plan = pcall(observer_translate_scan_plan, ...)
            timing_finish("scan_plan", started)
            if not ok then return nil end
            return plan
        end
        adapter.submit = protect(observer_translate_publish_request, "submit")
        adapter.response = protect(observer_translate_read_response, "response")
        adapter.apply = protect(observer_translate_apply, "apply_total")
        adapter.cancel = protect(observer_translate_cancel)
        adapter.output = protect(observer_translate_write_report, "report")
        return adapter
    end

    local adapter = {
        hash_file = hash_module_file,
        query = query,
        read = read_memory,
        hash_bytes = hash_bytes,
        output = output_manifest,
    }
    local state = core.new(adapter, {outgoing_probe = OUTGOING_PROBE_ENABLED})
    local code_manifest_written = false
    local code_scan_done = false
    local observer_state = nil
    local translate_state = nil
    local translate_last_dispatch_ms = nil

    local function observer_log_stopped()
        pcall(print, "[HD2 Chat Probe] observer stopped after a sanitized failure")
    end

    local function observer_finish_with_error(reason)
        if observer_state then
            observer_state.done = true
            observer_state.status = "observer_stopped"
            observer_state.stop_reason = reason
            observer_state.active_cycle = nil
            local output_ok = pcall(observer_write_report, observer_core.manifest(observer_state))
            if not output_ok then observer_log_stopped() end
        else
            observer_log_stopped()
        end
        return true
    end

    local function signatures_verified(manifest)
        if type(manifest) ~= "table" or manifest.status ~= "scan_complete"
            or type(manifest.known_signatures) ~= "table" or #manifest.known_signatures ~= 6 then return false end
        for index = 1, 6 do
            local item = manifest.known_signatures[index]
            if type(item) ~= "table" or item.comparison ~= "true" then return false end
        end
        return true
    end

    local function display_target_verified(manifest)
        if not signatures_verified(manifest) or type(manifest.candidates) ~= "table" then return false end
        for _, candidate in ipairs(manifest.candidates) do
            if type(candidate) == "table" and candidate.rva == DISPLAY_TARGET_RVA
                and type(candidate.window_rva) == "number"
                and candidate.window_rva == math.floor(candidate.window_rva)
                and type(candidate.byte_length) == "number"
                and candidate.byte_length == math.floor(candidate.byte_length)
                and type(candidate.bytes_hex) == "string"
                and candidate.window_rva <= DISPLAY_TARGET_RVA then
                local offset = DISPLAY_TARGET_RVA - candidate.window_rva
                if offset >= 0 and offset + 21 <= candidate.byte_length
                    and #candidate.bytes_hex == candidate.byte_length * 2 then
                    local prefix = candidate.bytes_hex:sub(offset * 2 + 1, offset * 2 + 42):lower()
                    if prefix == DISPLAY_TARGET_PREFIX then return true end
                end
            end
        end
        return false
    end

    local function start_observer(target_verified)
        local adapter_ok, observer_adapter = pcall(make_observer_adapter)
        if not adapter_ok then
            observer_log_stopped()
            return false
        end
        local state_ok, created_state = pcall(observer_core.new, observer_adapter, {
            display_test = DISPLAY_TEST_ENABLED,
            target_verified = target_verified == true,
        })
        if not state_ok or type(created_state) ~= "table" then
            observer_log_stopped()
            return false
        end
        observer_state = created_state
        return true
    end

    local function start_translation(target_verified)
        local adapter_ok, translate_adapter = pcall(make_translate_adapter, target_verified == true)
        if not adapter_ok then
            observer_log_stopped()
            return false
        end
        local state_ok, created_state = pcall(translate_core.new, translate_adapter, {
            target_verified = target_verified == true,
            session_id = translate_session_id,
        })
        if not state_ok or type(created_state) ~= "table" then
            observer_log_stopped()
            return false
        end
        translate_state = created_state
        translate_last_dispatch_ms = nil
        return true
    end

    local function observer_translate_cleanup_native()
        if not STANDALONE_ENABLED or not native_transport_api then return end
        pcall(native_transport_api.cancel, nil)
    end

    local function observer_finish_translation_with_error(reason)
        observer_translate_cleanup_native()
        if translate_state and translate_core and type(translate_core.manifest) == "function" then
            local manifest_ok, manifest = pcall(translate_core.manifest, translate_state)
            if manifest_ok and type(manifest) == "table" then
                manifest.status = "stopped"
                manifest.done = true
                if reason == "clock_error" or reason == "invalid_clock" or reason == "clock_reversed" then
                    manifest.code = reason
                else
                    manifest.code = nil
                end
                local output_ok = pcall(observer_translate_write_report, manifest)
                if not output_ok then observer_log_stopped() end
            else
                observer_log_stopped()
            end
        else
            observer_log_stopped()
        end
        return true
    end

    local function one_probe_step()
        if not code_scan_done then
            local step_ok, done, manifest = pcall(core.step, state, core.BUDGET_PER_STEP)
            if not step_ok then
                state.done = true
                state.status = "probe_error"
                state.detail = "probe stopped after an internal error"
                manifest = core.manifest(state)
                done = true
            end
            if not done then return false end

            code_scan_done = true
            if not code_manifest_written then
                code_manifest_written = true
                local output_ok = pcall(adapter.output, core.encode_json(manifest or core.manifest(state)))
                if not output_ok then pcall(print, "[HD2 Chat Probe] failed to write research manifest") end
            end

            if TRANSLATE_ENABLED then
                translate_layout.verified, translate_layout.gap = translate_layout.verify(ffi, read_memory)
                local target_verified = display_target_verified(manifest)
                    and translate_layout.verified == true
                observer_display_native_gate = target_verified
                if not translate_core or type(translate_core.new) ~= "function"
                    or type(translate_core.step) ~= "function" or type(translate_core.manifest) ~= "function" then
                    observer_log_stopped()
                    return true
                end
                if not start_translation(target_verified) then return true end
            elseif OBSERVE_ENABLED and signatures_verified(manifest) then
                local target_verified = not DISPLAY_TEST_ENABLED or display_target_verified(manifest)
                observer_display_native_gate = DISPLAY_TEST_ENABLED and target_verified
                if not observer_core or type(observer_core.new) ~= "function"
                    or type(observer_core.step) ~= "function" or type(observer_core.manifest) ~= "function" then
                    observer_log_stopped()
                    return true
                end
                if not start_observer(target_verified) then return true end
            else
                return true
            end
        end

        if translate_state then
            if type(translate_core.should_step) == "function" then
                local clock_ok, clock_value = pcall(kernel.GetTickCount64)
                if not clock_ok then return observer_finish_translation_with_error("clock_error") end
                local now_ms = tonumber(clock_value)
                local check_ok, should_run, clock_error = pcall(
                    translate_core.should_step,
                    translate_state,
                    now_ms,
                    translate_last_dispatch_ms
                )
                if not check_ok then return observer_finish_translation_with_error("internal_error") end
                if clock_error ~= nil then return observer_finish_translation_with_error(clock_error) end
                translate_last_dispatch_ms = now_ms
                if should_run ~= true then return false end
            end
            observer_read_budget = 0
            local step_ok, done = pcall(translate_core.step, translate_state)
            if not step_ok then return observer_finish_translation_with_error("internal_error") end
            if observer_faulted then return observer_finish_translation_with_error("adapter_error") end
            if done == true then observer_translate_cleanup_native() end
            return done == true
        end
        if not observer_state then return true end
        observer_read_budget = 0
        local step_ok, done = pcall(observer_core.step, observer_state)
        if not step_ok then return observer_finish_with_error("internal_error") end
        if observer_faulted then return observer_finish_with_error("adapter_error") end
        return done == true
    end
    return one_probe_step
end

local original_update = _G.update
local setup_ok, probe_step = pcall(initialize_probe)
if setup_ok then
    if TRANSLATE_ENABLED then
        if type(translate_core) == "table" and type(translate_core.wrap_update_after) == "function" then
            _G.update = translate_core.wrap_update_after(original_update, probe_step)
        else
            local finished = false
            local function after_original(...)
                if not finished then
                    local ok, done = pcall(probe_step)
                    if not ok or done == true then finished = true end
                end
                return ...
            end
            _G.update = function(...)
                if type(original_update) == "function" then
                    return after_original(original_update(...))
                end
                return after_original()
            end
        end
    elseif DISPLAY_TEST_ENABLED then
        local function pack_results(...)
            return {n = select("#", ...), ...}
        end
        local unpack_results = unpack or table.unpack
        local finished = false
        _G.update = function(...)
            local results
            if type(original_update) == "function" then
                results = pack_results(original_update(...))
            end
            if not finished then
                local ok, done = pcall(probe_step)
                if not ok or done == true then finished = true end
            end
            if results then return unpack_results(results, 1, results.n) end
        end
    else
        _G.update = core.wrap_update(original_update, probe_step)
    end
else
    pcall(print, "[HD2 Chat Probe] initialization failed; research scan did not start")
end

if target_language_settings_step and target_language_settings_clock
    and type(target_language_settings) == "table"
    and type(target_language_settings.wrap_update) == "function" then
    local wrapped_ok, wrapped_update = pcall(
        target_language_settings.wrap_update,
        _G.update,
        target_language_settings_step,
        target_language_settings_clock
    )
    if wrapped_ok and type(wrapped_update) == "function" then _G.update = wrapped_update end
end
