-- HD2聊天研究模块：包含代码扫描、只读观察与受限固定中文显示测试模式。
local core = (function()
--[[HD2_CHAT_PROBE_CORE]]
end)()

local OBSERVE_ENABLED = false --[[HD2_CHAT_OBSERVER_ENABLED]]
local DISPLAY_TEST_ENABLED = false --[[HD2_CHAT_DISPLAY_TEST_ENABLED]]
local observer_core = (function()
--[[HD2_CHAT_OBSERVER_CORE]]
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

        HD2Probe_HANDLE GetCurrentProcess(void);
        HD2Probe_HMODULE GetModuleHandleA(const char *module_name);
        HD2Probe_U32 GetModuleFileNameW(HD2Probe_HMODULE module, HD2Probe_U16 *path, HD2Probe_U32 capacity);
        HD2Probe_SIZE_T VirtualQuery(const void *address, HD2Probe_MEMORY_BASIC_INFORMATION *information, HD2Probe_SIZE_T information_size);
        int ReadProcessMemory(HD2Probe_HANDLE process, const void *address, void *buffer, HD2Probe_SIZE_T length, HD2Probe_SIZE_T *bytes_read);
        HD2Probe_HANDLE CreateFileW(const HD2Probe_U16 *path, HD2Probe_U32 access, HD2Probe_U32 share_mode, void *security, HD2Probe_U32 creation, HD2Probe_U32 attributes, HD2Probe_HANDLE template_file);
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

    local MAX_OBSERVER_ADDRESS = 0x7fffffffffff
    local MAX_OBSERVER_READ = 16 * 1024
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

    local function observer_query_address(address)
        if type(address) ~= "number" or address ~= address or address == math.huge or address == -math.huge
            or address < 0x10000 or address > MAX_OBSERVER_ADDRESS or address ~= math.floor(address) then
            return nil, "invalid_address"
        end
        local information = ffi.new("HD2Probe_MEMORY_BASIC_INFORMATION[1]")
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

    local function observer_read(address, length)
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
        local output = ffi.new("HD2Probe_U8[?]", length)
        while cursor < finish do
            local region, query_reason = observer_query_address(cursor)
            if not region then return nil, query_reason end
            local allowed, allowed_reason = observer_region_allowed(region)
            if not allowed then return nil, allowed_reason end
            if first_allocation == nil then first_allocation = region.allocation_base
            elseif first_allocation ~= region.allocation_base then return nil, "allocation_changed" end
            if region.base > cursor or region.finish <= cursor then return nil, "region_bounds" end

            local page_finish = (math.floor(cursor / 4096) + 1) * 4096
            local chunk_finish = math.min(finish, region.finish, page_finish)
            if chunk_finish <= cursor then return nil, "region_bounds" end
            local verified, verified_reason = observer_query_address(cursor)
            if not verified then return nil, verified_reason end
            local verified_allowed, verified_allowed_reason = observer_region_allowed(verified)
            if not verified_allowed then return nil, verified_allowed_reason end
            if not same_observer_region(region, verified) then return nil, "region_changed" end
            if verified.allocation_base ~= first_allocation then return nil, "allocation_changed" end

            local chunk_length = chunk_finish - cursor
            local bytes_read = ffi.new("HD2Probe_SIZE_T[1]")
            local destination = output + (cursor - address)
            local source = ffi.cast("const void *", ffi.cast("size_t", cursor))
            if kernel.ReadProcessMemory(process, source, destination, chunk_length, bytes_read) == 0 then
                return nil, "read_failed"
            end
            if tonumber(bytes_read[0]) ~= chunk_length then return nil, "short_read" end
            cursor = chunk_finish
        end
        return ffi.string(output, length), nil
    end

    local function observer_read_pointer(address)
        local bytes, reason = observer_read(address, 8)
        if not bytes then return nil, reason end
        local value = ffi.new("HD2Probe_SIZE_T[1]")
        ffi.copy(value, bytes, 8)
        local number = tonumber(value[0])
        if not number or number ~= math.floor(number) then return nil, "malformed_bytes" end
        if number == 0 then return nil, "null_pointer" end
        if number < 0x10000 or number > MAX_OBSERVER_ADDRESS then return nil, "pointer_range" end
        return number, nil
    end

    local function observer_u32(bytes, offset)
        local a, b, c, d = bytes:byte(offset + 1, offset + 4)
        if not d then return nil end
        return a + b * 256 + c * 65536 + d * 16777216
    end

    local function observer_u64(bytes, offset)
        if not bytes or offset < 0 or offset + 8 > #bytes then return nil end
        local value = ffi.new("HD2Probe_SIZE_T[1]")
        ffi.copy(value, bytes:sub(offset + 1, offset + 8), 8)
        local number = tonumber(value[0])
        if not number or number < 0 or number > MAX_OBSERVER_ADDRESS or number ~= math.floor(number) then return nil end
        return number
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

    local function observer_widget_read_widget_slot(slot)
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
        if matching_count > 1 then
            result_status = "ambiguous_key"
        elseif matching_count == 0 then
            result_status = "key_missing"
        else
            for index = 0, 63 do
                local record = observer_add(context.ring, index * 0x4B4)
                local expected_pointer = record and observer_add(record, 0xB4)
                if not record or not expected_pointer then return "read_failed" end
                if expected_pointer == value_pointer then
                    event_slot = index
                    break
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

    local adapter = {
        hash_file = hash_module_file,
        query = query,
        read = read_memory,
        hash_bytes = hash_bytes,
        output = output_manifest,
    }
    local state = core.new(adapter)
    local code_manifest_written = false
    local code_scan_done = false
    local observer_state = nil

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

            if not OBSERVE_ENABLED or not signatures_verified(manifest) then return true end
            local target_verified = not DISPLAY_TEST_ENABLED or display_target_verified(manifest)
            observer_display_native_gate = DISPLAY_TEST_ENABLED and target_verified
            if not observer_core or type(observer_core.new) ~= "function"
                or type(observer_core.step) ~= "function" or type(observer_core.manifest) ~= "function" then
                observer_log_stopped()
                return true
            end
            if not start_observer(target_verified) then return true end
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
    if DISPLAY_TEST_ENABLED then
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
