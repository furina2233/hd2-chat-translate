"""为 CMake 原生目标生成头文件、源码清单并核验 DLL metadata。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import build_native_http as native
from scripts import free_service_config
from scripts import target_languages


def _cmake_bracket_argument(value: str) -> str:
    """生成不会展开反斜线或变量的 CMake 方括号参数。"""
    equals = ""
    while f"]{equals}]" in value:
        equals += "="
    return f"[{equals}[{value}]{equals}]"


def write_source_manifest(destination: Path) -> None:
    """从既有构建器清单生成供 CMake include 的源码和头文件列表。"""
    missing = [path for path in (*native.NATIVE_SOURCES, *native.NATIVE_HEADERS) if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(native.ROOT)) for path in missing)
        raise RuntimeError(f"原生源码或头文件缺失：{names}")

    lines = ["set(HD2CT_NATIVE_SOURCES"]
    lines.extend(f"    {_cmake_bracket_argument(path.resolve().as_posix())}" for path in native.NATIVE_SOURCES)
    lines.extend([")", "set(HD2CT_NATIVE_HEADERS"])
    lines.extend(f"    {_cmake_bracket_argument(path.resolve().as_posix())}" for path in native.NATIVE_HEADERS)
    lines.append(")")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def generate_header(catalogue_path: Path, output_dir: Path) -> None:
    """校验共享目标语言目录并生成原生 C 头文件。"""
    try:
        catalogue = target_languages.load_catalogue(catalogue_path)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    target_languages.generate_c_header(output_dir, catalogue)


def generate_free_header(config_path: Path, output_dir: Path) -> None:
    """校验免费服务私有配置并生成认证头文件。"""
    try:
        free_service_config.generate_header(config_path, output_dir)
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def remove_metadata(metadata_path: Path) -> None:
    """删除上一次构建留下的 metadata。"""
    try:
        metadata_path.unlink()
    except FileNotFoundError:
        pass


def write_metadata(dll_path: Path, metadata_path: Path, compiler_path: Path,
                   objdump_value: str,
                   free_config_path: Path = free_service_config.DEFAULT_CONFIG,
                   generated_free_header: Path | None = None) -> dict[str, object]:
    """复用原生构建器的 PE 检查并原子写入打包器使用的 metadata。"""
    remove_metadata(metadata_path)
    if dll_path.name != "hd2ct_http.dll":
        raise RuntimeError("CMake 原生 DLL 文件名必须是 hd2ct_http.dll")
    if not dll_path.is_file():
        raise RuntimeError(f"原生 DLL 不存在：{dll_path}")

    compiler = native.resolve_tool(str(compiler_path))
    objdump = native.resolve_tool(objdump_value)
    description = native.run([str(objdump), "-f", str(dll_path)])
    if "pei-x86-64" not in description:
        raise RuntimeError("构建产物不是 Win64 PE DLL")

    imports = native.imported_dlls(dll_path, objdump)
    unexpected = {
        name for name in imports
        if name not in native.ALLOWED_IMPORTS and not name.startswith("API-MS-WIN-CRT-")
    }
    if unexpected:
        raise RuntimeError(f"DLL 含未批准的动态依赖：{', '.join(sorted(unexpected))}")
    required_imports = {"ADVAPI32.DLL", "BCRYPT.DLL", "KERNEL32.DLL", "WINHTTP.DLL"}
    has_crt = any(
        name in {"MSVCRT.DLL", "UCRTBASE.DLL"} or name.startswith("API-MS-WIN-CRT-")
        for name in imports
    )
    if not required_imports.issubset(imports) or not has_crt:
        raise RuntimeError("DLL 缺少预期的 WinHTTP、注册表、BCrypt 或 Windows CRT 系统导入")

    exports = native.exported_names(dll_path, objdump)
    missing = native.REQUIRED_EXPORTS - exports
    unexpected_exports = exports - native.REQUIRED_EXPORTS
    if missing or unexpected_exports or len(exports) != len(native.REQUIRED_EXPORTS):
        details = []
        if missing:
            details.append(f"缺少：{', '.join(sorted(missing))}")
        if unexpected_exports:
            details.append(f"多出：{', '.join(sorted(unexpected_exports))}")
        raise RuntimeError("DLL 导出必须恰好保留 3 个 ABI 名称；" + "；".join(details))

    try:
        catalogue = target_languages.load_catalogue()
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    try:
        _, free_service_digest = free_service_config.load_config(free_config_path)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    if generated_free_header is not None:
        try:
            header_text = generated_free_header.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(f"原生免费服务配置头文件不存在：{generated_free_header}") from error
        match = re.search(
            r'^#define HD2CT_FREE_CONFIG_SHA256 "([0-9a-f]{64})"$',
            header_text,
            re.MULTILINE,
        )
        if match is None or match.group(1) != free_service_digest:
            raise RuntimeError("原生 DLL 使用的免费服务配置与当前私有JSON不一致")
    binary = dll_path.read_bytes()
    if not binary:
        raise RuntimeError("构建产物是空 DLL")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "abi_version": 4,
        "filename": "hd2ct_http.dll",
        "size": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "target_languages_sha256": target_languages.catalogue_sha256(catalogue),
        "free_service_config_sha256": free_service_digest,
        "architecture": "x86_64",
        "imports": imports,
        "compiler": str(compiler),
        "compiler_version": native.run([str(compiler), "--version"]).splitlines()[0],
    }

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = metadata_path.with_name(metadata_path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, metadata_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    manifest_parser = commands.add_parser("write-source-manifest")
    manifest_parser.add_argument("--output", type=Path, required=True)

    header_parser = commands.add_parser("generate-header")
    header_parser.add_argument("--catalogue", type=Path, required=True)
    header_parser.add_argument("--output-dir", type=Path, required=True)

    free_header_parser = commands.add_parser("generate-free-header")
    free_header_parser.add_argument("--config", type=Path,
                                    default=free_service_config.DEFAULT_CONFIG)
    free_header_parser.add_argument("--output-dir", type=Path, required=True)

    remove_parser = commands.add_parser("remove-meta")
    remove_parser.add_argument("--metadata", type=Path, required=True)

    meta_parser = commands.add_parser("write-meta")
    meta_parser.add_argument("--dll", type=Path, required=True)
    meta_parser.add_argument("--metadata", type=Path, required=True)
    meta_parser.add_argument("--compiler", type=Path, required=True)
    meta_parser.add_argument("--objdump", required=True)
    meta_parser.add_argument("--free-config", type=Path,
                             default=free_service_config.DEFAULT_CONFIG)
    meta_parser.add_argument("--generated-free-header", type=Path)

    args = parser.parse_args()
    if args.command == "write-source-manifest":
        write_source_manifest(args.output)
    elif args.command == "generate-header":
        generate_header(args.catalogue, args.output_dir)
    elif args.command == "generate-free-header":
        generate_free_header(args.config, args.output_dir)
    elif args.command == "remove-meta":
        remove_metadata(args.metadata)
    else:
        print(json.dumps(
            write_metadata(args.dll, args.metadata, args.compiler, args.objdump,
                           args.free_config, args.generated_free_header),
            ensure_ascii=False,
            indent=2,
        ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
