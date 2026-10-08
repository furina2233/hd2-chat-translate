-- 纯 Lua 5.1 扫描核心；不依赖 FFI、游戏函数或外部模块。
local M = {}

M.SOURCE = {
    module = "game.dll",
    sha256 = "2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e",
    disk_size = 15522408,
    timestamp = 1790161983,
    size_of_image = 74727424,
}

M.SECTION = {
    rva = 4096,
    size = 34667155,
    flags = 0x60000020,
}

M.BUDGET_PER_STEP = 16 * 1024
M.OUTGOING_BUDGET_PER_STEP = 4 * 1024
M.OUTGOING_READ_CHUNK = 1024
M.OUTGOING_HEADER_READ_BYTES = 4 * 1024
M.MAX_ITERATIONS_PER_STEP = 64
M.MAX_OUTGOING_ITERATIONS_PER_STEP = 4
M.MAX_HITS_PER_PATTERN = 32
M.MAX_CANDIDATES = 128
M.MAX_CANDIDATE_BYTES = 128 * 1024
M.MAX_PE_BYTES = 64 * 1024

local MEM_COMMIT = 0x1000
local MEM_IMAGE = 0x1000000
local PAGE_READONLY = 0x02
local PAGE_READWRITE = 0x04
local PAGE_WRITECOPY = 0x08
local PAGE_EXECUTE_READ = 0x20
local PAGE_EXECUTE_READWRITE = 0x40
local PAGE_EXECUTE_WRITECOPY = 0x80
local IMAGE_SCN_MEM_EXECUTE = 0x20000000

local function valid_integer(value)
    return type(value) == "number" and value == value and value ~= math.huge
        and value ~= -math.huge and value >= 0 and value == math.floor(value)
end

local function new_array()
    return setmetatable({}, {__json_array = true})
end

local function u16(data, offset)
    local a, b = data:byte(offset + 1, offset + 2)
    if not b then return nil end
    return a + b * 256
end

local function u32(data, offset)
    local a, b, c, d = data:byte(offset + 1, offset + 4)
    if not d then return nil end
    return a + b * 256 + c * 65536 + d * 16777216
end

