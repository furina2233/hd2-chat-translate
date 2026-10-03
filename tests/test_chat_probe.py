"""LuaJIT mock 测试：扫描核心、Lua 入口和边界行为。"""

from __future__ import annotations

import json
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT  # noqa: E402


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




if __name__ == "__main__":
    unittest.main()
