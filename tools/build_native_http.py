"""使用 MinGW-w64 GCC 构建并核验游戏内网络 worker DLL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import target_languages


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DLL = ROOT / "artifacts" / "native" / "hd2ct_http.dll"
NATIVE_DIR = ROOT / "native"
NATIVE_SOURCES = (
    NATIVE_DIR / "client.c",
    NATIVE_DIR / "common.c",
    NATIVE_DIR / "config.c",
    NATIVE_DIR / "languages.c",
    NATIVE_DIR / "adapter" / "base.c",
    NATIVE_DIR / "adapter" / "chat_completions.c",
    NATIVE_DIR / "adapter" / "google.c",
    NATIVE_DIR / "adapter" / "baidu.c",
    NATIVE_DIR / "adapter" / "youdao.c",
    NATIVE_DIR / "transport.c",
    NATIVE_DIR / "vendor" / "cjson" / "cJSON.c",
)
NATIVE_HEADERS = (
    NATIVE_DIR / "client.h",
    NATIVE_DIR / "internal.h",
    NATIVE_DIR / "vendor" / "cjson" / "cJSON.h",
)
ALLOWED_IMPORTS = {
    "ADVAPI32.DLL",
    "BCRYPT.DLL",
    "KERNEL32.DLL",
    "MSVCRT.DLL",
    "UCRTBASE.DLL",
    "WINHTTP.DLL",
}
REQUIRED_EXPORTS = {
    "HD2CT_Submit",
    "HD2CT_Poll",
    "HD2CT_Cancel",
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


def resolve_tool(value: str) -> Path:
    """接受显式工具路径或 PATH 中的命令名。"""
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    located = shutil.which(value)
    if located is None:
        raise RuntimeError(f"找不到构建工具：{value}；请配置 PATH 或使用 --cc / --objdump 指定路径")
    return Path(located).resolve()


def resolve_objdump(compiler: Path, explicit: str | None) -> Path:
    """优先使用与 GCC 同目录、同前缀的 Binutils 工具。"""
    if explicit is not None:
        return resolve_tool(explicit)
    sibling = compiler.with_name(compiler.name.replace("gcc", "objdump"))
    if sibling != compiler and sibling.is_file():
        return sibling.resolve()
    return resolve_tool("objdump")


def imported_dlls(dll: Path, objdump: Path) -> list[str]:
    output = run([str(objdump), "-p", str(dll)])
    found = re.findall(r"(?im)^\s*DLL Name:\s*(\S+)\s*$", output)
    return sorted({name.upper() for name in found})


def exported_names(dll: Path, objdump: Path) -> set[str]:
    output = run([str(objdump), "-p", str(dll)])
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
    parser.add_argument("--cc", default="gcc", help="MinGW-w64 GCC 命令名或完整路径，默认从 PATH 查找 gcc")
    parser.add_argument("--objdump", help="objdump 命令名或完整路径，默认使用 GCC 同目录的工具")
    parser.add_argument("--output", type=Path, default=DEFAULT_DLL)
    parser.add_argument("--meta", type=Path, default=None)
    args = parser.parse_args()
    output = args.output.resolve()
    metadata = args.meta.resolve() if args.meta else output.with_name("hd2ct_http.meta.json")
    compiler = resolve_tool(args.cc)
    objdump = resolve_objdump(compiler, args.objdump)
    try:
        catalogue = target_languages.load_catalogue()
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_header = target_languages.generate_c_header(output.parent, catalogue)
    missing_sources = [path for path in NATIVE_SOURCES if not path.is_file()]
    missing_headers = [path for path in (*NATIVE_HEADERS, generated_header) if not path.is_file()]
    if missing_sources or missing_headers:
        missing = [*missing_sources, *missing_headers]
        raise RuntimeError(
            "原生源码或头文件缺失：" + ", ".join(str(path.relative_to(ROOT)) for path in missing)
        )
    metadata.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(compiler),
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
        "-I",
        str(NATIVE_DIR),
        "-I",
        str(output.parent),
        *(str(source) for source in NATIVE_SOURCES),
        "-o",
        str(output),
        "-lwinhttp",
        "-ladvapi32",
        "-lbcrypt",
    ]
    compile_output = run(command)
    if compile_output.strip():
        sys.stdout.write(compile_output)
    file_description = run([str(objdump), "-f", str(output)])
    if "pei-x86-64" not in file_description:
        raise RuntimeError("构建产物不是 Win64 PE DLL")
    imports = imported_dlls(output, objdump)
    unexpected = {
        name
        for name in imports
        if name not in ALLOWED_IMPORTS and not name.startswith("API-MS-WIN-CRT-")
    }
    if unexpected:
        raise RuntimeError(f"DLL 含未批准的动态依赖：{', '.join(sorted(unexpected))}")
    required_imports = {"ADVAPI32.DLL", "BCRYPT.DLL", "KERNEL32.DLL", "WINHTTP.DLL"}
    if not required_imports.issubset(imports) or not any(
        name in {"MSVCRT.DLL", "UCRTBASE.DLL"} or name.startswith("API-MS-WIN-CRT-")
        for name in imports
    ):
        raise RuntimeError("DLL 缺少预期的 WinHTTP、注册表、BCrypt 或 Windows CRT 系统导入")
    exports = exported_names(output, objdump)
    missing = REQUIRED_EXPORTS - exports
    unexpected_exports = exports - REQUIRED_EXPORTS
    if missing or unexpected_exports or len(exports) != len(REQUIRED_EXPORTS):
        details = []
        if missing:
            details.append(f"缺少：{', '.join(sorted(missing))}")
        if unexpected_exports:
            details.append(f"多出：{', '.join(sorted(unexpected_exports))}")
        raise RuntimeError("DLL 导出必须恰好保留 3 个 ABI 名称；" + "；".join(details))
    compiler_version = run([str(compiler), "--version"]).splitlines()[0]
    binary = output.read_bytes()
    manifest = {
        "schema_version": 1,
        "abi_version": 2,
        "filename": "hd2ct_http.dll",
        "size": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "target_languages_sha256": target_languages.catalogue_sha256(catalogue),
        "architecture": "x86_64",
        "imports": imports,
        "compiler": str(compiler),
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
