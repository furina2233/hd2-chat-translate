"""独立网络翻译Addon的PE边界、打包与Lua语法回归。"""

from __future__ import annotations

from datetime import datetime, timezone
import copy
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
import menu_locales  # noqa: E402
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
            "abi_version": 2,
            "filename": "hd2ct_http.dll",
            "size": len(dll),
            "sha256": hashlib.sha256(dll).hexdigest(),
            "architecture": "x86_64",
            "imports": ["KERNEL32.dll", "WINHTTP.dll"],
            "target_languages_sha256": builder.target_languages.catalogue_sha256(),
        }
    ).encode("utf-8")


def make_entry(dll: bytes) -> bytes:
    catalogue = builder.target_languages.load_catalogue()
    native_source = builder.standalone_module_source(
        (ROOT / "game" / "chat_http_native.lua").read_bytes(),
        dll,
        fake_metadata(dll),
        catalogue,
    )
    settings_source = builder.target_language_settings_source(
        (ROOT / "game" / "settings.lua").read_bytes(),
        catalogue,
        menu_locales.load_catalogue(),
    )
    return builder.entry_source(
        (ROOT / "game" / "chat_probe.lua").read_bytes(),
        (ROOT / "game" / "chat_probe_core.lua").read_bytes(),
        (ROOT / "game" / "chat_observe_core.lua").read_bytes(),
        translate_source=(ROOT / "game" / "chat_translate_core.lua").read_bytes(),
        standalone_source=native_source,
        settings_source=settings_source,
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
    local cancel_all_called = false

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
    exports.HD2CT_Cancel = ffi.cast("HD2Probe_U32 (*)(const char *)", function(token)
        if token == nil then cancel_all_called = true end
        return 1
    end)
    local expected_exports = {
        HD2CT_Submit = true,
        HD2CT_Poll = true,
        HD2CT_Cancel = true,
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
    local api = loader.load()
    local result = {api and "loaded" or "failed", tostring(load_calls), tostring(load_lock_ok),
        tostring(load_flag_ok), tostring(bad_export), tostring(deleted_unowned), tostring(move_flags_ok),
        tostring(api and api.abi_version or -1), tostring(#deleted_paths), tostring(submitted)}
    if api then
        api.submit("hd2ct_1", "body")
        result[#result + 1] = api.response("hd2ct_1") or "pending"
        api.cancel(nil)
    end
    result[#result + 1] = tostring(cancel_all_called)
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
                include_name = builder.default_output_path(instant).name
                no_dependencies_name = builder.default_output_path(
                    instant, include_dependencies=False
                ).name
            output = Path(directory) / include_name
            self.assertEqual(include_name, "HD2ChatTranslateIncludeDependencies20261003000405.zip")
            self.assertEqual(no_dependencies_name, "HD2ChatTranslateNoDependencies20261003000405.zip")
            self.assertTrue(builder.is_delivery_filename(include_name))
            self.assertTrue(builder.is_delivery_filename(no_dependencies_name))
            self.assertFalse(builder.is_delivery_filename("HD2ChatTranslate20261003000405.zip"))
            self.assertFalse(builder.is_delivery_filename("HD2ChatTranslateNoDependencies20260230000000.zip"))
            no_anchor, include_anchor = builder._paired_output_paths(
                Path(directory) / no_dependencies_name
            )
            self.assertEqual((no_anchor.name, include_anchor.name), (no_dependencies_name, include_name))
            self.assertEqual(
                builder._paired_output_paths(Path(directory) / include_name),
                (no_anchor, include_anchor),
            )
            with self.assertRaisesRegex(ValueError, "不能同时指定"):
                builder._paired_output_paths(
                    Path(directory) / include_name, output_dir=directory
                )

            _, _, loader_resource, loader_readme, loader_manifest = synthetic_shared_loader_package()
            loader_assets = (loader_resource, loader_readme, loader_manifest)
            with mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets):
                files = builder.addon_files(entry)
            with mock.patch.object(
                builder, "_load_dependency_assets", side_effect=AssertionError("不应读取前置")
            ):
                no_dependencies_files = builder.addon_files(entry, include_dependencies=False)
            no_dependencies_expected = {
                "manifest.json",
                "Addon/9ba626afa44a3aa3.patch_0",
                "Addon/9ba626afa44a3aa3.patch_0.stream",
                "Addon/9ba626afa44a3aa3.patch_0.gpu_resources",
                "LICENSE",
                "LICENSES/cJSON-LICENSE.txt",
            }
            self.assertEqual(set(no_dependencies_files), no_dependencies_expected)
            no_dependencies_archive = no_dependencies_files["Addon/9ba626afa44a3aa3.patch_0"]
            no_dependencies_header = struct.unpack_from("<III20sQQ24s", no_dependencies_archive, 0)
            self.assertEqual(no_dependencies_header[2], 1)
            no_dependencies_entry = struct.unpack_from("<7Q6I", no_dependencies_archive, 104)
            self.assertEqual(no_dependencies_entry[0], builder.resource_hash(builder.RESOURCE_NAME))
            self.assertEqual(
                no_dependencies_archive[
                    no_dependencies_entry[2]:no_dependencies_entry[2] + no_dependencies_entry[7]
                ],
                struct.pack("<II", len(entry), 2) + entry,
            )
            no_dependencies_manifest = json.loads(no_dependencies_files["manifest.json"])
            self.assertEqual(no_dependencies_manifest["Guid"], builder.ADDON_GUID)
            self.assertEqual(no_dependencies_manifest["Name"], "HD2 Chat Translate Standalone")
            self.assertIn("需分别安装这两个前置", no_dependencies_manifest["Description"])
            self.assertTrue(entry.startswith(b"-- HD2-Addon: mods/hd2chat/HD2ChatTranslate\n"))
            archive = files["Addon/9ba626afa44a3aa3.patch_0"]
            header = struct.unpack_from("<III20sQQ24s", archive, 0)
            self.assertEqual((header[0], header[1], header[2], header[4]), (0xF0000011, 1, 3, len(archive)))
            type_record = struct.unpack_from("<IIQIIII", archive, 72)
            self.assertEqual(type_record, (0, 0, builder.RESOURCE_TYPE, 3, 0, 16, 16))
            entries = [struct.unpack_from("<7Q6I", archive, 104 + 80 * index) for index in range(3)]
            self.assertEqual(
                [item[0] for item in entries],
                [
                    builder.SHARED_LOADER_RESOURCE_HASH,
                    builder.resource_hash(builder.RESOURCE_NAME),
                    builder.MOD_OPTIONS_MENU_RESOURCE_HASH,
                ],
            )
            self.assertEqual([item[-1] for item in entries], [0, 1, 2])
            parsed = read_archive_contract(archive)
            self.assertEqual((parsed["type_count"], parsed["file_count"]), (1, 3))
            self.assertEqual([row[-1] for row in parsed["file_rows"]], [0, 1, 2])
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
            menu_resource = builder.load_mod_options_menu_resource()
            self.assertEqual(
                archive[entries[2][2]:entries[2][2] + entries[2][7]],
                menu_resource,
            )
            bad_menu_zip = Path(directory) / "tampered-menu.zip"
            tampered_menu = bytearray(builder.MOD_OPTIONS_MENU_ZIP.read_bytes())
            tampered_menu[-1] ^= 1
            bad_menu_zip.write_bytes(tampered_menu)
            with self.assertRaisesRegex(ValueError, "ZIP SHA-256"):
                builder.load_mod_options_menu_resource(bad_menu_zip)
            with self.assertRaisesRegex(ValueError, "缺少ModOptionsMenu"):
                builder.load_mod_options_menu_resource(Path(directory) / "missing-menu.zip")
            with self.assertRaisesRegex(ValueError, "输出不能覆盖"):
                builder.build_artifact(builder.MOD_OPTIONS_MENU_ZIP)
            self.assertIn(b"HD2CT_DLL_HEX", archive[entries[1][2]:entries[1][2] + entries[1][7]])
            self.assertIn(b"target_language_settings", entry)

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
            self.assertEqual(files["LICENSES/ModOptionsMenu-LICENSE.txt"], builder.MOD_OPTIONS_MENU_LICENSE.read_bytes())
            self.assertEqual(
                hashlib.sha256(files["LICENSES/ModOptionsMenu-LICENSE.txt"]).hexdigest(),
                builder.MOD_OPTIONS_MENU_LICENSE_SHA256,
            )
            self.assertIn(b"releases/tag/v1.2", files["LICENSES/ModOptionsMenu-SOURCE.txt"])
            self.assertIn(b"Zero-Clause BSD (0BSD)", files["LICENSES/ModOptionsMenu-SOURCE.txt"])
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

            catalogue = builder.target_languages.load_catalogue()
            localizations = menu_locales.load_catalogue()
            settings_source = builder.target_language_settings_source(
                (ROOT / "game" / "settings.lua").read_bytes(), catalogue, localizations
            ).decode("utf-8")
            settings_delimiter = "[========["
            settings_closing = "]========]"
            self.assertNotIn(settings_closing, settings_source)
            english = localizations["locales"]["en"]
            english_values = [english[key] for key in menu_locales.TEXT_KEYS]
            english_values.extend(english["target_languages"].values())
            self.assertTrue(all(value.isascii() for value in english_values))

            with self.assertRaisesRegex(ValueError, "重复JSON键"):
                menu_locales.parse_catalogue(
                    b'{"schema_version":1,"schema_version":1,"locales":{}}'
                )
            invalid_locales = copy.deepcopy(localizations)
            del invalid_locales["locales"]["ru"]
            with self.assertRaisesRegex(ValueError, "locale集合字段无效"):
                menu_locales.validate_catalogue(invalid_locales)
            invalid_locales = copy.deepcopy(localizations)
            invalid_locales["locales"]["en"]["unexpected"] = "text"
            with self.assertRaisesRegex(ValueError, "en文本字段无效"):
                menu_locales.validate_catalogue(invalid_locales)
            invalid_locales = copy.deepcopy(localizations)
            invalid_locales["locales"]["en"]["target_language_description"] = "x" * 401
            with self.assertRaisesRegex(ValueError, "400字符上限"):
                menu_locales.validate_catalogue(invalid_locales)
            invalid_locales = copy.deepcopy(localizations)
            invalid_locales["locales"]["en"]["timeout_choice_format"] = "{timeout} seconds"
            with self.assertRaisesRegex(ValueError, "seconds.*占位符"):
                menu_locales.validate_catalogue(invalid_locales)

            locale_assertions = []
            for locale_tag in menu_locales.SUPPORTED_LOCALES:
                translated = localizations["locales"][locale_tag]
                locale_assertions.extend((
                    "_G.BingusTranslations.game_language = "
                    + builder._lua_string_literal(locale_tag) + "\n",
                    "assert(language_spec.label() == "
                    + builder._lua_string_literal(translated["target_language_label"]) + ")\n",
                    "assert(language_spec.description() == "
                    + builder._lua_string_literal(translated["target_language_description"]) + ")\n",
                    "assert(enabled_spec.label() == "
                    + builder._lua_string_literal(translated["enabled_label"]) + ")\n",
                    "assert(enabled_spec.description() == "
                    + builder._lua_string_literal(translated["enabled_description"]) + ")\n",
                    "assert(timeout_spec.label() == "
                    + builder._lua_string_literal(translated["timeout_label"]) + ")\n",
                    "assert(timeout_spec.description() == "
                    + builder._lua_string_literal(translated["timeout_description"]) + ")\n",
                ))
                for index, language in enumerate(catalogue["languages"], start=1):
                    locale_assertions.append(
                        f"assert(language_spec.choices[{index}]() == "
                        + builder._lua_string_literal(
                            translated["target_languages"][language["id"]]
                        ) + ")\n"
                    )
                for index, choice in enumerate(catalogue["menu_options"]["timeout"]["choices"], start=1):
                    locale_assertions.append(
                        f"assert(timeout_spec.choices[{index}]() == "
                        + builder._lua_string_literal(
                            translated["timeout_choice_format"].replace(
                                "{seconds}", str(choice["seconds"])
                            )
                        ) + ")\n"
                    )
            settings_script = (
                "local module = assert(loadstring("
                + settings_delimiter + settings_source + settings_closing
                + "))()\n"
                "local step = module.new()\n"
                "local calls, saved_specs = {}, {}\n"
                "assert(step(0) == false)\n"
                "local function forbidden() error('option values must not be read') end\n"
                "_G.ModOptionsMenu = {api = 1, version = 2, get = forbidden, set = forbidden, on_change = forbidden, "
                "register_option = function(id, spec) calls[#calls + 1] = id; saved_specs[id] = spec; return true end}\n"
                "assert(step(999) == false and #calls == 0)\n"
                "assert(step(1000) == false and #calls == 0)\n"
                "_G.ModOptionsMenu.version = 4\n"
                "assert(step(1999) == false and #calls == 0)\n"
                "local fail_enabled = true\n"
                "_G.ModOptionsMenu.register_option = function(id, spec) calls[#calls + 1] = id; "
                "if id == 'hd2chattranslate.enabled' and fail_enabled then fail_enabled = false; return false end; "
                "saved_specs[id] = spec; return true end\n"
                "assert(step(2000) == false and #calls == 2)\n"
                "assert(calls[1] == 'hd2chattranslate.target_language' and calls[2] == 'hd2chattranslate.enabled')\n"
                "assert(step(2500) == false and #calls == 2)\n"
                "assert(step(3000) == true and #calls == 4)\n"
                "assert(calls[3] == 'hd2chattranslate.enabled' and calls[4] == 'hd2chattranslate.timeout')\n"
                "assert(step(4000) == true and #calls == 4)\n"
                "local language_spec = saved_specs['hd2chattranslate.target_language']\n"
                "assert(language_spec.type == 'choice' and type(language_spec.label) == 'function')\n"
                "assert(language_spec.mod == 'HD2 Chat Translate' and language_spec.mod_id == 'hd2chattranslate')\n"
                "assert(language_spec.default == 1 and #language_spec.choices == 10)\n"
                "for i = 1, #language_spec.choices do assert(type(language_spec.choices[i]) == 'function') end\n"
                "local enabled_spec = saved_specs['hd2chattranslate.enabled']\n"
                "assert(enabled_spec.type == 'toggle' and enabled_spec.default == true)\n"
                "assert(type(enabled_spec.label) == 'function' and type(enabled_spec.description) == 'function')\n"
                "assert(enabled_spec.mod_id == 'hd2chattranslate')\n"
                "local timeout_spec = saved_specs['hd2chattranslate.timeout']\n"
                "assert(timeout_spec.type == 'choice' and timeout_spec.default == 2)\n"
                "assert(type(timeout_spec.label) == 'function' and type(timeout_spec.description) == 'function')\n"
                "assert(#timeout_spec.choices == 3)\n"
                "for i = 1, #timeout_spec.choices do assert(type(timeout_spec.choices[i]) == 'function') end\n"
                "_G.BingusTranslations = nil\n"
                "assert(language_spec.label() == 'Target language')\n"
                "assert(enabled_spec.description() == 'The chat box is monitored whether this is enabled or not. To remove the mod completely, uninstall it in the mod manager.')\n"
                "assert(timeout_spec.choices[2]() == '20 seconds')\n"
                "_G.BingusTranslations = {version = 1, game_language = 'en'}\n"
                "local language_registry = _G.BingusTranslations\n"
                + "".join(locale_assertions)
                + "assert(_G.BingusTranslations == language_registry and language_registry.version == 1)\n"
                + "assert(#calls == 4 and calls[1] == 'hd2chattranslate.target_language' and calls[4] == 'hd2chattranslate.timeout')\n"
                + "assert(language_spec.default == 1 and timeout_spec.default == 2 and #language_spec.choices == 10)\n"
                + "_G.BingusTranslations.game_language = 'fr-CA'; assert(language_spec.label() == "
                + builder._lua_string_literal(localizations["locales"]["fr"]["target_language_label"]) + ")\n"
                + "_G.BingusTranslations.game_language = 'es-MX'; assert(language_spec.label() == "
                + builder._lua_string_literal(localizations["locales"]["es"]["target_language_label"]) + ")\n"
                + "_G.BingusTranslations.game_language = 'pt-PT'; assert(language_spec.label() == "
                + builder._lua_string_literal(localizations["locales"]["pt"]["target_language_label"]) + ")\n"
                + "_G.BingusTranslations.game_language = 'unknown-XX'; assert(language_spec.label() == 'Target language')\n"
                + "_G.BingusTranslations.version = 2; assert(language_spec.label() == 'Target language')\n"
                + "_G.BingusTranslations = {version = 1, game_language = 5}; assert(language_spec.label() == 'Target language')\n"
                + "_G.BingusTranslations = setmetatable({}, {__index = function() error('registry metamethod called') end})\n"
                + "assert(language_spec.label() == 'Target language')\n"
                + "_G.BingusTranslations = 'invalid'; assert(language_spec.label() == 'Target language')\n"
                + "assert(#calls == 4 and language_spec.default == 1 and timeout_spec.default == 2)\n"
                "local logs, failed_step = {}, module.new()\n"
                "_G.print = function(message) logs[#logs + 1] = message end\n"
                "local failed_calls, failed_once = {}, true\n"
                "_G.ModOptionsMenu = {api = 1, version = 3, register_option = function(id, spec) "
                "failed_calls[#failed_calls + 1] = id; "
                "if id == 'hd2chattranslate.enabled' and failed_once then failed_once = false; return false end; "
                "return true end}\n"
                "assert(failed_step(0) == false and #failed_calls == 2 and #logs == 1)\n"
                "assert(failed_step(500) == false and #failed_calls == 2 and #logs == 1)\n"
                "assert(failed_step(1000) == true and #failed_calls == 4 and #logs == 1)\n"
                "assert(failed_calls[1] == 'hd2chattranslate.target_language' and failed_calls[3] == 'hd2chattranslate.enabled')\n"
                "assert(failed_calls[4] == 'hd2chattranslate.timeout')\n"
                "local clock_calls, step_calls, game_calls, now = 0, 0, 0, 0\n"
                "local wrapped = module.wrap_update(function(...) game_calls = game_calls + 1; return 'game', nil, ... end, "
                "function(time) step_calls = step_calls + 1; return time >= 10 end, "
                "function() clock_calls = clock_calls + 1; return now end)\n"
                "local first, second, third = wrapped('payload')\n"
                "assert(first == 'game' and second == nil and third == 'payload')\n"
                "assert(select('#', wrapped('payload')) == 3)\n"
                "now = 10; assert(select('#', wrapped('payload')) == 3)\n"
                "assert(clock_calls == 3 and step_calls == 3 and game_calls == 3)\n"
                "wrapped('payload'); assert(clock_calls == 3 and step_calls == 3 and game_calls == 4)\n"
                "assert(select('#', wrapped('payload', nil)) == 4)\n"
                "assert(clock_calls == 3 and step_calls == 3 and game_calls == 5)\n"
                "local throwing = module.wrap_update(function() error('game update failure') end, "
                "function() return true end, function() return 0 end)\n"
                "local call_ok, call_error = pcall(throwing)\n"
                "assert(not call_ok and tostring(call_error):find('game update failure', 1, true))\n"
                "RESULT = 'settings registration ok'"
            )
            self.assertEqual(self.lua.run(settings_script), "settings registration ok")

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
            old_abi_metadata = dict(metadata)
            old_abi_metadata["abi_version"] = 1
            with self.assertRaisesRegex(ValueError, "ABI"):
                builder.standalone_module_source(
                    (ROOT / "game" / "chat_http_native.lua").read_bytes(),
                    dll,
                    json.dumps(old_abi_metadata).encode("utf-8"),
                )
            missing_catalogue_metadata = dict(metadata)
            del missing_catalogue_metadata["target_languages_sha256"]
            with self.assertRaisesRegex(ValueError, "字段不匹配"):
                builder.standalone_module_source(
                    (ROOT / "game" / "chat_http_native.lua").read_bytes(),
                    dll,
                    json.dumps(missing_catalogue_metadata).encode("utf-8"),
                )
            wrong_catalogue_metadata = dict(metadata)
            wrong_catalogue_metadata["target_languages_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "目标语言目录摘要"):
                builder.standalone_module_source(
                    (ROOT / "game" / "chat_http_native.lua").read_bytes(),
                    dll,
                    json.dumps(wrong_catalogue_metadata).encode("utf-8"),
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

            pair_directory = Path(directory) / "pair-output"
            with (
                mock.patch.object(builder, "_build_packaged_entry", wraps=builder._build_packaged_entry) as entry_builder,
                mock.patch.object(builder, "_read_common_license_files", wraps=builder._read_common_license_files) as license_reader,
                mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets) as loader_reader,
                mock.patch.object(builder, "load_mod_options_menu_resource", wraps=builder.load_mod_options_menu_resource) as menu_reader,
            ):
                pair_paths = builder.build_artifacts(
                    output_dir=pair_directory,
                    native_dll=dll_path,
                    native_meta=meta_path,
                    now=instant,
                )
            self.assertEqual(
                [path.name for path in pair_paths],
                [no_dependencies_name, include_name],
            )
            self.assertEqual((entry_builder.call_count, license_reader.call_count), (1, 1))
            self.assertEqual((loader_reader.call_count, menu_reader.call_count), (1, 1))
            with (
                zipfile.ZipFile(pair_paths[0]) as no_dependencies_package,
                zipfile.ZipFile(pair_paths[1]) as include_dependencies_package,
            ):
                self.assertIsNone(no_dependencies_package.testzip())
                self.assertIsNone(include_dependencies_package.testzip())
                no_names = set(no_dependencies_package.namelist())
                include_names = set(include_dependencies_package.namelist())
                self.assertEqual(no_names, no_dependencies_expected)
                self.assertEqual(include_names, set(files))
                self.assertNotIn("LICENSES/ModOptionsMenu-LICENSE.txt", no_names)
                self.assertIn("LICENSES/ModOptionsMenu-LICENSE.txt", include_names)
                self.assertEqual(
                    no_dependencies_package.read("Addon/9ba626afa44a3aa3.patch_0"),
                    no_dependencies_files["Addon/9ba626afa44a3aa3.patch_0"],
                )
                no_manifest = json.loads(no_dependencies_package.read("manifest.json"))
                include_manifest = json.loads(include_dependencies_package.read("manifest.json"))
                self.assertEqual((no_manifest["Guid"], no_manifest["Name"]),
                                 (include_manifest["Guid"], include_manifest["Name"]))
                no_archive = no_dependencies_package.read("Addon/9ba626afa44a3aa3.patch_0")
                include_archive = include_dependencies_package.read("Addon/9ba626afa44a3aa3.patch_0")
                self.assertEqual(struct.unpack_from("<III20sQQ24s", no_archive, 0)[2], 1)
                self.assertEqual(struct.unpack_from("<III20sQQ24s", include_archive, 0)[2], 3)
                self.assertEqual(
                    no_dependencies_package.read("LICENSES/cJSON-LICENSE.txt"),
                    include_dependencies_package.read("LICENSES/cJSON-LICENSE.txt"),
                )

            missing_loader = Path(directory) / "missing-loader.zip"
            missing_menu = Path(directory) / "missing-menu.zip"
            no_dependencies_single_path = Path(directory) / "single-no-dependencies.zip"
            with mock.patch.object(
                builder, "_load_dependency_assets", side_effect=AssertionError("不应读取缺失前置")
            ):
                no_dependencies_single = builder.build_artifact(
                    no_dependencies_single_path,
                    include_dependencies=False,
                    loader_zip=missing_loader,
                    menu_zip=missing_menu,
                    native_dll=dll_path,
                    native_meta=meta_path,
                )
            with zipfile.ZipFile(no_dependencies_single) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), no_dependencies_expected)

            collision_directory = Path(directory) / "collision-output"
            collision_directory.mkdir()
            collision_no_dependencies, collision_include_dependencies = builder._paired_output_paths(
                output_dir=collision_directory, now=instant
            )
            old_content = b"keep existing package"
            collision_no_dependencies.write_bytes(old_content)
            with self.assertRaisesRegex(ValueError, "已存在，拒绝覆盖"):
                builder.build_artifacts(
                    output_dir=collision_directory,
                    native_dll=dll_path,
                    native_meta=meta_path,
                    now=instant,
                )
            self.assertEqual(collision_no_dependencies.read_bytes(), old_content)
            self.assertFalse(collision_include_dependencies.exists())

            reverse_collision_directory = Path(directory) / "reverse-collision-output"
            reverse_collision_directory.mkdir()
            reverse_no_dependencies, reverse_include_dependencies = builder._paired_output_paths(
                output_dir=reverse_collision_directory, now=instant
            )
            reverse_old_content = b"keep existing include-dependencies package"
            reverse_include_dependencies.write_bytes(reverse_old_content)
            with self.assertRaisesRegex(ValueError, "已存在，拒绝覆盖"):
                builder.build_artifacts(
                    output_dir=reverse_collision_directory,
                    native_dll=dll_path,
                    native_meta=meta_path,
                    now=instant,
                )
            self.assertEqual(reverse_include_dependencies.read_bytes(), reverse_old_content)
            self.assertFalse(reverse_no_dependencies.exists())

            race_directory = Path(directory) / "race-output"
            race_no_dependencies, race_include_dependencies = builder._paired_output_paths(
                output_dir=race_directory, now=instant
            )
            original_path_open = Path.open

            def create_racing_include_target(path, mode="r", *args, **kwargs):
                if path == race_include_dependencies and mode == "xb":
                    path.write_bytes(b"created by competing process")
                return original_path_open(path, mode, *args, **kwargs)

            with mock.patch.object(Path, "open", new=create_racing_include_target):
                with self.assertRaises(FileExistsError):
                    builder.build_artifacts(
                        output_dir=race_directory,
                        native_dll=dll_path,
                        native_meta=meta_path,
                        now=instant,
                    )
            self.assertFalse(race_no_dependencies.exists())
            self.assertEqual(
                race_include_dependencies.read_bytes(), b"created by competing process"
            )

            cli_outputs = (
                Path(directory) / no_dependencies_name,
                Path(directory) / include_name,
            )
            with (
                mock.patch.object(builder, "build_artifacts", return_value=cli_outputs) as cli_builder,
                mock.patch.object(sys, "argv", ["build_package.py", "--output", str(Path(directory) / include_name)]),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                builder.main()
            self.assertEqual(cli_builder.call_args.args, (Path(directory) / include_name,))
            self.assertIn(no_dependencies_name, stdout.getvalue())
            self.assertIn(include_name, stdout.getvalue())

            with (
                mock.patch.object(builder, "build_artifacts", return_value=cli_outputs) as cli_builder,
                mock.patch.object(sys, "argv", ["build_package.py", "--output-dir", str(pair_directory)]),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                builder.main()
            self.assertIsNone(cli_builder.call_args.args[0])
            self.assertEqual(cli_builder.call_args.kwargs["output_dir"], Path(pair_directory))
            self.assertIn(no_dependencies_name, stdout.getvalue())
            self.assertIn(include_name, stdout.getvalue())

            with (
                mock.patch.object(builder, "build_artifacts") as cli_builder,
                mock.patch.object(sys, "argv", ["build_package.py", "--output", str(Path(directory) / "HD2ChatTranslate20261003000405.zip")]),
                mock.patch("sys.stderr", new_callable=io.StringIO),
                self.assertRaises(SystemExit) as cli_error,
            ):
                builder.main()
            self.assertEqual(cli_error.exception.code, 2)
            cli_builder.assert_not_called()

    def test_loader_holds_verified_read_lock_through_absolute_hardened_load(self):
        result = self.run_loader("positive")
        self.assertEqual(result[0:4], ["loaded", "1", "true", "true"])
        self.assertEqual(result[4:8], ["false", "false", "true", "2"])
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
