"""FFI namespace isolation from other mods' Windows declarations."""

from __future__ import annotations

import os
import re
import unittest

from lua_support import LUA_DLL, LuaJIT, ROOT


def production_declarations() -> str:
    source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
    match = re.search(r"ffi\.cdef\[\[(.*?)\]\]", source, re.S)
    if match is None:
        raise AssertionError("production FFI declarations not found")
    return match.group(1)


class FFIAliasTests(unittest.TestCase):
    def test_windows_imports_have_private_names_and_matching_exports(self):
        declarations = production_declarations()
        functions = re.findall(
            r"^\s*[\w *]+?\b(\w+)\([^;]*?\)(?:\s+__asm__\(\"([^\"]+)\"\))?;",
            declarations,
            re.M,
        )
        self.assertEqual(len(functions), 31)
        for alias, export in functions:
            with self.subTest(export=export):
                self.assertEqual(alias, "hd2ct1_" + export)

        source = (ROOT / "game" / "chat_probe.lua").read_text(encoding="utf-8")
        bindings = re.findall(r"(\w+) = (kernel|bcrypt)_library\.(\w+),", source)
        self.assertEqual(len(bindings), len(functions))
        self.assertEqual({alias for _, _, alias in bindings}, {alias for alias, _ in functions})
        for export, library, alias in bindings:
            with self.subTest(export=export):
                self.assertEqual(alias, "hd2ct1_" + export)
                self.assertEqual(library, "bcrypt" if export.startswith("BCrypt") else "kernel")

    @unittest.skipUnless(os.name == "nt" and LUA_DLL is not None, "requires Windows LuaJIT")
    def test_incompatible_public_prototypes_cannot_change_private_calls(self):
        # Each run gets a fresh LuaJIT state. Test both declaration orders;
        # deliberately unusable public signatures must never reach the OS.
        hostile = "int VirtualQuery(int address); int ReadProcessMemory(int process);"
        for hostile_first in (True, False):
            with self.subTest(hostile_first=hostile_first):
                chunks = [hostile, production_declarations()]
                if not hostile_first:
                    chunks.reverse()
                declarations = "\n".join("ffi.cdef[[" + chunk + "]]" for chunk in chunks)
                script = "local ffi = require('ffi')\n" + declarations + r'''
local kernel = ffi.load('kernel32.dll')
local source = ffi.new('HD2Probe_U8[8]', {1, 2, 3, 4, 5, 6, 7, 8})
local region = ffi.new('HD2Probe_MEMORY_BASIC_INFORMATION[1]')
local queried = kernel.hd2ct1_VirtualQuery(source, region, ffi.sizeof(region[0]))
assert(tonumber(queried) == ffi.sizeof(region[0]), 'private VirtualQuery failed')
local output, received = ffi.new('HD2Probe_U8[8]'), ffi.new('HD2Probe_SIZE_T[1]')
local process = kernel.hd2ct1_GetCurrentProcess()
assert(kernel.hd2ct1_ReadProcessMemory(process, source, output, 8, received) ~= 0,
       'private ReadProcessMemory failed')
assert(tonumber(received[0]) == 8 and ffi.string(output, 8) == ffi.string(source, 8))
assert(tonumber(kernel.hd2ct1_GetTickCount64()) >= 0, 'private export resolution failed')
RESULT = 'private Windows calls are isolated'
'''
                self.assertEqual(LuaJIT(LUA_DLL).run(script), "private Windows calls are isolated")


if __name__ == "__main__":
    unittest.main()
