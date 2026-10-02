"""使用仓库中固定的 MinGW 编译器构建游戏内网络 worker DLL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path(r"E:\mingw64\bin\gcc.exe")
OBJDUMP = Path(r"E:\mingw64\bin\objdump.exe")
DEFAULT_DLL = ROOT / "artifacts" / "native" / "hd2ct_http.dll"
DEFAULT_META = ROOT / "artifacts" / "native" / "hd2ct_http.meta.json"
SOURCE = ROOT / "native" / "hd2ct_http.c"
CJSON_SOURCE = ROOT / "native" / "vendor" / "cjson" / "cJSON.c"
ALLOWED_IMPORTS = {
    "ADVAPI32.DLL",
    "KERNEL32.DLL",
    "MSVCRT.DLL",
    "WINHTTP.DLL",
}
REQUIRED_EXPORTS = {
    "HD2CT_ABIVersion",
    "HD2CT_InitializeEnvironment",
    "HD2CT_InitializeConfig",
    "HD2CT_IsEnabled",
    "HD2CT_LastStatus",
    "HD2CT_Submit",
    "HD2CT_Poll",
    "HD2CT_Cancel",
    "HD2CT_Disable",
}


def run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(
            f"命令失败（退出码 {completed.returncode}）：\n"
            f"{' '.join(command)}\n{completed.stdout}"
        )
    return completed.stdout


def imported_dlls(dll: Path) -> list[str]:
    output = run([str(OBJDUMP), "-p", str(dll)])
    found = re.findall(r"(?im)^\s*DLL Name:\s*(\S+)\s*$", output)
    return sorted({name.upper() for name in found})


def exported_names(dll: Path) -> set[str]:
    output = run([str(OBJDUMP), "-p", str(dll)])
    names: set[str] = set()
    in_exports = False
    for line in output.splitlines():
        if "The Export Tables" in line:
            in_exports = True
            continue
        if in_exports and "The Import Tables" in line:
            break
        if in_exports:
            match = re.search(
                r"\]\s+\+base\[\s*\d+\]\s+[0-9a-fA-F]+\s+(\S+)\s*$",
                line,
            )
            if match:
                names.add(match.group(1))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DLL)
    parser.add_argument("--meta", type=Path, default=None)
    args = parser.parse_args()
    output = args.output.resolve()
    metadata = args.meta.resolve() if args.meta else output.with_name("hd2ct_http.meta.json")
    if not COMPILER.is_file() or not OBJDUMP.is_file():
        raise RuntimeError("固定构建工具缺失：E:\\mingw64\\bin\\gcc.exe / objdump.exe")
    if not SOURCE.is_file() or not CJSON_SOURCE.is_file():
        raise RuntimeError("原生源码或已固定版本的 cJSON 源文件缺失")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(COMPILER),
        "-std=c11",
        "-O2",
        "-shared",
        "-s",
        "-static-libgcc",
        "-finput-charset=UTF-8",
        "-fexec-charset=UTF-8",
        "-D_WIN32_WINNT=0x0601",
        "-DWINVER=0x0601",
        "-DCJSON_NESTING_LIMIT=32",
        "-DCJSON_HIDE_SYMBOLS",
        "-Wl,--exclude-all-symbols",
        str(SOURCE),
        str(CJSON_SOURCE),
        "-o",
        str(output),
        "-lwinhttp",
        "-ladvapi32",
    ]
    compile_output = run(command)
    if compile_output.strip():
        sys.stdout.write(compile_output)
    file_description = run([str(OBJDUMP), "-f", str(output)])
    if "pei-x86-64" not in file_description:
        raise RuntimeError("构建产物不是 Win64 PE DLL")
    imports = imported_dlls(output)
    unexpected = {
        name
        for name in imports
        if name not in ALLOWED_IMPORTS and not name.startswith("API-MS-WIN-CRT-")
    }
    if unexpected:
        raise RuntimeError(f"DLL 含未批准的动态依赖：{', '.join(sorted(unexpected))}")
    required_imports = {"ADVAPI32.DLL", "KERNEL32.DLL", "WINHTTP.DLL"}
    if not required_imports.issubset(imports) or not any(
        name in {"MSVCRT.DLL", "UCRTBASE.DLL"} or name.startswith("API-MS-WIN-CRT-")
        for name in imports
    ):
        raise RuntimeError("DLL 缺少预期的 WinHTTP、注册表或 Windows CRT 系统导入")
    exports = exported_names(output)
    missing = REQUIRED_EXPORTS - exports
    if missing:
        raise RuntimeError(f"DLL 缺少 ABI 导出：{', '.join(sorted(missing))}")
    compiler_version = run([str(COMPILER), "--version"]).splitlines()[0]
    binary = output.read_bytes()
    manifest = {
        "schema_version": 1,
        "abi_version": 1,
        "filename": "hd2ct_http.dll",
        "size": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "architecture": "x86_64",
        "imports": imports,
        "compiler": str(COMPILER),
        "compiler_version": compiler_version,
    }
    metadata.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
