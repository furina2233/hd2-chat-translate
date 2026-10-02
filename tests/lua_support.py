"""查找本机 LuaJIT 并在私有 LuaJIT 状态中运行 Lua 片段。"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_luajit_dll() -> Path | None:
    candidates = []
    configured = os.environ.get("HD2_LUAJIT_DLL")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(Path(folder) / "lua51.dll" for folder in os.environ.get("PATH", "").split(os.pathsep) if folder)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


LUA_DLL = _find_luajit_dll()


class LuaJIT:
    """通过 LuaJIT DLL 执行纯 Lua 核心；不连接游戏进程。"""

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
