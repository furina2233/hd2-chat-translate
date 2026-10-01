-- HD2 只读模块扫描研究探针；此文件由构建脚本嵌入纯 Lua 扫描核心。
local core = (function()
--[[HD2_CHAT_PROBE_CORE]]
end)()

local function initialize_probe()
    local ffi = require("ffi")
    ffi.cdef[[
        typedef unsigned char HD2Probe_U8;
        typedef unsigned short HD2Probe_U16;
        typedef unsigned int HD2Probe_U32;
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

    local adapter = {
        hash_file = hash_module_file,
        query = query,
        read = read_memory,
        hash_bytes = hash_bytes,
        output = output_manifest,
    }
    local state = core.new(adapter)
    local function one_probe_step()
        local step_ok, done, manifest = pcall(core.step, state, core.BUDGET_PER_STEP)
        if not step_ok then
            state.done = true
            state.status = "probe_error"
            state.detail = "probe stopped after an internal error"
            local output_ok = pcall(adapter.output, core.encode_json(core.manifest(state)))
            if not output_ok then pcall(print, "[HD2 Chat Probe] failed to write research manifest") end
            return true
        end
        if done then
            local output_ok = pcall(adapter.output, core.encode_json(manifest))
            if not output_ok then pcall(print, "[HD2 Chat Probe] failed to write research manifest") end
        end
        return done
    end
    return one_probe_step
end

local original_update = _G.update
local setup_ok, probe_step = pcall(initialize_probe)
if setup_ok then
    _G.update = core.wrap_update(original_update, probe_step)
else
    pcall(print, "[HD2 Chat Probe] initialization failed; research scan did not start")
end
