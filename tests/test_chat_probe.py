"""只读研究探针的离线打包、manifest 与 LuaJIT mock 测试。"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_chat_probe as analyzer  # noqa: E402
import build_chat_probe as builder  # noqa: E402


LUA_DLL_CANDIDATES = [
    Path(os.environ.get("HD2_LUAJIT_DLL", "")),
    Path(r"E:\SteamLibrary\steamapps\common\Helldivers 2\bin\lua51.dll"),
]
LUA_DLL = next((path for path in LUA_DLL_CANDIDATES if str(path) and path.is_file()), None)


def valid_manifest() -> dict:
    raw = bytes((index % 251 for index in range(512)))
    rva = analyzer.SECTION_RVA + 0x1000
    return {
        "schema_version": 1,
        "status": "scan_complete",
        "detail": None,
        "source_build": {
            "module": "game.dll",
            "game_dll_sha256_expected": analyzer.EXPECTED_SHA256,
            "game_dll_sha256_observed": analyzer.EXPECTED_SHA256,
            "game_dll_size_expected": analyzer.EXPECTED_DISK_SIZE,
            "game_dll_size_observed": analyzer.EXPECTED_DISK_SIZE,
            "pe_timestamp_expected": 1790161983,
            "pe_timestamp_observed": 1790161983,
            "size_of_image_expected": 74727424,
            "size_of_image_observed": 74727424,
            "code_section_rva": analyzer.SECTION_RVA,
            "code_section_size": analyzer.SECTION_SIZE,
            "code_section_flags": 0x60000020,
        },
        "scan": {
            "section_rva": analyzer.SECTION_RVA,
            "section_size": analyzer.SECTION_SIZE,
            "max_code_read_bytes_per_step": 16384,
            "disk_hash_and_pe_header_read_outside_code_budget": True,
            "scanned_bytes": 512,
            "skipped_bytes": 0,
            "skipped_regions": 0,
            "read_failures": 0,
            "pattern_matches": {
                "history_signature": 0,
                "imm_le32_9590": 0,
                "imm_le32_9594": 0,
                "imm_le32_c418": 0,
            },
            "truncation": {
                "patterns": {
                    "history_signature": 0,
                    "imm_le32_9590": 0,
                    "imm_le32_9594": 0,
                    "imm_le32_c418": 0,
                },
                "total": 0,
            },
            "unreadable_candidates": 0,
            "candidate_bytes": len(raw),
        },
        "known_signatures": [
            {
                "label": "sample",
                "rva": rva,
                "comparison": "true",
                "expected_hex": "aabb",
                "observed_hex": "aabb",
            }
        ],
        "candidates": [
            {
                "rva": rva,
                "window_rva": rva - 128,
                "page_protection": 0x20,
                "page_protections": [0x20],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes_hex": raw.hex(),
                "byte_length": len(raw),
                "reason": "candidate only",
            }
        ],
        "function_verification": "none; RVAs and byte matches are research candidates only",
    }


class LuaJIT:
    """通过仓库旁的 LuaJIT DLL 执行纯 Lua 核心；不启动或连接游戏。"""

    def __init__(self, dll_path: Path):
        self.dll = ctypes.CDLL(str(dll_path))
        self.dll.luaL_newstate.restype = ctypes.c_void_p
        self.dll.luaL_openlibs.argtypes = [ctypes.c_void_p]
        self.dll.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.dll.luaL_loadstring.restype = ctypes.c_int
        self.dll.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.lua_pcall.restype = ctypes.c_int
        self.dll.lua_getfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        self.dll.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
        self.dll.lua_tolstring.restype = ctypes.c_void_p
        self.dll.lua_close.argtypes = [ctypes.c_void_p]

    def run(self, script: str, global_name: str = "RESULT") -> str:
        state = self.dll.luaL_newstate()
        if not state:
            raise RuntimeError("luaL_newstate failed")
        try:
            self.dll.luaL_openlibs(state)
            status = self.dll.luaL_loadstring(state, script.encode("utf-8"))
            if status != 0:
                raise RuntimeError(self._stack_text(state))
            status = self.dll.lua_pcall(state, 0, 0, 0)
            if status != 0:
                raise RuntimeError(self._stack_text(state))
            self.dll.lua_getfield(state, -10002, global_name.encode("ascii"))
            length = ctypes.c_size_t()
            pointer = self.dll.lua_tolstring(state, -1, ctypes.byref(length))
            if not pointer:
                raise RuntimeError(f"Lua global {global_name} is not a string")
            return ctypes.string_at(pointer, length.value).decode("utf-8")
        finally:
            self.dll.lua_close(state)

    def _stack_text(self, state: int) -> str:
        length = ctypes.c_size_t()
        pointer = self.dll.lua_tolstring(state, -1, ctypes.byref(length))
        return ctypes.string_at(pointer, length.value).decode("utf-8", "replace") if pointer else "LuaJIT error"


LUA_CORE_HARNESS = r'''
local core = dofile([[CORE_PATH]])
local function put16(bytes, offset, value)
    bytes[offset + 1] = value % 256
    bytes[offset + 2] = math.floor(value / 256) % 256
end
local function put32(bytes, offset, value)
    for index = 0, 3 do bytes[offset + index + 1] = math.floor(value / (256 ^ index)) % 256 end
end
local function put_text(bytes, offset, text)
    for index = 1, #text do bytes[offset + index] = text:byte(index) end
end
local function make_header()
    local bytes = {}
    for index = 1, 4096 do bytes[index] = 0 end
    put_text(bytes, 0, "MZ")
    put32(bytes, 0x3c, 0x80)
    put_text(bytes, 0x80, string.char(80, 69, 0, 0))
    local file = 0x84
    put16(bytes, file, 0x8664)
    put16(bytes, file + 2, 2)
    put32(bytes, file + 4, 1790161983)
    put16(bytes, file + 16, 240)
    local optional = file + 20
    put16(bytes, optional, 0x20b)
    put32(bytes, optional + 56, 74727424)
    put32(bytes, optional + 60, 4096)
    local sections = optional + 240
    put_text(bytes, sections, string.rep(" ", 8))
    put32(bytes, sections + 8, 34667155)
    put32(bytes, sections + 12, 4096)
    put32(bytes, sections + 36, 0x60000020)
    put_text(bytes, sections + 40, ".data" .. string.rep("\0", 3))
    put32(bytes, sections + 48, 4096)
    put32(bytes, sections + 52, 0x200000)
    put32(bytes, sections + 76, 0x40000040)
    local chunks = {}
    for start = 1, 4096, 512 do
        local part = {}
        for index = start, start + 511 do part[#part + 1] = string.char(bytes[index]) end
        chunks[#chunks + 1] = table.concat(part)
    end
    return table.concat(chunks)
end
local function from_hex(value)
    return (value:gsub("..", function(pair) return string.char(tonumber(pair, 16)) end))
end
local function same(a, b)
    return a and b and a.start_rva == b.start_rva and a.size == b.size
        and a.allocation_base == b.allocation_base and a.type == b.type
        and a.state == b.state and a.protect == b.protect
end
local function run_case(config)
    local section_start = core.SECTION.rva
    local section_end = section_start + core.SECTION.size
    local mapped_length = config.mapped_length or 65536
    local mapped_end = section_start + mapped_length
    local header = make_header()
    local code
    if config.mode == "dense" then
        local pattern_groups = {
            "94950000",
            "90950000",
            "18c40000",
            "8b87949500008b8f90950000",
        }
        local dense_parts = {}
        for _, hex in ipairs(pattern_groups) do
            for _ = 1, 32 do dense_parts[#dense_parts + 1] = from_hex(hex) .. string.rep(string.char(0), 16 - #hex / 2) end
        end
        local dense_code = table.concat(dense_parts)
        code = dense_code .. string.rep(string.char(0), mapped_length - #dense_code)
    else
        code = string.rep(string.char(0), mapped_length)
    end
    if config.signature_offset then
        local signature = from_hex("8b87949500008b8f90950000")
        local offset = config.signature_offset
        code = code:sub(1, offset) .. signature .. code:sub(offset + #signature + 1)
    end
    local query_count = 0
    local read_count = 0
    local requested_code_bytes = 0
    local section_query_count = 0
    local function query(rva)
        query_count = query_count + 1
        if rva < section_start then
            return {start_rva = 0, size = section_start, allocation_base = true,
                type = 0x1000000, state = 0x1000, protect = 0x02}
        end
        if config.mode == "zero" and rva == section_start then
            return {start_rva = section_start, size = 0, allocation_base = true,
                type = 0x1000000, state = 0x1000, protect = 0x20}
        end
        if rva >= section_start and rva < mapped_end then
            local protect, region_type, region_state = 0x20, 0x1000000, 0x1000
            if config.mode == "nonimage" then region_type = 0x20000 end
            if config.mode == "nonexec" then protect = 0x02 end
            if config.mode == "guard" then protect = 0x120 end
            if config.mode == "permissionswitch" and rva == section_start then
                section_query_count = section_query_count + 1
                if section_query_count == 2 then protect = 0x40 end
            end
            return {start_rva = section_start, size = mapped_length, allocation_base = true,
                type = region_type, state = region_state, protect = protect}
        end
        if rva >= mapped_end and rva < section_end then
            return {start_rva = mapped_end, size = section_end - mapped_end,
                allocation_base = true, type = 0x1000000, state = 0x2000, protect = 0}
        end
        return nil
    end
    local adapter = {
        hash_file = function()
            if config.mode == "hashmismatch" then return string.rep("0", 64), core.SOURCE.disk_size end
            if config.mode == "sizemismatch" then return core.SOURCE.sha256, core.SOURCE.disk_size - 1 end
            return core.SOURCE.sha256, core.SOURCE.disk_size
        end,
        query = query,
        read = function(rva, length, executable)
            read_count = read_count + 1
            if rva >= section_start then requested_code_bytes = requested_code_bytes + length end
            if config.mode == "shortread" and rva == section_start then
                return string.rep(string.char(0), math.max(0, length - 1))
            end
            if rva + length > core.SOURCE.size_of_image then return nil end
            local output = {}
            local cursor = rva
            while cursor < rva + length do
                local region = query(cursor)
                local allowed = region and region.allocation_base and region.type == 0x1000000
                    and region.state == 0x1000
                    and (region.protect == 0x02 or region.protect == 0x04 or region.protect == 0x08
                        or region.protect == 0x20 or region.protect == 0x40 or region.protect == 0x80)
                    and (not executable or region.protect == 0x20 or region.protect == 0x40 or region.protect == 0x80)
                if not allowed then return nil end
                local region_end = region.start_rva + region.size
                if region.start_rva > cursor or region_end <= cursor then return nil end
                local chunk_end = math.min(rva + length, region_end, math.floor(cursor / 4096 + 1) * 4096)
                local rechecked = query(cursor)
                if not same(region, rechecked) then return nil end
                if cursor < section_start then
                    local header_end = math.min(chunk_end, section_start)
                    output[#output + 1] = header:sub(cursor + 1, header_end)
                else
                    local code_end = math.min(chunk_end, mapped_end)
                    if code_end <= cursor then return nil end
                    output[#output + 1] = code:sub(cursor - section_start + 1, code_end - section_start)
                end
                cursor = chunk_end
            end
            local data = table.concat(output)
            if #data ~= length then return nil end
            return data
        end,
        hash_bytes = function() return string.rep("a", 64) end,
    }
    local state = core.new(adapter)
    local done, manifest = false, nil
    local max_frame_code_request = 0
    for _ = 1, 256 do
        local before = requested_code_bytes
        done, manifest = core.step(state)
        max_frame_code_request = math.max(max_frame_code_request, requested_code_bytes - before)
        if done then break end
    end
    if not done then error("mock scan exceeded frame cap: " .. config.name) end
    local pattern_slots = 0
    for _ in pairs(state.pattern_last_seen) do pattern_slots = pattern_slots + 1 end
    return {manifest = manifest, query_calls = query_count, read_calls = read_count,
        pattern_slots = pattern_slots, max_frame_code_request = max_frame_code_request}
end
local cases = {
    {name = "cross_block", mapped_length = 65536, signature_offset = 16384 - 6},
    {name = "shortread", mode = "shortread", mapped_length = 8192, signature_offset = 4096 - 6},
    {name = "hole", mode = "hole", mapped_length = 4096, signature_offset = 4096 - 6},
    {name = "permissionswitch", mode = "permissionswitch", mapped_length = 32768, signature_offset = 4096 - 6},
    {name = "nonimage", mode = "nonimage", mapped_length = 4096},
    {name = "nonexec", mode = "nonexec", mapped_length = 4096},
    {name = "guard", mode = "guard", mapped_length = 4096},
    {name = "zero", mode = "zero", mapped_length = 4096},
    {name = "dense", mode = "dense", mapped_length = 32768},
    {name = "hashmismatch", mode = "hashmismatch", mapped_length = 4096},
    {name = "sizemismatch", mode = "sizemismatch", mapped_length = 4096},
}
local results = {}
for _, config in ipairs(cases) do results[config.name] = run_case(config) end
RESULT = core.encode_json(results)
'''


class PackageAndAnalyzerTests(unittest.TestCase):
    def test_builder_archive_header_hash_and_payload_marker(self):
        entry = builder.entry_source(
            (ROOT / "game" / "chat_probe.lua").read_bytes(),
            (ROOT / "game" / "chat_probe_core.lua").read_bytes(),
        )
        self.assertEqual(builder.resource_hash(builder.RESOURCE_NAME), 0xC509C11199F753C2)
        files = builder.addon_files(entry)
        self.assertEqual(
            set(files),
            {
                "manifest.json",
                "Addon/9ba626afa44a3aa3.patch_0",
                "Addon/9ba626afa44a3aa3.patch_0.stream",
                "Addon/9ba626afa44a3aa3.patch_0.gpu_resources",
            },
        )
        archive = files["Addon/9ba626afa44a3aa3.patch_0"]
        magic, version, count = struct.unpack_from("<III", archive, 0)
        self.assertEqual((magic, version, count), (0xF0000011, 1, 1))
        resource_type = struct.unpack_from("<IIQIIII", archive, 72)[2]
        self.assertEqual(resource_type, builder.RESOURCE_TYPE)
        record = struct.unpack_from("<7Q6I", archive, 104)
        self.assertEqual(record[0], builder.resource_hash(builder.RESOURCE_NAME))
        self.assertEqual(record[1], builder.RESOURCE_TYPE)
        payload = archive[record[2] : record[2] + record[7]]
        declared_size, resource_version = struct.unpack_from("<II", payload)
        source = payload[8:]
        self.assertEqual((declared_size, resource_version), (len(source), 2))
        self.assertTrue(source.startswith(b"-- HD2-Addon: mods/hd2chat/chat_probe\n"))
        self.assertIn(b"--[[HD2_CHAT_PROBE_CORE]]", (ROOT / "game" / "chat_probe.lua").read_bytes())
        self.assertNotIn(b"--[[HD2_CHAT_PROBE_CORE]]", source)
        self.assertIn(b"function_verification", source)
        self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.stream"], b"")
        self.assertEqual(files["Addon/9ba626afa44a3aa3.patch_0.gpu_resources"], b"")

    def test_builder_zip_members(self):
        with tempfile.TemporaryDirectory() as directory:
            output = builder.build_artifact(Path(directory) / "probe.zip")
            with zipfile.ZipFile(output) as package:
                self.assertEqual(
                    set(package.namelist()),
                    {
                        "Addon/9ba626afa44a3aa3.patch_0",
                        "Addon/9ba626afa44a3aa3.patch_0.gpu_resources",
                        "Addon/9ba626afa44a3aa3.patch_0.stream",
                        "manifest.json",
                    },
                )
                manifest = json.loads(package.read("manifest.json"))
                self.assertIn("只读研究探针", manifest["Description"])
                self.assertIn("不连接聊天", manifest["Description"])

    def test_analyzer_accepts_digest_verified_manifest_and_exports_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(json.dumps(valid_manifest()), encoding="utf-8")
            manifest = analyzer.load_manifest(manifest_path)
            exported = analyzer.export_candidates(manifest, Path(directory) / "out")
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0].read_bytes(), bytes.fromhex(manifest["candidates"][0]["bytes_hex"]))
        self.assertIn("research candidate", analyzer.summarize(manifest))

    def test_analyzer_rejects_malicious_or_inconsistent_manifest(self):
        mutations = []
        value = valid_manifest()
        value["candidates"][0]["rva"] = analyzer.SECTION_END
        mutations.append(value)
        value = valid_manifest()
        value["candidates"][0]["window_rva"] = analyzer.SECTION_END - 100
        mutations.append(value)
        value = valid_manifest()
        value["candidates"][0]["sha256"] = "0" * 64
        mutations.append(value)
        value = valid_manifest()
        value["candidates"][0]["byte_length"] = 513
        mutations.append(value)
        value = valid_manifest()
        value["scan"]["candidate_bytes"] -= 1
        mutations.append(value)
        value = valid_manifest()
        value["source_build"]["pe_timestamp_observed"] = 1
        mutations.append(value)
        value = valid_manifest()
        value["candidates"][0]["page_protection"] = 0x120
        mutations.append(value)
        value = valid_manifest()
        value["known_signatures"][0]["comparison"] = "false"
        mutations.append(value)
        for malformed in mutations:
            with self.subTest(malformed=malformed):
                with self.assertRaises(analyzer.ManifestError):
                    analyzer.validate_manifest(malformed)

    def test_analyzer_rejects_too_many_candidates_and_oversized_file(self):
        manifest = valid_manifest()
        manifest["candidates"] *= 129
        manifest["scan"]["candidate_bytes"] = 512 * len(manifest["candidates"])
        with self.assertRaises(analyzer.ManifestError):
            analyzer.validate_manifest(manifest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b" " * (analyzer.MAX_MANIFEST_BYTES + 1))
            with self.assertRaises(analyzer.ManifestError):
                analyzer.load_manifest(path)


@unittest.skipUnless(LUA_DLL is not None, "本机未提供 LuaJIT lua51.dll")
class LuaCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = LuaJIT(LUA_DLL)
        cls.core_path = (ROOT / "game" / "chat_probe_core.lua").as_posix()

    def test_mock_scans_bounds_gaps_permissions_hash_and_caps(self):
        script = LUA_CORE_HARNESS.replace("CORE_PATH", self.core_path)
        result = json.loads(self.lua.run(script))

        cross = result["cross_block"]["manifest"]
        self.assertEqual(cross["status"], "scan_complete")
        self.assertEqual(cross["scan"]["pattern_matches"]["history_signature"], 1)
        self.assertGreaterEqual(len(cross["candidates"]), 1)
        self.assertLessEqual(result["cross_block"]["max_frame_code_request"], 16 * 1024)
        self.assertLessEqual(result["dense"]["max_frame_code_request"], 16 * 1024)

        for name in ("shortread", "hole", "permissionswitch"):
            with self.subTest(name=name):
                manifest = result[name]["manifest"]
                self.assertEqual(manifest["status"], "scan_complete")
                self.assertEqual(manifest["scan"]["pattern_matches"]["history_signature"], 0)
                self.assertGreater(manifest["scan"]["skipped_bytes"], 0)
        self.assertGreater(result["shortread"]["manifest"]["scan"]["read_failures"], 0)
        self.assertGreater(result["permissionswitch"]["manifest"]["scan"]["read_failures"], 0)

        for name in ("nonimage", "nonexec", "guard", "zero"):
            with self.subTest(name=name):
                manifest = result[name]["manifest"]
                self.assertEqual(manifest["status"], "scan_complete")
                self.assertEqual(manifest["scan"]["scanned_bytes"], 0)
                self.assertGreater(manifest["scan"]["skipped_regions"], 0)

        dense = result["dense"]["manifest"]
        self.assertLessEqual(len(dense["candidates"]), 128)
        self.assertGreater(dense["scan"]["truncation"]["total"], 0)
        self.assertEqual(dense["scan"]["pattern_matches"]["imm_le32_c418"], 32)
        self.assertGreater(dense["scan"]["truncation"]["patterns"]["imm_le32_9590"], 0)
        self.assertLessEqual(dense["scan"]["candidate_bytes"], 128 * 1024)
        self.assertEqual(result["dense"]["pattern_slots"], 4)

        mismatch = result["hashmismatch"]
        self.assertEqual(mismatch["manifest"]["status"], "hash_mismatch")
        self.assertEqual(mismatch["read_calls"], 0)
        self.assertEqual(mismatch["manifest"]["candidates"], [])
        self.assertEqual(mismatch["manifest"]["known_signatures"], [])
        wrong_size = result["sizemismatch"]
        self.assertEqual(wrong_size["manifest"]["status"], "disk_size_mismatch")
        self.assertEqual(wrong_size["read_calls"], 0)

    def test_update_wrapper_spans_frames_and_isolates_probe_errors(self):
        script = f'''
            local core = dofile([[{self.core_path}]])
            local probe_calls, update_calls = 0, 0
            local wrapped = core.wrap_update(function() update_calls = update_calls + 1 end, function()
                probe_calls = probe_calls + 1
                return probe_calls == 3
            end)
            for _ = 1, 4 do wrapped() end
            local error_calls, original_calls = 0, 0
            local error_wrapped = core.wrap_update(function() original_calls = original_calls + 1 end, function()
                error_calls = error_calls + 1
                error("mock probe failure")
            end)
            error_wrapped()
            error_wrapped()
            RESULT = string.format("%d,%d,%d,%d", probe_calls, update_calls, error_calls, original_calls)
        '''
        self.assertEqual(self.lua.run(script), "3,4,1,2")

    def test_entry_lua_syntax_and_ffi_pointer_and_bcrypt_sha256(self):
        packed = builder.entry_source(
            (ROOT / "game" / "chat_probe.lua").read_bytes(),
            (ROOT / "game" / "chat_probe_core.lua").read_bytes(),
        ).decode("utf-8")
        delimiter = "[========["
        closing = "]========]"
        self.assertNotIn(closing, packed)
        syntax = "local chunk, err = loadstring(" + delimiter + packed + closing + "); assert(chunk, err); RESULT='syntax ok'"
        self.assertEqual(self.lua.run(syntax), "syntax ok")

        smoke = r'''
            local ffi = require("ffi")
            local buffer = ffi.new("uint8_t[4]")
            buffer[0], buffer[1], buffer[2], buffer[3] = 1, 2, 3, 4
            local destination = buffer + 2
            assert(destination[0] == 3)
            ffi.cdef[[
                typedef void *Probe_BCRYPT_HANDLE;
                typedef int Probe_STATUS;
                Probe_STATUS BCryptOpenAlgorithmProvider(Probe_BCRYPT_HANDLE *algorithm, const unsigned short *name, const unsigned short *implementation, unsigned int flags);
                Probe_STATUS BCryptCreateHash(Probe_BCRYPT_HANDLE algorithm, Probe_BCRYPT_HANDLE *hash, unsigned char *object_buffer, unsigned int object_size, unsigned char *secret, unsigned int secret_size, unsigned int flags);
                Probe_STATUS BCryptHashData(Probe_BCRYPT_HANDLE hash, unsigned char *data, unsigned int length, unsigned int flags);
                Probe_STATUS BCryptFinishHash(Probe_BCRYPT_HANDLE hash, unsigned char *digest, unsigned int digest_size, unsigned int flags);
                Probe_STATUS BCryptDestroyHash(Probe_BCRYPT_HANDLE hash);
                Probe_STATUS BCryptCloseAlgorithmProvider(Probe_BCRYPT_HANDLE algorithm, unsigned int flags);
            ]]
            local bcrypt = ffi.load("bcrypt.dll")
            local name = ffi.new("unsigned short[7]", 83, 72, 65, 50, 53, 54, 0)
            local algorithm = ffi.new("Probe_BCRYPT_HANDLE[1]")
            assert(bcrypt.BCryptOpenAlgorithmProvider(algorithm, name, nil, 0) == 0)
            local hash = ffi.new("Probe_BCRYPT_HANDLE[1]")
            assert(bcrypt.BCryptCreateHash(algorithm[0], hash, nil, 0, nil, 0, 0) == 0)
            local abc = ffi.new("unsigned char[3]", 97, 98, 99)
            assert(bcrypt.BCryptHashData(hash[0], abc, 3, 0) == 0)
            local digest = ffi.new("unsigned char[32]")
            assert(bcrypt.BCryptFinishHash(hash[0], digest, 32, 0) == 0)
            local hex = {}
            for index = 0, 31 do hex[#hex + 1] = string.format("%02x", digest[index]) end
            assert(table.concat(hex) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            bcrypt.BCryptDestroyHash(hash[0])
            bcrypt.BCryptCloseAlgorithmProvider(algorithm[0], 0)
            RESULT = "ffi and bcrypt ok"
        '''
        self.assertEqual(self.lua.run(smoke), "ffi and bcrypt ok")


if __name__ == "__main__":
    unittest.main()
