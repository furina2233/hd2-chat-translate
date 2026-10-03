"""独立网络翻译Addon的PE边界、打包与Lua语法回归。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import build_package as builder  # noqa: E402
from lua_support import LUA_DLL, LuaJIT  # noqa: E402


def fake_x64_dll() -> bytes:
    """生成仅用于builder边界检查的最小PE32+ DLL字节。"""
    pe_offset = 0x80
    optional_size = 0xF0
    section_offset = pe_offset + 24 + optional_size
    output = bytearray(0x400)
    output[:2] = b"MZ"
    struct.pack_into("<I", output, 0x3C, pe_offset)
    output[pe_offset : pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", output, pe_offset + 4, 0x8664, 1, 0, 0, 0, optional_size, 0x2022)
    optional = pe_offset + 24
    struct.pack_into("<H", output, optional, 0x20B)
    struct.pack_into("<I", output, optional + 16, 0x1000)
    struct.pack_into("<I", output, optional + 56, 0x2000)
    section = section_offset
    output[section : section + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", output, section + 8, 0x1000, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", output, section + 36, 0x60000020)
    return bytes(output)


def synthetic_shared_loader_package(
    *,
    stream: bytes = b"",
    gpu_resources: bytes = b"",
    include_readme: bool = True,
    include_manifest: bool = True,
    entry_data_offset: int = 192,
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """生成独立测试用的v18结构ZIP，不执行也不依赖本机官方包。"""
    lua_bytes = b"\x1bLJ\x02" + b"L" * (20612 - 4)
    resource = struct.pack("<II", len(lua_bytes), 2) + lua_bytes
    patch = bytearray(20816)
    struct.pack_into("<III20sQQ24s", patch, 0, 0xF0000011, 1, 1, bytes(20), 20816, 0, bytes(24))
    struct.pack_into("<IIQIIII", patch, 72, 0, 0, builder.RESOURCE_TYPE, 1, 0, 16, 16)
    struct.pack_into(
        "<7Q6I",
        patch,
        104,
        builder.SHARED_LOADER_RESOURCE_HASH,
        builder.RESOURCE_TYPE,
        entry_data_offset,
        0,
        0,
        0,
        0,
        len(resource),
        0,
        0,
        16,
        16,
        0,
    )
    patch[192 : 192 + len(resource)] = resource
    readme = b"Upstream README fixture\r\n"
    manifest = b'{"name":"loader fixture"}\r\n'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr(builder.SHARED_LOADER_PATCH_MEMBER, patch)
        package.writestr(builder.SHARED_LOADER_STREAM_MEMBER, stream)
        package.writestr(builder.SHARED_LOADER_GPU_MEMBER, gpu_resources)
        if include_readme:
            package.writestr(builder.SHARED_LOADER_README_MEMBER, readme)
        if include_manifest:
            package.writestr(builder.SHARED_LOADER_MANIFEST_MEMBER, manifest)
        package.writestr("manifest.json", b"must not be copied")
        package.writestr("thumbnail.png", b"not read")
    return buffer.getvalue(), bytes(patch), resource, readme, manifest




def read_archive_contract(data: bytes) -> dict[str, object]:
    """按TOC头部独立字段语义解析资源分组与文件行。"""
    if len(data) < 72:
        raise ValueError("archive头部被截断")
    magic, type_count, file_count = struct.unpack_from("<III", data, 0)
    if magic != 0xF0000011 or not 0 < type_count <= 1024 or not 0 < file_count <= 4096:
        raise ValueError("archive头或计数无效")
    type_rows_offset = 72
    file_rows_offset = type_rows_offset + 32 * type_count
    data_start = (file_rows_offset + 80 * file_count + 15) & ~15
    if file_rows_offset + 80 * file_count > len(data):
        raise ValueError("archive TOC表超出文件范围")

    type_rows = [
        struct.unpack_from("<IIQIIII", data, type_rows_offset + 32 * index)
        for index in range(type_count)
    ]
    grouped_counts = {row[2]: row[3] for row in type_rows}
    if len(grouped_counts) != type_count or any(count <= 0 for count in grouped_counts.values()):
        raise ValueError("archive typeRows重复或分组计数无效")
    if sum(grouped_counts.values()) != file_count:
        raise ValueError("typeRows分组计数与header file_count不一致")

    file_rows = [
        struct.unpack_from("<7Q6I", data, file_rows_offset + 80 * index)
        for index in range(file_count)
    ]
    observed_counts = {file_type: 0 for file_type in grouped_counts}
    for row in file_rows:
        if row[1] not in grouped_counts:
            raise ValueError("fileRow的type未出现在typeRows中")
        observed_counts[row[1]] += 1
    if observed_counts != grouped_counts:
        raise ValueError("fileRows类型计数与typeRows分组不一致")

    file_indices = [row[-1] for row in file_rows]
    if len(set(file_indices)) != file_count or sorted(file_indices) != list(range(file_count)):
        raise ValueError("fileRow序号必须唯一且从零连续递增")
    cursor = data_start
    for row in file_rows:
        offset, size = row[2], row[7]
        if offset != cursor or offset % 16 or size <= 0 or offset + size > len(data):
            raise ValueError("fileRow资源范围或16字节对齐无效")
        cursor = (offset + size + 15) & ~15
    if cursor != len(data):
        raise ValueError("archive资源尾部或填充长度不匹配")
    return {
        "magic": magic,
        "type_count": type_count,
        "file_count": file_count,
        "type_rows": type_rows,
        "file_rows": file_rows,
        "data_start": data_start,
    }


def fake_metadata(dll: bytes) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "abi_version": 1,
            "filename": "hd2ct_http.dll",
            "size": len(dll),
            "sha256": hashlib.sha256(dll).hexdigest(),
            "architecture": "x86_64",
            "imports": ["KERNEL32.dll", "WINHTTP.dll"],
        }
    ).encode("utf-8")


def make_entry(dll: bytes) -> bytes:
    native_source = builder.standalone_module_source(
        (ROOT / "game" / "chat_http_native.lua").read_bytes(),
        dll,
        fake_metadata(dll),
    )
    return builder.entry_source(
        (ROOT / "game" / "chat_probe.lua").read_bytes(),
        (ROOT / "game" / "chat_probe_core.lua").read_bytes(),
        (ROOT / "game" / "chat_observe_core.lua").read_bytes(),
        translate_source=(ROOT / "game" / "chat_translate_core.lua").read_bytes(),
        standalone_source=native_source,
        standalone=True,
    )


def production_file_info_declaration() -> str:
    """读取Addon实际声明，避免模拟测试与生产布局各自漂移。"""
    source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
    start = source.index("typedef struct HD2Probe_BY_HANDLE_FILE_INFORMATION")
    ending = "} HD2Probe_BY_HANDLE_FILE_INFORMATION;"
    end = source.index(ending, start) + len(ending)
    return source[start:end]


def fake_loader_script(mode: str) -> str:
    """用LuaJIT内存对象模拟Win32文件、bcrypt、加载器和DLL ABI。"""
    dll = fake_x64_dll()
    digest = hashlib.sha256(dll).hexdigest()
    module = builder.standalone_module_source(
        (ROOT / "game" / "chat_http_native.lua").read_bytes(),
        dll,
        fake_metadata(dll),
    ).decode("utf-8")
    harness = r'''
local function run_loader()
    local ffi = require("ffi")
    ffi.cdef[[
        typedef unsigned char HD2Probe_U8;
        typedef unsigned short HD2Probe_U16;
        typedef unsigned int HD2Probe_U32;
        typedef void *HD2Probe_HANDLE;
        typedef void *HD2Probe_HMODULE;
        typedef void *HD2Probe_BCRYPT_HANDLE;
        typedef HD2Probe_BCRYPT_HANDLE HD2Probe_BCRYPT_ALG_HANDLE;
        typedef HD2Probe_BCRYPT_HANDLE HD2Probe_BCRYPT_HASH_HANDLE;
        FILE_INFORMATION_DECLARATION
    ]]
    local mode = "MODE"
    local digest = "DIGEST"
    local expected_hex = "DLLHEX"
    local expected_bytes = {}
    for i = 1, #expected_hex, 2 do
        expected_bytes[#expected_bytes + 1] = string.char(tonumber(expected_hex:sub(i, i + 1), 16))
    end
    local expected_payload = table.concat(expected_bytes)
    local base = [[C:\Users\LocalUser\AppData\Local]]
    local final_path = base .. [[\HD2ChatTranslate\native\]] .. digest .. ".dll"
    local function wide_text(pointer)
        local value = ffi.cast("const HD2Probe_U16 *", pointer)
        local chars = {}
        for i = 0, 32767 do
            local code = tonumber(value[i])
            if code == 0 then break end
            chars[#chars + 1] = string.char(code)
        end
        return table.concat(chars)
    end
    local function wide_fill(path, output)
        for i = 1, #path do output[i - 1] = path:byte(i) end
        output[#path] = 0
        return #path
    end
    local function u16_ascii(value)
        local result = ffi.new("HD2Probe_U16[?]", #value + 1)
        for i = 1, #value do result[i - 1] = value:byte(i) end
        result[#value] = 0
        return result
    end

    local dirs = {}
    dirs["C:\\"] = {attributes = 0x10}
    dirs["C:\\Users"] = {attributes = 0x10}
    dirs["C:\\Users\\LocalUser"] = {attributes = 0x10}
    dirs["C:\\Users\\LocalUser\\AppData"] = {attributes = 0x10}
    dirs["C:\\Users\\LocalUser\\AppData\\Local"] = {attributes = 0x10}
    if mode == "reparse_ancestor" then dirs["C:\\Users"].attributes = 0x410 end
    local files = {}
    if mode == "wrong_file_sha" or mode == "wrong_file_size" or mode == "reparse_file" then
        files[final_path] = {
            data = mode == "wrong_file_size" and expected_payload:sub(1, -2) or string.rep("X", #expected_payload),
            attributes = mode == "reparse_file" and 0x400 or 0x80,
            path = final_path,
            kind = "file",
        }
    end
    local handles = {}
    local next_handle = 10
    local invalid_handle = ffi.cast("HD2Probe_HANDLE", -1)
    local load_calls, load_lock_ok, load_flag_ok = 0, false, false
    local bad_export = false
    local deleted_unowned = false
    local deleted_paths = {}
    local move_flags_ok = true
    local information_calls = 0
    local submitted = 0
    local enabled = true
    local disabled = false

    local function add_handle(reference, share, position)
        next_handle = next_handle + 1
        handles[next_handle] = {reference = reference, share = share, position = position or 1, open = true}
        return next_handle
    end
    local kernel = {}
    function kernel.GetEnvironmentVariableW(_, output, capacity)
        local length = wide_fill(base, output)
        return length < capacity and length or 0
    end
    function kernel.CreateDirectoryW(pointer)
        local path = wide_text(pointer)
        if dirs[path] then return 0 end
        dirs[path] = {attributes = 0x10}
        return 1
    end
    function kernel.CreateFileW(pointer, access, share, _, creation, attributes)
        local path = wide_text(pointer)
        if creation == 3 then
            local object = dirs[path] or files[path]
            if not object then return invalid_handle end
            return add_handle(object, share)
        end
        if creation ~= 1 or dirs[path] or files[path] then return invalid_handle end
        local object = {data = "", attributes = 0x80, path = path, kind = "file", owned_temp = true}
        files[path] = object
        return add_handle(object, share)
    end
    function kernel.GetFileInformationByHandle(handle, output)
        information_calls = information_calls + 1
        local entry = handles[tonumber(handle)]
        if not entry or not entry.open then return 0 end
        if ffi.sizeof(output[0]) < 52 then return 0 end
        ffi.fill(output, 52, 0)
        output[0].dwFileAttributes = entry.reference.attributes
        return 1
    end
    function kernel.GetFileSize(handle, high)
        local entry = handles[tonumber(handle)]
        if not entry or not entry.open then return 0xffffffff end
        high[0] = 0
        return #entry.reference.data
    end
    function kernel.GetLastError() return 0 end
    function kernel.ReadFile(handle, output, requested, received)
        local entry = handles[tonumber(handle)]
        if not entry or not entry.open then return 0 end
        local data = entry.reference.data
        local count = math.min(requested, math.max(0, #data - entry.position + 1))
        if count > 0 then ffi.copy(output, data:sub(entry.position, entry.position + count - 1), count) end
        entry.position = entry.position + count
        received[0] = count
        return 1
    end
    function kernel.WriteFile(handle, source, length, written)
        local entry = handles[tonumber(handle)]
        if not entry or not entry.open then return 0 end
        local count = length
        entry.reference.data = ffi.string(source, count)
        written[0] = count
        return 1
    end
    function kernel.FlushFileBuffers(handle)
        return handles[tonumber(handle)] and 1 or 0
    end
    function kernel.CloseHandle(handle)
        local entry = handles[tonumber(handle)]
        if not entry then return 0 end
        entry.open = false
        return 1
    end
    function kernel.MoveFileExW(source, destination, flags)
        local from, to = wide_text(source), wide_text(destination)
        if flags ~= 0x8 then move_flags_ok = false end
        if files[to] then return 0 end
        if not files[from] or not files[from].owned_temp then return 0 end
        files[to] = files[from]
        files[from] = nil
        return 1
    end
    function kernel.DeleteFileW(pointer)
        local path = wide_text(pointer)
        deleted_paths[#deleted_paths + 1] = path
        local object = files[path]
        if not object or not object.owned_temp then
            deleted_unowned = true
            return 0
        end
        files[path] = nil
        return 1
    end
    function kernel.GetTickCount64() return 123456 end
    function kernel.LoadLibraryExW(pointer, _, flags)
        load_calls = load_calls + 1
        local path = wide_text(pointer)
        load_flag_ok = flags == 0x800 and path == final_path
        for _, entry in pairs(handles) do
            if entry.open and entry.reference == files[final_path] then
                load_lock_ok = entry.share == 1
            end
        end
        return ffi.cast("HD2Probe_HMODULE", 0x1234)
    end

    local exports = {}
    exports.HD2CT_ABIVersion = ffi.cast("HD2Probe_U32 (*)(void)", function() return 1 end)
    exports.HD2CT_InitializeEnvironment = ffi.cast("HD2Probe_U32 (*)(void)", function() return 0 end)
    exports.HD2CT_IsEnabled = ffi.cast("HD2Probe_U32 (*)(void)", function() return enabled and 1 or 0 end)
    exports.HD2CT_LastStatus = ffi.cast(
        "HD2Probe_U32 (*)(void)", function() return 0 end)
    exports.HD2CT_Submit = ffi.cast(
        "HD2Probe_U32 (*)(const char *, const char *, HD2Probe_U32)",
        function(_, _, _) submitted = submitted + 1; return 1 end)
    exports.HD2CT_Poll = ffi.cast(
        "HD2Probe_U32 (*)(const char *, char *, HD2Probe_U32, HD2Probe_U32 *)",
        function(_, output, capacity, written)
            local result = "OK\ntranslated"
            if capacity < #result + 1 then return 0 end
            ffi.copy(output, result, #result)
            output[#result] = 0
            written[0] = #result
            return 1
        end)
    exports.HD2CT_Cancel = ffi.cast("HD2Probe_U32 (*)(const char *)", function() return 1 end)
    exports.HD2CT_Disable = ffi.cast("void (*)(void)", function() disabled = true end)
    local expected_exports = {
        HD2CT_ABIVersion = true,
        HD2CT_InitializeEnvironment = true,
        HD2CT_IsEnabled = true,
        HD2CT_LastStatus = true,
        HD2CT_Submit = true,
        HD2CT_Poll = true,
        HD2CT_Cancel = true,
        HD2CT_Disable = true,
    }
    function kernel.GetProcAddress(_, name)
        if not expected_exports[name] then bad_export = true; return nil end
        return exports[name]
    end

    local bcrypt = {}
    local bytes_being_hashed = ""
    function bcrypt.BCryptOpenAlgorithmProvider(output) output[0] = ffi.cast("HD2Probe_BCRYPT_ALG_HANDLE", 0x11); return 0 end
    function bcrypt.BCryptCreateHash(_, output) output[0] = ffi.cast("HD2Probe_BCRYPT_HASH_HANDLE", 0x22); bytes_being_hashed = ""; return 0 end
    function bcrypt.BCryptHashData(_, bytes, length) bytes_being_hashed = bytes_being_hashed .. ffi.string(bytes, length); return 0 end
    function bcrypt.BCryptFinishHash(_, output)
        if bytes_being_hashed ~= expected_payload then return -1 end
        for i = 0, 31 do output[i] = tonumber(digest:sub(i * 2 + 1, i * 2 + 2), 16) end
        return 0
    end
    function bcrypt.BCryptDestroyHash() return 0 end
    function bcrypt.BCryptCloseAlgorithmProvider() return 0 end
    local function source_hash(bytes)
        if bytes == expected_payload and mode ~= "bad_payload_hash" then return digest end
        return string.rep("0", 64)
    end

    local factory = (function()
MODULE_SOURCE
    end)()
    local loader = factory(ffi, kernel, bcrypt, source_hash, u16_ascii)
    local api, init_status = loader.load()
    local result = {api and "loaded" or "failed", tostring(load_calls), tostring(load_lock_ok),
        tostring(load_flag_ok), tostring(bad_export), tostring(deleted_unowned), tostring(move_flags_ok),
        tostring(init_status or -1), tostring(#deleted_paths), tostring(submitted)}
    if api then
        api.submit("hd2ct_1", "body")
        result[#result + 1] = api.response("hd2ct_1") or "pending"
        api.disable()
    end
    result[#result + 1] = tostring(disabled)
    result[#result + 1] = tostring(information_calls)
    RESULT = table.concat(result, "|")
end
run_loader()
'''
    file_information = production_file_info_declaration()
    return (
        harness.replace("MODE", mode)
        .replace("DIGEST", digest)
        .replace("DLLHEX", dll.hex())
        .replace("FILE_INFORMATION_DECLARATION", file_information)
        .replace("MODULE_SOURCE", module)
    )


class StandaloneBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = LuaJIT(LUA_DLL) if LUA_DLL is not None else None

    def run_loader(self, mode: str) -> list[str]:
        if self.lua is None:
            self.skipTest("本机未提供LuaJIT lua51.dll")
        return self.lua.run(fake_loader_script(mode)).split("|")

    def test_default_standalone_package_contract_and_crc(self):
        dll = fake_x64_dll()
        entry = make_entry(dll)
        self.assertLessEqual(len(entry), builder.MAX_SOURCE_BYTES)
        self.assertIn(b"local STANDALONE_ENABLED = true", entry)
        self.assertIn(b"local TRANSLATE_ENABLED = true", entry)
        self.assertIn(b"local OBSERVE_ENABLED = false", entry)

        instant = datetime(2026, 10, 2, 16, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(builder, "ROOT", Path(directory)):
                default_name = builder.default_output_path(instant).name
            output = Path(directory) / default_name
            self.assertEqual(default_name, "HD2ChatTranslate20261003000405.zip")
            self.assertTrue(builder.is_delivery_filename(default_name))

            _, _, loader_resource, loader_readme, loader_manifest = synthetic_shared_loader_package()
            loader_assets = (loader_resource, loader_readme, loader_manifest)
            with mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets):
                files = builder.addon_files(entry)
            archive = files["Addon/9ba626afa44a3aa3.patch_0"]
            header = struct.unpack_from("<III20sQQ24s", archive, 0)
            self.assertEqual((header[0], header[1], header[2], header[4]), (0xF0000011, 1, 2, len(archive)))
            type_record = struct.unpack_from("<IIQIIII", archive, 72)
            self.assertEqual(type_record, (0, 0, builder.RESOURCE_TYPE, 2, 0, 16, 16))
            entries = [struct.unpack_from("<7Q6I", archive, 104 + 80 * index) for index in range(2)]
            self.assertEqual(
                [item[0] for item in entries],
                [builder.SHARED_LOADER_RESOURCE_HASH, builder.resource_hash(builder.RESOURCE_NAME)],
            )
            self.assertEqual([item[-1] for item in entries], [0, 1])
            parsed = read_archive_contract(archive)
            self.assertEqual((parsed["type_count"], parsed["file_count"]), (1, 2))
            self.assertEqual([row[-1] for row in parsed["file_rows"]], [0, 1])
            for item in entries:
                self.assertEqual(item[1], builder.RESOURCE_TYPE)
                self.assertEqual(item[2] % 16, 0)
                self.assertLessEqual(item[2] + item[7], len(archive))
            self.assertEqual(
                archive[entries[0][2]:entries[0][2] + entries[0][7]],
                loader_resource,
            )
            self.assertEqual(
                archive[entries[1][2]:entries[1][2] + entries[1][7]],
                struct.pack("<II", len(entry), 2) + entry,
            )
            self.assertIn(b"HD2CT_DLL_HEX", archive[entries[1][2]:entries[1][2] + entries[1][7]])

            manifest = json.loads(files["manifest.json"])
            self.assertEqual(manifest["Guid"], builder.ADDON_GUID)
            self.assertEqual(manifest["Name"], "HD2 Chat Translate Standalone")
            self.assertEqual(len(manifest["Options"]), 1)
            self.assertEqual(manifest["Options"][0]["Include"], ["Addon"])
            self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.stream"], b"")
            self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.gpu_resources"], b"")
            for text in (manifest["Description"], manifest["Options"][0]["Description"]):
                self.assertIn("first-mod-wins", text)
                self.assertIn("列表最底", text)
                self.assertIn("列表最顶", text)
            self.assertEqual(files["LICENSES/BingusSharedLoader-README.txt"], loader_readme)
            self.assertEqual(files["LICENSES/BingusSharedLoader-manifest.json"], loader_manifest)
            self.assertIn(b"GNU GENERAL PUBLIC LICENSE", files["LICENSE"])
            self.assertEqual(files["LICENSE"], builder.PROJECT_LICENSE.read_bytes())
            self.assertEqual(files["LICENSES/cJSON-LICENSE.txt"], builder.STANDALONE_LICENSE.read_bytes())

            packed_entry = entry.decode("utf-8")
            delimiter = "[========["
            closing = "]========]"
            self.assertNotIn(closing, packed_entry)
            compile_script = (
                "local chunk, err = loadstring("
                + delimiter + packed_entry + closing
                + "); assert(chunk, err); RESULT='standalone syntax ok'"
            )
            self.assertIsNotNone(self.lua)
            self.assertEqual(self.lua.run(compile_script), "standalone syntax ok")

            dll_path = Path(directory) / "hd2ct_http.dll"
            meta_path = Path(directory) / "hd2ct_http.meta.json"
            dll_path.write_bytes(dll)
            metadata = json.loads(fake_metadata(dll))
            bad_metadata = dict(metadata)
            bad_metadata["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "大小或 SHA-256"):
                builder.standalone_module_source(
                    (ROOT / "game" / "chat_http_native.lua").read_bytes(),
                    dll,
                    json.dumps(bad_metadata).encode("utf-8"),
                )

            meta_path.write_bytes(fake_metadata(dll))
            with (
                mock.patch.object(builder, "STANDALONE_DLL", dll_path),
                mock.patch.object(builder, "STANDALONE_META", meta_path),
                mock.patch.object(builder, "default_output_path", return_value=output),
                mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets),
            ):
                built = builder.build_artifact()
            with zipfile.ZipFile(built) as package:
                self.assertIsNone(package.testzip())
                names = package.namelist()
                self.assertEqual(set(names), set(files))
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(package.read("LICENSE"), builder.PROJECT_LICENSE.read_bytes())

    def test_loader_holds_verified_read_lock_through_absolute_hardened_load(self):
        result = self.run_loader("positive")
        self.assertEqual(result[0:4], ["loaded", "1", "true", "true"])
        self.assertEqual(result[4:8], ["false", "false", "true", "0"])
        self.assertEqual(result[10:12], ["OK\ntranslated", "true"])

    def test_loader_rejects_bad_payload_file_size_sha_and_reparse_paths(self):
        for mode in (
            "bad_payload_hash",
            "wrong_file_sha",
            "wrong_file_size",
            "reparse_file",
            "reparse_ancestor",
        ):
            with self.subTest(mode=mode):
                result = self.run_loader(mode)
                self.assertEqual(result[0], "failed")
                self.assertEqual(result[1], "0")
                self.assertEqual(result[4], "false")
                self.assertEqual(result[5], "false")

    @unittest.skipUnless(os.name == "nt", "真实Win32文件信息API仅在Windows执行")
    def test_win32_file_information_layout_preserves_canary(self):
        if self.lua is None:
            self.skipTest("本机未提供LuaJIT lua51.dll")
        declaration = production_file_info_declaration()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file-information-canary.bin"
            target.write_bytes(b"native ABI layout check")
            encoded_path = target.resolve().as_posix().replace("/", "\\").encode("utf-16-le")
            code_units = struct.unpack("<" + "H" * (len(encoded_path) // 2), encoded_path)
            assignments = "\n".join(
                f"path[{index}] = 0x{unit:04x}" for index, unit in enumerate(code_units)
            )
            script = f'''\nlocal ffi = require("ffi")
ffi.cdef[[
    typedef unsigned char HD2Probe_U8;
    typedef unsigned short HD2Probe_U16;
    typedef unsigned int HD2Probe_U32;
    typedef void *HD2Probe_HANDLE;
    {declaration}
    HD2Probe_HANDLE CreateFileW(const HD2Probe_U16 *path, HD2Probe_U32 access,
        HD2Probe_U32 share, void *security, HD2Probe_U32 creation,
        HD2Probe_U32 attributes, HD2Probe_HANDLE template_file);
    int GetFileInformationByHandle(HD2Probe_HANDLE file,
        HD2Probe_BY_HANDLE_FILE_INFORMATION *information);
    int CloseHandle(HD2Probe_HANDLE handle);
]]
assert(ffi.sizeof("HD2Probe_BY_HANDLE_FILE_INFORMATION") == 52)
assert(ffi.offsetof("HD2Probe_BY_HANDLE_FILE_INFORMATION", "dwVolumeSerialNumber") == 28)
assert(ffi.offsetof("HD2Probe_BY_HANDLE_FILE_INFORMATION", "nFileSizeHigh") == 32)
assert(ffi.offsetof("HD2Probe_BY_HANDLE_FILE_INFORMATION", "nFileIndexLow") == 48)
local path = ffi.new("HD2Probe_U16[{len(code_units) + 1}]")
{assignments}
path[{len(code_units)}] = 0
local kernel = ffi.load("kernel32")
local handle = kernel.CreateFileW(path, 0x80000000, 0x1, nil, 3, 0x80, nil)
assert(handle ~= ffi.cast("HD2Probe_HANDLE", -1), "CreateFileW failed")
local guarded = ffi.new("HD2Probe_U8[60]")
ffi.fill(guarded, 60, 0xA7)
local information = ffi.cast("HD2Probe_BY_HANDLE_FILE_INFORMATION *", guarded)
assert(kernel.GetFileInformationByHandle(handle, information) ~= 0, "GetFileInformationByHandle failed")
for index = 52, 59 do assert(guarded[index] == 0xA7, "Win32 API overwrote canary") end
assert(kernel.CloseHandle(handle) ~= 0)
RESULT = "52-byte ABI and canary ok"
'''
            self.assertEqual(self.lua.run(script), "52-byte ABI and canary ok")

if __name__ == "__main__":
    unittest.main()
