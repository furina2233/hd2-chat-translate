-- HD2 进程内 HTTP 传输加载器；仅由独立翻译包嵌入。
--[[HD2CT_NATIVE_PAYLOAD]]

return function(ffi, kernel, bcrypt, hash_bytes, u16_ascii)
    local bit = require("bit")
    local DLL_SIZE = HD2CT_DLL_SIZE
    local DLL_SHA256 = HD2CT_DLL_SHA256
    local DLL_HEX = HD2CT_DLL_HEX
    local GLOBAL_KEY = "__HD2_CHAT_HTTP_NATIVE_V2"
    local FILE_ATTRIBUTE_DIRECTORY = 0x10
    local FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    local FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    local FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    local LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800
    local INVALID_HANDLE = ffi.cast("HD2Probe_HANDLE", -1)
    local file_information_abi_ok

    local function file_information_abi_valid()
        if file_information_abi_ok ~= nil then return file_information_abi_ok end
        local ok, size, volume_offset, size_high_offset, index_low_offset = pcall(function()
            local structure = "HD2Probe_BY_HANDLE_FILE_INFORMATION"
            return ffi.sizeof(structure),
                ffi.offsetof(structure, "dwVolumeSerialNumber"),
                ffi.offsetof(structure, "nFileSizeHigh"),
                ffi.offsetof(structure, "nFileIndexLow")
        end)
        file_information_abi_ok = ok and tonumber(size) == 52
            and tonumber(volume_offset) == 28
            and tonumber(size_high_offset) == 32
            and tonumber(index_low_offset) == 48
        return file_information_abi_ok
    end

    local function ascii_hex_byte(pair)
        local function nibble(byte)
            if byte >= 48 and byte <= 57 then return byte - 48 end
            if byte >= 65 and byte <= 70 then return byte - 55 end
            if byte >= 97 and byte <= 102 then return byte - 87 end
            return nil
        end
        local high = nibble(pair:byte(1))
        local low = nibble(pair:byte(2))
        if high == nil or low == nil then return nil end
        return high * 16 + low
    end

    local function dll_payload()
        if type(DLL_SIZE) ~= "number" or DLL_SIZE < 1 or DLL_SIZE > 512 * 1024
            or DLL_SIZE ~= math.floor(DLL_SIZE)
            or type(DLL_SHA256) ~= "string" or not DLL_SHA256:match("^[0-9a-f][0-9a-f]+$")
            or #DLL_SHA256 ~= 64 or type(DLL_HEX) ~= "string" or #DLL_HEX ~= DLL_SIZE * 2 then
            return nil
        end
        local output = ffi.new("HD2Probe_U8[?]", DLL_SIZE)
        for index = 0, DLL_SIZE - 1 do
            local byte = ascii_hex_byte(DLL_HEX:sub(index * 2 + 1, index * 2 + 2))
            if byte == nil then return nil end
            output[index] = byte
        end
        if hash_bytes(ffi.string(output, DLL_SIZE)) ~= DLL_SHA256 then return nil end
        return output
    end

    local function copy_wide_prefix(source, length)
        local path = ffi.new("HD2Probe_U16[?]", length + 1)
        ffi.copy(path, source, length * 2)
        path[length] = 0
        return path
    end

    local function append_wide_ascii(source, length, suffix)
        if type(suffix) ~= "string" or suffix:find("[^A-Za-z0-9_%.%-\\]") then return nil end
        local output_length = length + #suffix
        if output_length + 1 > 32768 then return nil end
        local output = ffi.new("HD2Probe_U16[?]", output_length + 1)
        ffi.copy(output, source, length * 2)
        for index = 1, #suffix do output[length + index - 1] = suffix:byte(index) end
        output[output_length] = 0
        return output, output_length
    end

    local function inspect_handle(handle, want_directory)
        if not file_information_abi_valid() then return false end
        local information = ffi.new("HD2Probe_BY_HANDLE_FILE_INFORMATION[1]")
        if kernel.GetFileInformationByHandle(handle, information) == 0 then return false end
        local attributes = tonumber(information[0].dwFileAttributes)
        if attributes == nil or bit.band(attributes, FILE_ATTRIBUTE_REPARSE_POINT) ~= 0 then return false end
        return (bit.band(attributes, FILE_ATTRIBUTE_DIRECTORY) ~= 0) == want_directory
    end

    local function lock_directory(path, retained_handles)
        local handle = kernel.CreateFileW(
            path, 0x80, 0x3, nil, 3,
            FILE_FLAG_BACKUP_SEMANTICS + FILE_FLAG_OPEN_REPARSE_POINT, nil)
        if handle == nil or handle == INVALID_HANDLE then return false end
        if not inspect_handle(handle, true) then
            kernel.CloseHandle(handle)
            return false
        end
        retained_handles[#retained_handles + 1] = handle
        return true
    end

    local function unlock_all(handles)
        for index = #handles, 1, -1 do
            kernel.CloseHandle(handles[index])
            handles[index] = nil
        end
    end

    local function local_app_directory(retained_handles)
        local name = u16_ascii("LOCALAPPDATA")
        local buffer = ffi.new("HD2Probe_U16[32768]")
        local length = tonumber(kernel.GetEnvironmentVariableW(name, buffer, 32768))
        if not length or length < 3 or length >= 32768
            or buffer[1] ~= 58 or buffer[2] ~= 92 then
            return nil
        end
        if not (buffer[0] >= 65 and buffer[0] <= 90)
            and not (buffer[0] >= 97 and buffer[0] <= 122) then
            return nil
        end
        while length > 3 and buffer[length - 1] == 92 do length = length - 1 end
        if not lock_directory(copy_wide_prefix(buffer, 3), retained_handles) then return nil end
        local segment_start = 3
        for index = 3, length do
            if index == length or buffer[index] == 92 then
                if index == segment_start then return nil end
                if buffer[segment_start] == 46
                    and (index - segment_start == 1 or buffer[segment_start + 1] == 46) then
                    return nil
                end
                if not lock_directory(copy_wide_prefix(buffer, index), retained_handles) then
                    return nil
                end
                segment_start = index + 1
            end
        end
        return copy_wide_prefix(buffer, length), length
    end

    local function ensure_native_directory(root, root_length, retained_handles)
        local app_path = append_wide_ascii(root, root_length, "\\HD2ChatTranslate")
        local native_path, native_length = append_wide_ascii(
            root, root_length, "\\HD2ChatTranslate\\native")
        if not app_path or not native_path then return nil end
        kernel.CreateDirectoryW(app_path, nil)
        if not lock_directory(app_path, retained_handles) then return nil end
        kernel.CreateDirectoryW(native_path, nil)
        if not lock_directory(native_path, retained_handles) then return nil end
        return native_path, native_length
    end

    local function file_length(handle)
        local high = ffi.new("HD2Probe_U32[1]")
        high[0] = 0
        local low = kernel.GetFileSize(handle, high)
        if low == 0xffffffff and tonumber(kernel.GetLastError()) ~= 0 then return nil end
        if tonumber(high[0]) ~= 0 then return nil end
        return tonumber(low)
    end

    local function hash_locked_file(handle, expected_size)
        if file_length(handle) ~= expected_size then return nil end
        local algorithm_out = ffi.new("HD2Probe_BCRYPT_ALG_HANDLE[1]")
        local algorithm_name = u16_ascii("SHA256")
        if bcrypt.BCryptOpenAlgorithmProvider(algorithm_out, algorithm_name, nil, 0) ~= 0 then return nil end
        local algorithm = algorithm_out[0]
        local hash_out = ffi.new("HD2Probe_BCRYPT_HASH_HANDLE[1]")
        if bcrypt.BCryptCreateHash(algorithm, hash_out, nil, 0, nil, 0, 0) ~= 0 then
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
            return nil
        end
        local hash = hash_out[0]
        local chunk = ffi.new("HD2Probe_U8[8192]")
        local received = ffi.new("HD2Probe_U32[1]")
        local total = 0
        local ok = true
        while true do
            received[0] = 0
            if kernel.ReadFile(handle, chunk, 8192, received, nil) == 0 then
                ok = false
                break
            end
            local count = tonumber(received[0])
            if not count or count > 8192 then
                ok = false
                break
            end
            if count == 0 then break end
            total = total + count
            if total > expected_size or bcrypt.BCryptHashData(hash, chunk, count, 0) ~= 0 then
                ok = false
                break
            end
        end
        local digest = ffi.new("HD2Probe_U8[32]")
        local finished = total == expected_size and ok
            and bcrypt.BCryptFinishHash(hash, digest, 32, 0) == 0
        bcrypt.BCryptDestroyHash(hash)
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
        if not finished then return nil end
        local pieces = {}
        for index = 0, 31 do pieces[#pieces + 1] = string.format("%02x", digest[index]) end
        return table.concat(pieces)
    end

    local function open_verified_dll(path)
        local handle = kernel.CreateFileW(
            path, 0x80000000, 0x1, nil, 3,
            0x80 + FILE_FLAG_OPEN_REPARSE_POINT, nil)
        if handle == nil or handle == INVALID_HANDLE then return nil end
        if not inspect_handle(handle, false) or hash_locked_file(handle, DLL_SIZE) ~= DLL_SHA256 then
            kernel.CloseHandle(handle)
            return nil
        end
        return handle
    end

    local function write_payload(directory, directory_length, payload)
        local final_path = append_wide_ascii(directory, directory_length, "\\" .. DLL_SHA256 .. ".dll")
        if not final_path then return nil end
        local existing = open_verified_dll(final_path)
        if existing then return existing, final_path end

        local temp_handle
        local temp_path
        local sequence = math.floor((tonumber(kernel.GetTickCount64()) or 0) % 0x7fffffff)
        for attempt = 1, 16 do
            temp_path = append_wide_ascii(directory, directory_length, string.format(
                "\\.%s.%08x.%02d.tmp", DLL_SHA256, sequence, attempt))
            if not temp_path then return nil end
            temp_handle = kernel.CreateFileW(temp_path, 0x40000000, 0, nil, 1, 0x80, nil)
            if temp_handle ~= nil and temp_handle ~= INVALID_HANDLE then break end
            temp_handle = nil
        end
        if not temp_handle then return nil end
        local written = ffi.new("HD2Probe_U32[1]")
        local write_ok = kernel.WriteFile(temp_handle, payload, DLL_SIZE, written, nil) ~= 0
            and tonumber(written[0]) == DLL_SIZE
        local flush_ok = write_ok and kernel.FlushFileBuffers(temp_handle) ~= 0
        local close_ok = kernel.CloseHandle(temp_handle) ~= 0
        if not write_ok or not flush_ok or not close_ok then
            kernel.DeleteFileW(temp_path)
            return nil
        end
        if kernel.MoveFileExW(temp_path, final_path, 0x8) == 0 then
            kernel.DeleteFileW(temp_path)
        end
        local locked = open_verified_dll(final_path)
        if not locked then return nil end
        return locked, final_path
    end

    local function function_pointer(address, signature)
        if address == nil then return nil end
        return ffi.cast(signature, address)
    end

    local function has_functions(api)
        return type(api.submit) == "function" and type(api.response) == "function"
            and type(api.cancel) == "function"
    end

    local loader = {}
    function loader.load()
        local prior = rawget(_G, GLOBAL_KEY)
        if type(prior) == "table" and prior.sha256 == DLL_SHA256 and prior.size == DLL_SIZE
            and prior.abi_version == HD2CT_ABI_VERSION and has_functions(prior) then
            local ok, cancelled = pcall(prior.cancel, nil)
            if not ok or cancelled ~= true then return nil end
            return prior
        end

        local payload = dll_payload()
        if not payload then return nil end
        local retained = {}
        local root, root_length = local_app_directory(retained)
        if not root then unlock_all(retained); return nil end
        local directory, directory_length = ensure_native_directory(root, root_length, retained)
        if not directory then unlock_all(retained); return nil end
        local path, locked = nil, nil
        locked, path = write_payload(directory, directory_length, payload)
        if not locked or not path then unlock_all(retained); return nil end

        local library = kernel.LoadLibraryExW(path, nil, LOAD_LIBRARY_SEARCH_SYSTEM32)
        if library == nil then
            kernel.CloseHandle(locked)
            unlock_all(retained)
            return nil
        end
        kernel.CloseHandle(locked)
        unlock_all(retained)

        local function resolve(name, signature)
            return function_pointer(kernel.GetProcAddress(library, name), signature)
        end
        local c_submit = resolve("HD2CT_Submit", "HD2Probe_U32 (*)(const char *, const char *, HD2Probe_U32)")
        local c_poll = resolve("HD2CT_Poll", "HD2Probe_U32 (*)(const char *, char *, HD2Probe_U32, HD2Probe_U32 *)")
        local c_cancel = resolve("HD2CT_Cancel", "HD2Probe_U32 (*)(const char *)")
        local api = {
            sha256 = DLL_SHA256,
            size = DLL_SIZE,
            library = library,
            abi_version = HD2CT_ABI_VERSION,
        }
        if not c_submit or not c_poll or not c_cancel then return nil end

        local accepted = {}
        local accepted_count = 0
        local pending_cancels = {}
        local pending_cancel_set = {}

        local function safe_token(token)
            return type(token) == "string" and #token >= 3 and #token <= 128
                and not token:find("[^A-Za-z0-9_%-]")
        end

        local function forget_token(token)
            if accepted[token] then
                accepted[token] = nil
                accepted_count = math.max(0, accepted_count - 1)
            end
            if pending_cancel_set[token] then
                pending_cancel_set[token] = nil
                for index = #pending_cancels, 1, -1 do
                    if pending_cancels[index] == token then
                        table.remove(pending_cancels, index)
                        break
                    end
                end
            end
        end

        local function retry_cancels(limit)
            if type(limit) ~= "number" or limit < 1 then return 0 end
            local tries = math.min(math.floor(limit), #pending_cancels)
            local completed = 0
            for _ = 1, tries do
                local token = table.remove(pending_cancels, 1)
                if token then
                    local ok, result = pcall(c_cancel, token)
                    if ok and tonumber(result) == 1 then
                        pending_cancel_set[token] = nil
                        forget_token(token)
                        completed = completed + 1
                    else
                        pending_cancels[#pending_cancels + 1] = token
                    end
                end
            end
            return completed
        end

        function api.submit(token, body)
            retry_cancels(4)
            if not safe_token(token) or type(body) ~= "string" or #body < 1 or #body > 1023
                or accepted[token] or accepted_count >= 64 then
                return false
            end
            local buffer = ffi.new("char[?]", #body)
            ffi.copy(buffer, body, #body)
            local ok, result = pcall(c_submit, token, buffer, #body)
            if not ok or tonumber(result) ~= 1 then return false end
            accepted[token] = true
            accepted_count = accepted_count + 1
            return true
        end

        function api.response(token)
            retry_cancels(4)
            if not accepted[token] or not safe_token(token) then return nil end
            local buffer = ffi.new("char[16388]")
            local written = ffi.new("HD2Probe_U32[1]")
            written[0] = 0
            local ok, result = pcall(c_poll, token, buffer, 16388, written)
            if not ok then return "ERR\nRESPONSE_EXCEPTION" end
            if tonumber(result) ~= 1 then return nil end
            local length = tonumber(written[0])
            if not length or length < 1 or length > 16387 or length ~= math.floor(length) then
                return "ERR\nBAD_RESPONSE"
            end
            return ffi.string(buffer, length)
        end

        function api.cancel(token)
            if token == nil then
                local ok, result = pcall(c_cancel, nil)
                if not ok or tonumber(result) ~= 1 then return false end
                accepted = {}
                accepted_count = 0
                pending_cancels = {}
                pending_cancel_set = {}
                return true
            end
            if not safe_token(token) then return false end
            if not accepted[token] then return true end
            local ok, result = pcall(c_cancel, token)
            if ok and tonumber(result) == 1 then
                forget_token(token)
                return true
            end
            if not pending_cancel_set[token] and #pending_cancels < 32 then
                pending_cancel_set[token] = true
                pending_cancels[#pending_cancels + 1] = token
            end
            return false
        end

        rawset(_G, GLOBAL_KEY, api)
        return api
    end
    return loader
end
