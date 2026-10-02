"""独立网络翻译Addon的PE边界、打包与Lua语法回归。"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import build_chat_probe as builder  # noqa: E402
from test_chat_probe import LUA_DLL, LuaJIT  # noqa: E402


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


def read_synthetic_shared_loader_assets(
    path: Path,
    package_bytes: bytes,
    patch: bytes,
    resource: bytes,
) -> tuple[bytes, bytes, bytes]:
    path.write_bytes(package_bytes)
    with (
        mock.patch.object(builder, "SHARED_LOADER_ZIP", path),
        mock.patch.object(
            builder, "SHARED_LOADER_ZIP_SHA256", hashlib.sha256(package_bytes).hexdigest()
        ),
        mock.patch.object(builder, "SHARED_LOADER_PATCH_SHA256", hashlib.sha256(patch).hexdigest()),
        mock.patch.object(
            builder, "SHARED_LOADER_RESOURCE_SHA256", hashlib.sha256(resource).hexdigest()
        ),
    ):
        return builder.load_shared_loader_assets()


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
    if mode == "old_48_struct" then
        assert(ffi.sizeof("HD2Probe_BY_HANDLE_FILE_INFORMATION") == 48)
    end
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
    local resolved = {}
    local deleted_unowned = false
    local deleted_paths = {}
    local move_flags_ok = true
    local information_calls = 0
    local submitted = 0
    local enabled = true
    local disabled = false
    local cancel_calls = 0
    local cancel_busy = mode == "cancel_busy_once" or mode == "cancel_busy_cap"
    local first_busy = mode == "cancel_busy_once"

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
        if mode == "old_48_struct" then
            output[0].dwFileAttributes = entry.reference.attributes
            return 1
        end
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
        local count = mode == "short_write" and math.max(0, length - 1) or length
        entry.reference.data = ffi.string(source, count)
        written[0] = count
        return 1
    end
    function kernel.FlushFileBuffers(handle)
        return mode == "flush_fail" and 0 or (handles[tonumber(handle)] and 1 or 0)
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
        if mode == "rename_fail" or files[to] then return 0 end
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
        "HD2Probe_U32 (*)(void)", function() return mode == "http401" and 401 or 0 end)
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
    exports.HD2CT_Cancel = ffi.cast("HD2Probe_U32 (*)(const char *)", function()
        cancel_calls = cancel_calls + 1
        if cancel_busy then
            if first_busy then first_busy = false; return 0 end
            if mode == "cancel_busy_cap" then return 0 end
        end
        return 1
    end)
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
        resolved[name] = true
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
        if mode == "positive" then
            api.submit("hd2ct_1", "body")
            result[#result + 1] = api.response("hd2ct_1") or "pending"
            api.disable()
        elseif mode == "http401" then
            result[#result + 1] = tostring(api.enabled())
            result[#result + 1] = tostring(api.last_status())
        elseif mode == "cancel_busy_once" then
            api.submit("hd2ct_1", "body")
            local response = api.response("hd2ct_1") or "pending"
            local first = api.cancel("hd2ct_1")
            local retried = api.retry_cancels(4)
            result[#result + 1] = tostring(first)
            result[#result + 1] = tostring(retried)
            result[#result + 1] = response
            api.disable()
        elseif mode == "cancel_busy_cap" then
            for i = 1, 40 do api.submit("hd2ct_" .. tostring(i), "x") end
            for i = 1, 40 do api.cancel("hd2ct_" .. tostring(i)) end
            local before = cancel_calls
            cancel_busy = false
            result[#result + 1] = tostring(api.retry_cancels(100))
            result[#result + 1] = tostring(cancel_calls - before)
        end
    end
    result[#result + 1] = tostring(disabled)
    result[#result + 1] = tostring(information_calls)
    RESULT = table.concat(result, "|")
end
run_loader()
'''
    file_information = production_file_info_declaration()
    if mode == "old_48_struct":
        file_information = re.sub(
            r"^[ \t]*HD2Probe_U32 dwVolumeSerialNumber;\r?\n",
            "",
            file_information,
            flags=re.MULTILINE,
        )
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

    def test_delivery_name_uses_fixed_beijing_time_and_valid_cli_format(self):
        instant = datetime(2026, 10, 2, 16, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(builder, "ROOT", Path(directory)):
                output = builder.default_output_path(instant)
        self.assertEqual(output.name, "HD2ChatTranslate20261003000405.zip")
        self.assertTrue(builder.is_delivery_filename(output.name))
        self.assertFalse(builder.is_delivery_filename("HD2ChatTranslate20261303000405.zip"))
        self.assertFalse(builder.is_delivery_filename("HD2ChatTranslate.zip"))

    def test_all_modes_use_the_timestamped_default_and_custom_fixture_paths_remain_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            outputs = [
                temporary / f"HD2ChatTranslate2026100212000{index}.zip"
                for index in range(5)
            ]
            native = temporary / "hd2ct_http.dll"
            metadata = temporary / "hd2ct_http.meta.json"
            license_file = temporary / "LICENSE"
            native.write_bytes(fake_x64_dll())
            metadata.write_bytes(fake_metadata(native.read_bytes()))
            license_file.write_text("license", encoding="utf-8")
            modes = (
                {},
                {"observe": True},
                {"display_test": True},
                {"translate": True},
                {"standalone": True},
            )
            with (
                mock.patch.object(builder, "default_output_path", side_effect=outputs),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", temporary / "missing-receipt.json"),
                mock.patch.object(builder, "STANDALONE_DLL", native),
                mock.patch.object(builder, "STANDALONE_META", metadata),
                mock.patch.object(builder, "STANDALONE_LICENSE", license_file),
                mock.patch.object(builder, "standalone_module_source", return_value=b"module"),
                mock.patch.object(builder, "entry_source", return_value=b"entry"),
                mock.patch.object(builder, "addon_files", return_value={"manifest.json": b"{}"}),
            ):
                for mode, expected in zip(modes, outputs):
                    self.assertEqual(builder.build_artifact(**mode), expected)
                    self.assertTrue(expected.is_file())

            fixture = temporary / "internal-fixture.zip"
            self.assertEqual(builder._select_output_path(fixture), fixture)

    def test_default_output_collision_and_corrupt_receipt_never_overwrite_or_create(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            existing = temporary / "HD2ChatTranslate20261002120000.zip"
            existing.write_bytes(b"keep this package")
            receipt = temporary / "receipt.json"
            receipt.write_text("{broken", encoding="utf-8")
            with (
                mock.patch.object(builder, "default_output_path", return_value=existing),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt),
            ):
                with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                    builder.build_artifact()
            self.assertEqual(existing.read_bytes(), b"keep this package")

            with mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt):
                not_created = temporary / "HD2ChatTranslate20261002120001.zip"
                with self.assertRaisesRegex(ValueError, "拒绝覆盖默认输出"):
                    builder._protect_deployed_source(not_created)
                self.assertFalse(not_created.exists())

                receipt.write_text(
                    json.dumps(
                        {
                            "sourceZips": {
                                "probe": {"path": str(existing)},
                                "loader": {"path": str(temporary / "loader.zip")},
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "仍被部署收据引用"):
                    builder._protect_deployed_source(existing)
                self.assertEqual(existing.read_bytes(), b"keep this package")

    def test_cli_rejects_non_timestamp_output_name(self):
        with (
            mock.patch.object(sys, "argv", ["build_chat_probe.py", "--output", "custom.zip"]),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            builder.main()
        self.assertEqual(raised.exception.code, 2)

    def test_output_creation_refuses_a_collision_after_the_precheck(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "HD2ChatTranslate20261002120000.zip"
            output.write_bytes(b"preserve the concurrently created package")
            with (
                mock.patch.object(builder, "default_output_path", return_value=output),
                mock.patch.object(builder.os.path, "lexists", return_value=False),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", Path(directory) / "missing.json"),
                self.assertRaises(FileExistsError),
            ):
                builder.build_artifact()
            self.assertEqual(output.read_bytes(), b"preserve the concurrently created package")

    def test_cli_accepts_timestamped_output_path(self):
        output = Path("HD2ChatTranslate20261002123456.zip")
        with (
            mock.patch.object(sys, "argv", ["build_chat_probe.py", "--output", str(output)]),
            mock.patch.object(builder, "build_artifact", return_value=output) as build,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            builder.main()
        build.assert_called_once_with(output, False, False, False, standalone=False)

    def test_lua_archive_helper_preserves_legacy_single_resource_bytes_and_bounds_list(self):
        resource = struct.pack("<II", 4, 2) + b"data"
        archive = builder.make_single_resource_archive(0x1234, resource)
        self.assertEqual(
            hashlib.sha256(archive).hexdigest(),
            "aabae1880d3785ba7c14a4787f7d08ee635bccd0685d032c8eba26ee36397675",
        )
        self.assertEqual(builder.make_lua_resource_archive([(0x1234, resource)]), archive)
        with self.assertRaisesRegex(ValueError, "重复"):
            builder.make_lua_resource_archive([(0x1234, resource), (0x1234, resource)])
        with self.assertRaisesRegex(ValueError, "数量上限"):
            builder.make_lua_resource_archive(
                [(index, resource) for index in range(builder.MAX_ARCHIVE_LUA_RESOURCES + 1)]
            )

    def test_local_shared_loader_zip_is_pinned_and_reads_only_required_assets(self):
        package_bytes, patch, resource, readme, manifest = synthetic_shared_loader_package()
        with tempfile.TemporaryDirectory() as directory:
            actual = read_synthetic_shared_loader_assets(
                Path(directory) / "loader.zip", package_bytes, patch, resource
            )
        self.assertEqual(actual, (resource, readme, manifest))

    def test_local_shared_loader_rejects_missing_bad_and_nonempty_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            missing = temporary / "missing.zip"
            with mock.patch.object(builder, "SHARED_LOADER_ZIP", missing):
                with self.assertRaisesRegex(ValueError, "缺少本机"):
                    builder.load_shared_loader_assets()

            package_bytes, patch, resource, _, _ = synthetic_shared_loader_package()
            path = temporary / "wrong-sha.zip"
            path.write_bytes(package_bytes)
            with (
                mock.patch.object(builder, "SHARED_LOADER_ZIP", path),
                mock.patch.object(builder, "SHARED_LOADER_ZIP_SHA256", "0" * 64),
            ):
                with self.assertRaisesRegex(ValueError, "ZIP SHA-256"):
                    builder.load_shared_loader_assets()

            for sidecars in ({"stream": b"unexpected"}, {"gpu_resources": b"unexpected"}):
                invalid_zip, invalid_patch, invalid_resource, _, _ = synthetic_shared_loader_package(
                    **sidecars
                )
                with self.assertRaisesRegex(ValueError, "sidecar.*必须为空"):
                    read_synthetic_shared_loader_assets(
                        temporary / "nonempty-sidecar.zip",
                        invalid_zip,
                        invalid_patch,
                        invalid_resource,
                    )

            malformed_zip, malformed_patch, malformed_resource, _, _ = synthetic_shared_loader_package(
                entry_data_offset=208
            )
            with self.assertRaisesRegex(ValueError, "TOC entry布局"):
                read_synthetic_shared_loader_assets(
                    temporary / "wrong-layout.zip",
                    malformed_zip,
                    malformed_patch,
                    malformed_resource,
                )

            missing_doc_zip, missing_doc_patch, missing_doc_resource, _, _ = (
                synthetic_shared_loader_package(include_manifest=False)
            )
            with self.assertRaisesRegex(ValueError, "缺少或重复白名单条目"):
                read_synthetic_shared_loader_assets(
                    temporary / "missing-doc.zip",
                    missing_doc_zip,
                    missing_doc_patch,
                    missing_doc_resource,
                )

    def run_loader(self, mode: str) -> list[str]:
        if self.lua is None:
            self.skipTest("本机未提供LuaJIT lua51.dll")
        return self.lua.run(fake_loader_script(mode)).split("|")

    def test_native_dll_metadata_and_pe_entry_are_checked(self):
        dll = fake_x64_dll()
        size, digest = builder.validate_native_dll(dll, fake_metadata(dll))
        self.assertEqual((size, digest), (len(dll), hashlib.sha256(dll).hexdigest()))

        bad_meta = json.loads(fake_metadata(dll))
        bad_meta["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "大小或 SHA-256"):
            builder.validate_native_dll(dll, json.dumps(bad_meta).encode())

        bad_architecture = bytearray(dll)
        struct.pack_into("<H", bad_architecture, 0x84, 0x14C)
        with self.assertRaisesRegex(ValueError, "x64 PE DLL"):
            builder.validate_native_dll(bytes(bad_architecture), fake_metadata(bytes(bad_architecture)))

        bad_entry = bytearray(dll)
        struct.pack_into("<I", bad_entry, 0x80 + 24 + 16, 0x3000)
        with self.assertRaisesRegex(ValueError, "入口地址"):
            builder.validate_native_dll(bytes(bad_entry), fake_metadata(bytes(bad_entry)))

    def test_standalone_payload_is_single_marker_hex_and_size_bounded(self):
        template = (ROOT / "game" / "chat_http_native.lua").read_bytes()
        dll = fake_x64_dll()
        packed = builder.standalone_module_source(template, dll, fake_metadata(dll))
        self.assertNotIn(builder.NATIVE_PAYLOAD_MARKER, packed)
        self.assertIn(f'local HD2CT_DLL_SIZE = {len(dll)}'.encode(), packed)
        self.assertIn(hashlib.sha256(dll).hexdigest().encode(), packed)
        self.assertIn(dll.hex().encode(), packed)

        with self.assertRaisesRegex(ValueError, "恰好包含一个payload标记"):
            builder.standalone_module_source(template + builder.NATIVE_PAYLOAD_MARKER, dll, fake_metadata(dll))

    def test_standalone_mode_flags_manifest_license_and_zip_crc(self):
        dll = fake_x64_dll()
        entry = make_entry(dll)
        self.assertLessEqual(len(entry), builder.MAX_SOURCE_BYTES)
        self.assertIn(b"local STANDALONE_ENABLED = true", entry)
        self.assertIn(b"local TRANSLATE_ENABLED = true", entry)
        self.assertIn(b"local OBSERVE_ENABLED = false", entry)

        _, _, loader_resource, loader_readme, loader_manifest = synthetic_shared_loader_package()
        loader_assets = (loader_resource, loader_readme, loader_manifest)
        with mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets):
            files = builder.addon_files(entry, standalone=True)
        self.assertEqual(
            set(files),
            {
                "manifest.json",
                "Addon/9ba626afa44a3aa3.patch_0",
                "Addon/9ba626afa44a3aa3.patch_0.stream",
                "Addon/9ba626afa44a3aa3.patch_0.gpu_resources",
                "LICENSES/cJSON-LICENSE.txt",
                "LICENSES/BingusSharedLoader-README.txt",
                "LICENSES/BingusSharedLoader-manifest.json",
                "LICENSES/BingusSharedLoader-SOURCE.txt",
            },
        )
        archive = files["Addon/9ba626afa44a3aa3.patch_0"]
        header = struct.unpack_from("<III20sQQ24s", archive, 0)
        self.assertEqual((header[0], header[1], header[2], header[4]), (0xF0000011, 2, 1, len(archive)))
        type_record = struct.unpack_from("<IIQIIII", archive, 72)
        self.assertEqual(type_record, (0, 0, builder.RESOURCE_TYPE, 2, 0, 16, 16))
        entries = [struct.unpack_from("<7Q6I", archive, 104 + 80 * index) for index in range(2)]
        self.assertEqual([item[0] for item in entries], [builder.SHARED_LOADER_RESOURCE_HASH, builder.resource_hash(builder.RESOURCE_NAME)])
        self.assertEqual(len({item[0] for item in entries}), 2)
        for item in entries:
            self.assertEqual(item[1], builder.RESOURCE_TYPE)
            self.assertEqual(item[2] % 16, 0)
            self.assertGreater(item[7], 0)
            self.assertLessEqual(item[2] + item[7], len(archive))
        first_blob = archive[entries[0][2] : entries[0][2] + entries[0][7]]
        chat_blob = archive[entries[1][2] : entries[1][2] + entries[1][7]]
        self.assertEqual(first_blob, loader_resource)
        self.assertEqual(chat_blob, struct.pack("<II", len(entry), 2) + entry)
        self.assertIn(b"HD2CT_DLL_HEX", chat_blob)
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(manifest["Guid"], builder.ADDON_GUID)
        self.assertEqual(manifest["Name"], "HD2 Chat Translate Standalone")
        self.assertEqual(len(manifest["Options"]), 1)
        self.assertEqual(manifest["Options"][0]["Include"], ["Addon"])
        for text in (manifest["Description"], manifest["Options"][0]["Description"]):
            self.assertIn("无需运行伴随程序", text)
            self.assertIn("HD2CT_API_URL", text)
            self.assertIn("HD2CT_API_KEY", text)
            self.assertIn("Bingus Shared Loader v18", text)
            self.assertIn("无需另外导入", text)
            self.assertIn("列表最底", text)
            self.assertIn("first-mod-wins", text)
            self.assertIn("列表最顶", text)
        self.assertEqual(files["LICENSES/BingusSharedLoader-README.txt"], loader_readme)
        self.assertEqual(files["LICENSES/BingusSharedLoader-manifest.json"], loader_manifest)
        source_note = files["LICENSES/BingusSharedLoader-SOURCE.txt"].decode("utf-8")
        self.assertIn("github.com/CowboyBingus/BingusSharedLoader", source_note)
        self.assertIn(builder.SHARED_LOADER_ZIP_SHA256, source_note)
        self.assertIn("不为 Bingus Shared Loader 声明或新增许可证", source_note)

        with tempfile.TemporaryDirectory() as directory:
            native_dir = Path(directory) / "native"
            native_dir.mkdir()
            dll_path = native_dir / "hd2ct_http.dll"
            meta_path = native_dir / "hd2ct_http.meta.json"
            dll_path.write_bytes(dll)
            meta_path.write_bytes(fake_metadata(dll))
            output = Path(directory) / "standalone.zip"
            with (
                mock.patch.object(builder, "STANDALONE_DLL", dll_path),
                mock.patch.object(builder, "STANDALONE_META", meta_path),
                mock.patch.object(builder, "default_output_path", return_value=output),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", Path(directory) / "receipt.json"),
                mock.patch.object(builder, "load_shared_loader_assets", return_value=loader_assets),
            ):
                built = builder.build_artifact(standalone=True)
            with zipfile.ZipFile(built) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), set(files))
                self.assertEqual(package.read("LICENSES/cJSON-LICENSE.txt"), builder.STANDALONE_LICENSE.read_bytes())

    def test_legacy_modes_leave_native_loader_disabled(self):
        source = (ROOT / "game" / "chat_probe.lua").read_bytes()
        core = (ROOT / "game" / "chat_probe_core.lua").read_bytes()
        observer = (ROOT / "game" / "chat_observe_core.lua").read_bytes()
        translate = (ROOT / "game" / "chat_translate_core.lua").read_bytes()
        packaged = builder.entry_source(source, core)
        self.assertIn(b"local STANDALONE_ENABLED = false", packaged)
        self.assertNotIn(b"__HD2_CHAT_HTTP_NATIVE_V1", packaged)
        self.assertNotIn(b"HD2CT_DLL_HEX", packaged)

        companion = builder.entry_source(
            source, core, observer, translate_source=translate, translate=True
        )
        self.assertIn(b"local TRANSLATE_ENABLED = true", companion)
        self.assertIn(b"local STANDALONE_ENABLED = false", companion)
        self.assertNotIn(b"HD2CT_DLL_HEX", companion)
        with self.assertRaisesRegex(ValueError, "互斥"):
            builder.addon_files(packaged, translate=True, standalone=True)

    def test_standalone_requires_fixed_dll_and_protects_receipt_source(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            missing_dll = temporary / "missing.dll"
            missing_meta = temporary / "missing.meta.json"
            receipt = temporary / "receipt.json"
            with (
                mock.patch.object(builder, "STANDALONE_DLL", missing_dll),
                mock.patch.object(builder, "STANDALONE_META", missing_meta),
                mock.patch.object(
                    builder,
                    "default_output_path",
                    return_value=temporary / "HD2ChatTranslate20261002120000.zip",
                ),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt),
            ):
                with self.assertRaisesRegex(ValueError, "请先构建原生 helper"):
                    builder.build_artifact(standalone=True)

            protected_zip = temporary / "HD2ChatTranslate20261002120000.zip"
            protected_zip.write_bytes(b"rollback source")
            receipt.write_text(
                json.dumps(
                    {
                        "sourceZips": {
                            "probe": {"path": str(protected_zip)},
                            "loader": {"path": str(temporary / "loader.zip")},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(builder, "STANDALONE_DLL", missing_dll),
                mock.patch.object(builder, "STANDALONE_META", missing_meta),
                mock.patch.object(builder, "default_output_path", return_value=protected_zip),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", receipt),
            ):
                with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                    builder.build_artifact(standalone=True)
            self.assertEqual(protected_zip.read_bytes(), b"rollback source")

    def test_standalone_builder_never_overwrites_its_pinned_loader_zip_input(self):
        with tempfile.TemporaryDirectory() as directory:
            loader_zip = Path(directory) / "loader.zip"
            loader_zip.write_bytes(b"pinned loader package")
            with (
                mock.patch.object(builder, "SHARED_LOADER_ZIP", loader_zip),
                mock.patch.object(builder, "DEPLOYMENT_RECEIPT", Path(directory) / "receipt.json"),
            ):
                with self.assertRaisesRegex(ValueError, "输出不能覆盖构建输入文件"):
                    builder.build_artifact(loader_zip, standalone=True)
            self.assertEqual(loader_zip.read_bytes(), b"pinned loader package")

    def test_complete_standalone_entry_passes_luajit_parser(self):
        if LUA_DLL is None:
            self.skipTest("本机未提供LuaJIT lua51.dll")
        packed = make_entry(fake_x64_dll()).decode("utf-8")
        delimiter = "[========["
        closing = "]========]"
        self.assertNotIn(closing, packed)
        script = (
            "local chunk, err = loadstring("
            + delimiter
            + packed
            + closing
            + "); assert(chunk, err); RESULT='standalone syntax ok'"
        )
        self.assertEqual(LuaJIT(LUA_DLL).run(script), "standalone syntax ok")

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

    def test_failed_temp_write_flush_or_rename_only_removes_its_own_temp(self):
        for mode in ("short_write", "flush_fail", "rename_fail"):
            with self.subTest(mode=mode):
                result = self.run_loader(mode)
                self.assertEqual(result[0], "failed")
                self.assertEqual(result[1], "0")
                self.assertEqual(result[5], "false")
                self.assertEqual(result[6], "true")
                self.assertEqual(result[8], "1")

    def test_native_cancel_busy_retries_only_bounded_owned_tokens(self):
        once = self.run_loader("cancel_busy_once")
        self.assertEqual(once[10:14], ["false", "1", "OK\ntranslated", "true"])
        capped = self.run_loader("cancel_busy_cap")
        self.assertEqual(capped[10:12], ["32", "32"])

    def test_target_gate_and_native_exports_are_fixed(self):
        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        guard = source.index("if target_verified == true and native_http_loader then")
        load = source.index("pcall(native_http_loader.load)", guard)
        self.assertGreater(load, guard)
        self.assertEqual(source.count("native_http_loader.load"), 1)

        module = (ROOT / "game" / "chat_http_native.lua").read_text(encoding="utf-8")
        names = (
            "HD2CT_ABIVersion",
            "HD2CT_InitializeEnvironment",
            "HD2CT_IsEnabled",
            "HD2CT_LastStatus",
            "HD2CT_Submit",
            "HD2CT_Poll",
            "HD2CT_Cancel",
            "HD2CT_Disable",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIn(f'resolve("{name}"', module)
        self.assertNotIn('resolve("InitializeEnvironment"', module)
        self.assertNotIn('ffi.load(', module)

        heartbeat = source.split("local function observer_translate_heartbeat(force)", 1)[1].split(
            "local function observer_translate_refresh_for_setter", 1
        )[0]
        self.assertIn("native_init_status == 0 and native_transport_api.enabled()", heartbeat)
        self.assertNotIn("native_last_status == 0", heartbeat)
        http_error = self.run_loader("http401")
        self.assertEqual(http_error[10:12], ["true", "401"])

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

    def test_old_48_byte_file_information_guard_rejects_before_win32_or_load(self):
        result = self.run_loader("old_48_struct")
        self.assertEqual(result[0], "failed")
        self.assertEqual(result[1], "0")
        self.assertEqual(result[-1], "0")


if __name__ == "__main__":
    unittest.main()