local function from_hex(hex)
    if type(hex) ~= "string" or #hex % 2 ~= 0 or hex:find("[^0-9a-fA-F]") then
        return nil
    end
    local bytes = {}
    for i = 1, #hex, 2 do
        bytes[#bytes + 1] = string.char(tonumber(hex:sub(i, i + 1), 16))
    end
    return table.concat(bytes)
end

local function to_hex(data)
    local out = {}
    for i = 1, #data do
        out[i] = string.format("%02x", data:byte(i))
    end
    return table.concat(out)
end

local function has_flag(value, flag)
    return math.floor(value / flag) % 2 == 1
end

local function readable_protection(protect)
    return protect == PAGE_READONLY or protect == PAGE_READWRITE or protect == PAGE_WRITECOPY
        or protect == PAGE_EXECUTE_READ or protect == PAGE_EXECUTE_READWRITE
        or protect == PAGE_EXECUTE_WRITECOPY
end

local function executable_protection(protect)
    return protect == PAGE_EXECUTE_READ or protect == PAGE_EXECUTE_READWRITE
        or protect == PAGE_EXECUTE_WRITECOPY
end

local function check_range(adapter, rva, length, executable)
    if not valid_integer(rva) or not valid_integer(length) or length == 0
        or rva > M.SOURCE.size_of_image or length > M.SOURCE.size_of_image - rva then
        return nil, "invalid range"
    end
    if executable then
        local section = M.SECTION
        if rva < section.rva or rva >= section.rva + section.size
            or length > section.rva + section.size - rva then
            return nil, "outside approved code section"
        end
    end
    local finish = rva + length
    local cursor = rva
    local protections = new_array()
    local seen = {}
    local segments = {}
    while cursor < finish do
        local query_ok, region = pcall(adapter.query, cursor)
        if not query_ok then return nil, "query failed" end
        if type(region) ~= "table" or not valid_integer(region.start_rva)
            or not valid_integer(region.size) or region.size == 0
            or not valid_integer(region.protect or 0)
            or region.start_rva > M.SOURCE.size_of_image
            or region.size > M.SOURCE.size_of_image - region.start_rva then
            return nil, "unreadable"
        end
        local region_end = region.start_rva + region.size
        if region.start_rva > cursor or region_end <= cursor
            or region.allocation_base ~= true or region.type ~= MEM_IMAGE
            or region.state ~= MEM_COMMIT or not readable_protection(region.protect or 0) then
            return nil, "unreadable"
        end
        if executable and not executable_protection(region.protect or 0) then
            return nil, "not executable"
        end
        local key = tostring(region.protect)
        if not seen[key] then
            seen[key] = true
            protections[#protections + 1] = region.protect
        end
        local segment_end = math.min(finish, region_end)
        segments[#segments + 1] = {
            start_rva = cursor,
            end_rva = segment_end,
            protect = region.protect,
            region_start = region.start_rva,
            region_size = region.size,
            allocation_base = region.allocation_base,
            type = region.type,
            state = region.state,
        }
        cursor = segment_end
    end
    -- 在读取前复核每个区段，拒绝检查期间发生的权限或归属变化。
    for _, segment in ipairs(segments) do
        local query_ok, current = pcall(adapter.query, segment.start_rva)
        if not query_ok then return nil, "query failed before read" end
        if type(current) ~= "table" or current.start_rva ~= segment.region_start
            or current.size ~= segment.region_size or current.allocation_base ~= segment.allocation_base
            or current.type ~= segment.type or current.state ~= segment.state
            or current.protect ~= segment.protect then
            return nil, "region changed before read"
        end
    end
    return protections, nil, segments
end

local function checked_read(adapter, rva, length, executable)
    local protections, reason = check_range(adapter, rva, length, executable)
    if not protections then return nil, reason, nil, false end
    local read_ok, data = pcall(adapter.read, rva, length, executable)
    if not read_ok then return nil, "read failed", protections, true end
    if type(data) ~= "string" or #data ~= length then
        return nil, "short read", protections, true
    end
    return data, nil, protections, true
end

local function parse_pe(data)
    if type(data) ~= "string" or #data > M.MAX_PE_BYTES or #data < 64 then
        return nil, "invalid PE header size"
    end
    if data:sub(1, 2) ~= "MZ" then return nil, "missing DOS header" end
    local pe = u32(data, 0x3c)
    if not pe or pe < 64 or pe + 24 > #data then return nil, "invalid PE offset" end
    if data:sub(pe + 1, pe + 4) ~= "PE\0\0" then return nil, "missing PE signature" end
    local file_header = pe + 4
    local machine = u16(data, file_header)
    local section_count = u16(data, file_header + 2)
    local timestamp = u32(data, file_header + 4)
    local optional_size = u16(data, file_header + 16)
    if machine ~= 0x8664 or not section_count or section_count < 1 or section_count > 96
        or not optional_size or optional_size < 112 then
        return nil, "unsupported PE file header"
    end
    local optional = file_header + 20
    if optional + optional_size > #data or u16(data, optional) ~= 0x20b then
        return nil, "invalid PE32+ optional header"
    end
    local size_of_image = u32(data, optional + 56)
    local size_of_headers = u32(data, optional + 60)
    local section_table = optional + optional_size
    local table_end = section_table + section_count * 40
    if not size_of_image or not size_of_headers or size_of_headers > M.MAX_PE_BYTES
        or table_end > size_of_headers or table_end > #data then
        return nil, "invalid PE section table bounds"
    end

    local first_blank = nil
    for index = 0, section_count - 1 do
        local offset = section_table + index * 40
        local name = data:sub(offset + 1, offset + 8)
        local virtual_size = u32(data, offset + 8)
        local rva = u32(data, offset + 12)
        local characteristics = u32(data, offset + 36)
        if not virtual_size or not rva or not characteristics or rva > size_of_image
            or virtual_size > size_of_image - rva then
            return nil, "invalid PE section entry"
        end
        if not first_blank and name:gsub("[%z ]", "") == "" then
            first_blank = {
                rva = rva,
                size = virtual_size,
                flags = characteristics,
            }
        end
    end
    if not first_blank then return nil, "main code section not found" end
    return {
        timestamp = timestamp,
        size_of_image = size_of_image,
        size_of_headers = size_of_headers,
        section = first_blank,
    }
end

local PATTERNS = {
    {
        id = "history_signature",
        reason = "exact history field signature",
        bytes = from_hex("8b87949500008b8f90950000"),
    },
    {
        id = "imm_le32_9590",
        reason = "candidate LE32 immediate 0x9590; not decoded as a function",
        bytes = from_hex("90950000"),
    },
    {
        id = "imm_le32_9594",
        reason = "candidate LE32 immediate 0x9594; not decoded as a function",
        bytes = from_hex("94950000"),
    },
    {
        id = "imm_le32_c418",
        reason = "candidate LE32 immediate 0xc418; not decoded as a function",
        bytes = from_hex("18c40000"),
    },
}

local KNOWN = {
    {label = "send_1097560", rva = 0x1097560, hex = "415641574881ec78040000488b059e4a5a014833c44889842450040000803900"},
    {label = "chat_box_186025d", rva = 0x186025d, hex = "488b0d8cccc1014c8d87d41600004881c118c40000e8e97283ff"},
    {label = "history_1097a7c", rva = 0x1097a7c, hex = "8b87949500008b8f90950000"},
    {label = "chat_rpc_beb103", rva = 0xbeb103, hex = "41b901000000488bd7b98eb8dd9fe81a33ffff"},
    {label = "set_label_143bf90", rva = 0x143bf90, hex = "4883ec284c8bd93991100100000f8480"},
    {label = "set_string_arg_143c950", rva = 0x143c950, hex = "40534883ec20488bd94881c110010000"},
}

local OUTGOING_WINDOWS = {
    {rva = 0x1097500, size = 0x3000},
    {rva = 0x185f000, size = 0x2000},
    {rva = 0xbeaf00, size = 0x1800},
    {rva = 0xbde300, size = 0x1000},
}

local function new_outgoing_windows()
    local windows = new_array()
    for _, window in ipairs(OUTGOING_WINDOWS) do
        windows[#windows + 1] = {
            rva = window.rva,
            size = window.size,
            offset = 0,
            status = "pending",
            hex = "",
        }
    end
    return windows
end

-- 已采集指令的直接 call 目标是 0x12f2f60、0x20bba88、0x143a1b0。
-- 前四个候选的窗口从 0x10976e0、0x10978e0、0x1097c20、0x1097e20 开始，接续发送/历史区域。
-- 后三组中的第二项分别是直接 call 目标后移 0x200 的窗口续段。
local FOLLOWUP = {
    {label = "followup_1097760", rva = 0x1097760},
    {label = "followup_1097960", rva = 0x1097960},
    {label = "followup_1097ca0", rva = 0x1097ca0},
    {label = "followup_1097ea0", rva = 0x1097ea0},
    {label = "followup_12f2f60", rva = 0x12f2f60},
    {label = "followup_12f3160", rva = 0x12f3160},
    {label = "followup_20bba88", rva = 0x20bba88},
    {label = "followup_20bbc88", rva = 0x20bbc88},
    {label = "followup_143a1b0", rva = 0x143a1b0},
    {label = "followup_143a3b0", rva = 0x143a3b0},
    -- 0x12f2f60 的直接调用链；用于区分事件派发与实际聊天行显示。
    {label = "dispatch_1382650", rva = 0x1382650},
    {label = "dispatch_1382850", rva = 0x1382850},
    {label = "dispatch_185d6e0", rva = 0x185d6e0},
    {label = "dispatch_185d8e0", rva = 0x185d8e0},
    {label = "dispatch_185f470", rva = 0x185f470},
    {label = "dispatch_185f670", rva = 0x185f670},
    -- 0x185f470将文本记录交给下游；继续核对显示记录的所有权与更新方式。
    {label = "display_1860b00", rva = 0x1860b00},
    {label = "display_1860d00", rva = 0x1860d00},
    {label = "display_185f170", rva = 0x185f170},
    {label = "display_185f370", rva = 0x185f370},
    -- 控件正文属性接口、格式化辅助和后续更新；核对复制/借用语义后再接译文。
    {label = "property_1441ca0", rva = 0x1441ca0},
    {label = "property_1441ea0", rva = 0x1441ea0},
    {label = "format_13006a0", rva = 0x13006a0},
    {label = "format_13008a0", rva = 0x13008a0},
    {label = "text_173c360", rva = 0x173c360},
    {label = "text_173c560", rva = 0x173c560},
    {label = "refresh_1861010", rva = 0x1861010},
    {label = "refresh_1861210", rva = 0x1861210},
    -- 0x13006a0的直接格式化调用目标，用于核对属性表到显示文本的转换。
    {label = "format_map_1300b90", rva = 0x1300b90},
    {label = "format_map_1300d90", rva = 0x1300d90},
}

local function append_reason(candidate, reason)
    if not candidate.reason_set[reason] then
        candidate.reason_set[reason] = true
        candidate.reasons[#candidate.reasons + 1] = reason
    end
end

local function add_candidate(state, rva, reason, pattern_length)
    if rva < state.section.rva or rva >= state.section.rva + state.section.size then return end
    local existing = state.candidate_map[rva]
    if existing then
        append_reason(existing, reason)
        return
    end
    local window_start = math.max(state.section.rva, rva - 128)
    local window_end = math.min(state.section.rva + state.section.size, rva + 384)
    local window_length = window_end - window_start
    if state.candidate_total >= M.MAX_CANDIDATES
        or state.candidate_reserved_bytes + window_length > M.MAX_CANDIDATE_BYTES then
        state.truncated.total = state.truncated.total + 1
        return
    end
    local candidate = {
        rva = rva,
        window_rva = window_start,
        window_length = window_length,
        reasons = new_array(),
        reason_set = {},
    }
    append_reason(candidate, reason)
    state.candidate_map[rva] = candidate
    state.candidate_total = state.candidate_total + 1
    state.candidate_reserved_bytes = state.candidate_reserved_bytes + window_length
    state.capture_queue[#state.capture_queue + 1] = candidate
end

local function fail(state, status, detail)
    state.done = true
    state.status = status
    state.detail = state.outgoing_probe and status or detail
    state.result = M.manifest(state)
    return true, state.result
end

local function prepare_header(state, header)
    local pe, pe_error = parse_pe(header)
    if not pe then return nil, "invalid_pe", pe_error end
    state.pe = pe
    if pe.timestamp ~= M.SOURCE.timestamp or pe.size_of_image ~= M.SOURCE.size_of_image then
        return nil, "pe_mismatch", "PE timestamp or SizeOfImage does not match the approved build"
    end
    if pe.section.rva ~= M.SECTION.rva or pe.section.size ~= M.SECTION.size
        or pe.section.flags ~= M.SECTION.flags or not has_flag(pe.section.flags, IMAGE_SCN_MEM_EXECUTE) then
        return nil, "section_mismatch", "first blank-name section does not match the approved code section"
    end
    state.section = pe.section

    if state.outgoing_probe then
        for _, window in ipairs(state.outgoing_windows) do
            if window.rva < state.section.rva or window.rva >= state.section.rva + state.section.size
                or window.size > state.section.rva + state.section.size - window.rva then
                return nil, "probe_window_invalid", "approved code window is outside the executable section"
            end
        end
        state.outgoing_window_phase_started = true
        state.outgoing_window_index = 1
        state.phase = "outgoing_windows"
        return true
    end

    for _, known in ipairs(KNOWN) do
        local expected = from_hex(known.hex)
        add_candidate(state, known.rva, "known RVA lead " .. known.label .. "; not a verified function", #expected)
    end
    for _, lead in ipairs(FOLLOWUP) do
        add_candidate(
            state,
            lead.rva,
            "follow-up code window " .. lead.label .. "; not a verified function",
            1
        )
    end
    state.known_index = 1
    state.phase = "known"
    return true
end

local function prepare(state)
    local digest, disk_size, hash_error = state.adapter.hash_file()
    if type(digest) ~= "string" then
        state.disk_size = disk_size
        if disk_size ~= nil and disk_size ~= M.SOURCE.disk_size then
            return nil, "disk_size_mismatch", "game.dll size does not match the approved build"
        end
        return nil, "disk_hash_unreadable", hash_error or "module file could not be hashed"
    end
    state.disk_sha256 = digest:lower()
    state.disk_size = disk_size
    if disk_size ~= M.SOURCE.disk_size then
        return nil, "disk_size_mismatch", "game.dll size does not match the approved build"
    end
    if state.disk_sha256 ~= M.SOURCE.sha256 then
        return nil, "hash_mismatch", "game.dll SHA-256 does not match the approved build"
    end

    if state.outgoing_probe then
        state.outgoing_header = ""
        state.outgoing_header_offset = 0
        state.phase = "outgoing_header"
        return true
    end
    local header, header_error = checked_read(state.adapter, 0, 4096, false)
    if not header then
        return nil, "headers_unreadable", header_error
    end
    return prepare_header(state, header)
end

local function compare_known_signatures(state)
    local known = KNOWN[state.known_index]
    if not known then
        state.scan_cursor = state.section.rva
        state.scan_end = state.section.rva + state.section.size
        state.phase = "scanning"
        return true
    end
    local expected = from_hex(known.hex)
    if #expected > state.frame_read_budget then return false end
    local observed, _, _, attempted = checked_read(state.adapter, known.rva, #expected, true)
    if attempted then state.frame_read_budget = state.frame_read_budget - #expected end
    local comparison = "unreadable"
    local observed_hex = ""
    if observed then
        observed_hex = to_hex(observed)
        comparison = observed == expected and "true" or "false"
    else
        state.read_failures = state.read_failures + 1
    end
    state.known_signatures[#state.known_signatures + 1] = {
        label = known.label,
        rva = known.rva,
        comparison = comparison,
        expected_hex = known.hex,
        observed_hex = observed_hex,
    }
    state.known_index = state.known_index + 1
    return true
end

local function scan_block(state, data, start_rva)
    local combined = state.carry .. data
    local combined_rva = start_rva - #state.carry
    for _, pattern in ipairs(PATTERNS) do
        local position = 1
        while true do
            local hit = combined:find(pattern.bytes, position, true)
            if not hit then break end
            local hit_rva = combined_rva + hit - 1
            if hit_rva > state.pattern_last_seen[pattern.id] then
                state.pattern_last_seen[pattern.id] = hit_rva
                state.match_counts[pattern.id] = state.match_counts[pattern.id] + 1
                if state.match_counts[pattern.id] <= M.MAX_HITS_PER_PATTERN then
                    add_candidate(state, hit_rva, pattern.reason, #pattern.bytes)
                else
                    state.truncated.patterns[pattern.id] = state.truncated.patterns[pattern.id] + 1
                end
            end
            position = hit + 1
        end
    end
    local overlap = state.max_pattern_length - 1
    if #combined > overlap then
        state.carry = combined:sub(#combined - overlap + 1)
    else
        state.carry = combined
    end
end

local function valid_scan_region(region, cursor)
    if type(region) ~= "table" or not valid_integer(region.start_rva)
        or not valid_integer(region.size) or region.size == 0
        or not valid_integer(region.protect or 0)
        or region.start_rva > M.SOURCE.size_of_image
        or region.size > M.SOURCE.size_of_image - region.start_rva then return false end
    local finish = region.start_rva + region.size
    if region.start_rva > cursor or finish <= cursor then return false, nil end
    return region.allocation_base == true and region.type == MEM_IMAGE
        and region.state == MEM_COMMIT and readable_protection(region.protect)
        and executable_protection(region.protect), math.min(finish, M.SECTION.rva + M.SECTION.size)
end

local function next_page(cursor)
    return math.floor(cursor / 4096 + 1) * 4096
end

local function bounded_region_end(region, cursor)
    if type(region) == "table" and valid_integer(region.start_rva)
        and valid_integer(region.size) and region.size > 0
        and region.start_rva <= cursor and region.start_rva <= M.SOURCE.size_of_image
        and region.size <= M.SOURCE.size_of_image - region.start_rva then
        local finish = region.start_rva + region.size
        if finish > cursor then return math.min(finish, M.SECTION.rva + M.SECTION.size) end
    end
    return math.min(M.SECTION.rva + M.SECTION.size, next_page(cursor))
end

local function same_region(a, b)
    return type(a) == "table" and type(b) == "table"
        and a.start_rva == b.start_rva and a.size == b.size
        and a.allocation_base == b.allocation_base and a.type == b.type
        and a.state == b.state and a.protect == b.protect
end

local function capture_one(state, budget)
    local candidate = state.capture_queue[state.capture_index]
    if not candidate then return 0, false end
    if candidate.window_length > budget then return 0, false end
    state.capture_index = state.capture_index + 1
    local data, reason, protections, attempted = checked_read(
        state.adapter,
        candidate.window_rva,
        candidate.window_length,
        true
    )
    if attempted then state.frame_read_budget = state.frame_read_budget - candidate.window_length end
    if not data then
        state.candidate_reserved_bytes = state.candidate_reserved_bytes - candidate.window_length
        state.candidate_map[candidate.rva] = nil
        state.unreadable_candidates = state.unreadable_candidates + 1
        return candidate.window_length, true
    end
    local center_protections = check_range(state.adapter, candidate.rva, 1, true)
    if not center_protections then
        state.candidate_reserved_bytes = state.candidate_reserved_bytes - candidate.window_length
        state.candidate_map[candidate.rva] = nil
        state.unreadable_candidates = state.unreadable_candidates + 1
        return candidate.window_length, true
    end
    state.candidates[#state.candidates + 1] = {
        rva = candidate.rva,
        window_rva = candidate.window_rva,
        page_protection = center_protections[1],
        page_protections = protections,
        sha256 = state.adapter.hash_bytes(data),
        bytes_hex = to_hex(data),
        byte_length = #data,
        reason = table.concat(candidate.reasons, "; "),
    }
    state.candidate_bytes = state.candidate_bytes + #data
    return candidate.window_length, true
end

function M.new(adapter, options)
    assert(type(adapter) == "table", "adapter required")
    assert(type(adapter.hash_file) == "function", "adapter.hash_file required")
    assert(type(adapter.query) == "function", "adapter.query required")
    assert(type(adapter.read) == "function", "adapter.read required")
    assert(type(adapter.hash_bytes) == "function", "adapter.hash_bytes required")
    options = options or {}
    assert(type(options) == "table", "options must be a table")
    local outgoing_probe = options.outgoing_probe == true
    return {
        adapter = adapter,
        outgoing_probe = outgoing_probe,
        outgoing_windows = outgoing_probe and new_outgoing_windows() or nil,
        outgoing_header = "",
        outgoing_header_offset = 0,
        outgoing_window_phase_started = false,
        outgoing_window_index = nil,
        phase = "prepare",
        done = false,
        candidates = new_array(),
        candidate_total = 0,
        candidate_map = {},
        capture_queue = new_array(),
        capture_index = 1,
        candidate_bytes = 0,
        candidate_reserved_bytes = 0,
        unreadable_candidates = 0,
        known_signatures = new_array(),
        carry = "",
        pattern_last_seen = {
            history_signature = -1,
            imm_le32_9590 = -1,
            imm_le32_9594 = -1,
            imm_le32_c418 = -1,
        },
        match_counts = {
            history_signature = 0,
            imm_le32_9590 = 0,
            imm_le32_9594 = 0,
            imm_le32_c418 = 0,
        },
        truncated = {
            patterns = {
                history_signature = 0,
                imm_le32_9590 = 0,
                imm_le32_9594 = 0,
                imm_le32_c418 = 0,
            },
            total = 0,
        },
        skipped_bytes = 0,
        skipped_regions = 0,
        read_failures = 0,
        scanned_bytes = 0,
        frame_read_budget = 0,
        known_index = 1,
        current_region = nil,
        max_pattern_length = 12,
        source_sha256 = M.SOURCE.sha256,
    }
end

function M.step(state, frame_budget)
    if state.done then return true, state.result end
    frame_budget = tonumber(frame_budget) or M.BUDGET_PER_STEP
    if frame_budget ~= frame_budget or frame_budget == math.huge or frame_budget == -math.huge then
        frame_budget = M.BUDGET_PER_STEP
    end
    if frame_budget < 0 then frame_budget = 0 end
    frame_budget = math.min(math.floor(frame_budget), M.BUDGET_PER_STEP)
    if state.outgoing_probe then
        frame_budget = math.min(frame_budget, M.OUTGOING_BUDGET_PER_STEP)
    end
    local initial_budget = frame_budget
    state.frame_read_budget = frame_budget
    if state.phase == "prepare" then
        local ok, status, detail = prepare(state)
        if not ok then return fail(state, status, detail) end
    end

    local while_guard = 0
    local iteration_limit = state.outgoing_probe
        and M.MAX_OUTGOING_ITERATIONS_PER_STEP or M.MAX_ITERATIONS_PER_STEP
    while state.frame_read_budget > 0 and while_guard < iteration_limit do
        while_guard = while_guard + 1
        if state.phase == "outgoing_header" then
            local amount = math.min(
                M.OUTGOING_HEADER_READ_BYTES - state.outgoing_header_offset,
                M.OUTGOING_READ_CHUNK,
                state.frame_read_budget
            )
            if amount <= 0 then break end
            local data = checked_read(
                state.adapter,
                state.outgoing_header_offset,
                amount,
                false
            )
            state.frame_read_budget = state.frame_read_budget - amount
            if not data then return fail(state, "headers_unreadable", "PE header read failed") end
            state.outgoing_header = state.outgoing_header .. data
            state.outgoing_header_offset = state.outgoing_header_offset + #data
            if state.outgoing_header_offset == M.OUTGOING_HEADER_READ_BYTES then
                local ok, status, detail = prepare_header(state, state.outgoing_header)
                if not ok then return fail(state, status, detail) end
            end
        elseif state.phase == "outgoing_windows" then
            local window = state.outgoing_windows[state.outgoing_window_index]
            if not window then
                state.done = true
                state.status = "outgoing_probe_complete"
                state.phase = "complete"
                state.result = M.manifest(state)
                return true, state.result
            end
            local amount = math.min(
                window.size - window.offset,
                M.OUTGOING_READ_CHUNK,
                state.frame_read_budget
            )
            if amount <= 0 then break end
            local data = checked_read(state.adapter, window.rva + window.offset, amount, true)
            state.frame_read_budget = state.frame_read_budget - amount
            if not data then
                window.status = window.offset > 0 and "partial" or "failed"
                state.done = true
                state.status = window.offset > 0 and "outgoing_probe_partial" or "outgoing_probe_failed"
                state.detail = "window_read_failed"
                state.phase = "outgoing_failed"
                state.result = M.manifest(state)
                return true, state.result
            end
            window.hex = window.hex .. to_hex(data)
            window.offset = window.offset + #data
            if window.offset == window.size then
                window.status = "complete"
                state.outgoing_window_index = state.outgoing_window_index + 1
                if state.outgoing_window_index > #state.outgoing_windows then
                    state.done = true
                    state.status = "outgoing_probe_complete"
                    state.phase = "complete"
                    state.result = M.manifest(state)
                    return true, state.result
                end
            end
        elseif state.phase == "known" then
            compare_known_signatures(state)
        elseif state.phase == "scanning" and state.capture_index <= #state.capture_queue then
            capture_one(state, state.frame_read_budget)
        elseif state.phase == "scanning" and state.scan_cursor < state.scan_end then
            local cursor = state.scan_cursor
            local query_ok, region = pcall(state.adapter.query, cursor)
            local valid, region_end = false, nil
            if query_ok then valid, region_end = valid_scan_region(region, cursor) end
            if not valid then
                state.carry = ""
                state.current_region = nil
                state.skipped_regions = state.skipped_regions + 1
                local skip_end = math.min(state.scan_end, bounded_region_end(region, cursor))
                local skipped = math.max(1, skip_end - cursor)
                state.skipped_bytes = state.skipped_bytes + skipped
                state.scan_cursor = cursor + skipped
            else
                if not same_region(state.current_region, region) then state.carry = "" end
                state.current_region = region
                local again_ok, again = pcall(state.adapter.query, cursor)
                if not again_ok or not same_region(region, again) then
                    state.carry = ""
                    state.current_region = nil
                    state.read_failures = state.read_failures + 1
                    local skipped = math.max(1, math.min(state.scan_end, next_page(cursor)) - cursor)
                    state.skipped_bytes = state.skipped_bytes + skipped
                    state.scan_cursor = cursor + skipped
                else
                    region_end = math.min(state.scan_end, region_end)
                    local amount = math.min(
                        region_end - cursor,
                        state.frame_read_budget,
                        next_page(cursor) - cursor
                    )
                    local read_ok, bytes = pcall(state.adapter.read, cursor, amount, true)
                    state.frame_read_budget = state.frame_read_budget - amount
                    if not read_ok or type(bytes) ~= "string" or #bytes ~= amount then
                        state.carry = ""
                        state.current_region = nil
                        state.read_failures = state.read_failures + 1
                        local skip_end = math.min(state.scan_end, next_page(cursor))
                        local skipped = math.max(1, skip_end - cursor)
                        state.skipped_bytes = state.skipped_bytes + skipped
                        state.scan_cursor = cursor + skipped
                    else
                        state.scanned_bytes = state.scanned_bytes + #bytes
                        scan_block(state, bytes, cursor)
                        state.scan_cursor = cursor + amount
                    end
                end
            end
        else
            break
        end
    end

    if state.phase == "scanning" and state.scan_cursor >= state.scan_end
        and state.capture_index > #state.capture_queue then
        state.done = true
        state.status = "scan_complete"
        state.result = M.manifest(state)
        return true, state.result
    end
    return false, nil, initial_budget - state.frame_read_budget
end

function M.manifest(state)
    if state.outgoing_probe then
        local windows = new_array()
        for index, window in ipairs(state.outgoing_windows) do
            local status = window.status
            if status == "pending" and state.done then
                if state.outgoing_window_phase_started and index == state.outgoing_window_index then
                    status = window.offset > 0 and "partial" or "failed"
                else
                    status = "not_attempted"
                end
            end
            windows[#windows + 1] = {
                rva = window.rva,
                size = window.size,
                status = status,
                hex = window.hex,
            }
        end
        return {
            schema_version = 1,
            mode = "outgoing_send_code_probe",
            status = state.status or "running",
            detail = state.detail,
            function_verification = "unverified",
            read_limits = {
                max_memory_read_bytes_per_step = M.OUTGOING_BUDGET_PER_STEP,
                max_memory_read_bytes_per_call = M.OUTGOING_READ_CHUNK,
                disk_hash_bytes_excluded_from_memory_budget = true,
            },
            source_build = {
                module = M.SOURCE.module,
                game_dll_sha256_expected = M.SOURCE.sha256,
                game_dll_sha256_observed = state.disk_sha256,
                game_dll_size_expected = M.SOURCE.disk_size,
                game_dll_size_observed = state.disk_size,
                pe_timestamp_expected = M.SOURCE.timestamp,
                pe_timestamp_observed = state.pe and state.pe.timestamp or nil,
                size_of_image_expected = M.SOURCE.size_of_image,
                size_of_image_observed = state.pe and state.pe.size_of_image or nil,
                code_section_rva = M.SECTION.rva,
                code_section_size = M.SECTION.size,
                code_section_flags = M.SECTION.flags,
            },
            windows = windows,
        }
    end
    return {
        schema_version = 1,
        status = state.status or "running",
        detail = state.detail,
        source_build = {
            module = M.SOURCE.module,
            game_dll_sha256_expected = M.SOURCE.sha256,
            game_dll_sha256_observed = state.disk_sha256,
            game_dll_size_expected = M.SOURCE.disk_size,
            game_dll_size_observed = state.disk_size,
            pe_timestamp_expected = M.SOURCE.timestamp,
            pe_timestamp_observed = state.pe and state.pe.timestamp or nil,
            size_of_image_expected = M.SOURCE.size_of_image,
            size_of_image_observed = state.pe and state.pe.size_of_image or nil,
            code_section_rva = M.SECTION.rva,
            code_section_size = M.SECTION.size,
            code_section_flags = M.SECTION.flags,
        },
        scan = {
            section_rva = M.SECTION.rva,
            section_size = M.SECTION.size,
            max_code_read_bytes_per_step = M.BUDGET_PER_STEP,
            disk_hash_and_pe_header_read_outside_code_budget = true,
            scanned_bytes = state.scanned_bytes,
            skipped_bytes = state.skipped_bytes,
            skipped_regions = state.skipped_regions,
            read_failures = state.read_failures,
            pattern_matches = state.match_counts,
            truncation = state.truncated,
            unreadable_candidates = state.unreadable_candidates,
            candidate_bytes = state.candidate_bytes,
        },
        known_signatures = state.known_signatures,
        candidates = state.candidates,
        function_verification = "none; RVAs and byte matches are research candidates only",
    }
end

local function json_escape(value)
    return '"' .. value:gsub('[%z\1-\31\\"]', function(character)
        local replacements = {
            ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b',
            ['\f'] = '\\f', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
        }
        return replacements[character] or string.format("\\u%04x", character:byte())
    end) .. '"'
end

local function is_array(value)
    local meta = getmetatable(value)
    if meta and meta.__json_array then return true end
    local count, maximum = 0, 0
    for key in pairs(value) do
        if type(key) ~= "number" or key < 1 or key ~= math.floor(key) then return false end
        count = count + 1
        if key > maximum then maximum = key end
    end
    return count > 0 and count == maximum
end

local function json_encode(value, stack)
    local kind = type(value)
    if kind == "nil" then return "null" end
    if kind == "boolean" then return value and "true" or "false" end
    if kind == "number" then
        if value ~= value or value == math.huge or value == -math.huge then return "null" end
        return string.format("%.0f", value)
    end
    if kind == "string" then return json_escape(value) end
    if kind ~= "table" then error("unsupported JSON value") end
    if stack[value] then error("cyclic JSON value") end
    stack[value] = true
    local out = {}
    if is_array(value) then
        for i = 1, #value do out[#out + 1] = json_encode(value[i], stack) end
        stack[value] = nil
        return "[" .. table.concat(out, ",") .. "]"
    end
    local keys = {}
    for key in pairs(value) do
        if type(key) ~= "string" then error("JSON object keys must be strings") end
        keys[#keys + 1] = key
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
        out[#out + 1] = json_escape(key) .. ":" .. json_encode(value[key], stack)
    end
    stack[value] = nil
    return "{" .. table.concat(out, ",") .. "}"
end

function M.encode_json(value)
    return json_encode(value, {})
end

function M.wrap_update(original_update, probe_step)
    local finished = false
    return function(...)
        if not finished then
            local ok, done = pcall(probe_step)
            if not ok or done == true then finished = true end
        end
        if type(original_update) == "function" then
            return original_update(...)
        end
    end
end

return M
