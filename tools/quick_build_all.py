"""依次构建原生DLL和两种standalone Arsenal安装包。"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE_BUILD_SCRIPT = (ROOT / "tools" / "build_native_http.py").resolve()
PACKAGE_BUILD_SCRIPT = (ROOT / "tools" / "build_package.py").resolve()
DEFAULT_DLL = (ROOT / "artifacts" / "native" / "hd2ct_http.dll").resolve()
DEFAULT_META = (ROOT / "artifacts" / "native" / "hd2ct_http.meta.json").resolve()


def _resolved_input_path(value: str, invocation_directory: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = invocation_directory / path
    return str(path.resolve())


def _tool_argument(value: str, invocation_directory: Path) -> str:
    path = Path(value).expanduser()
    candidate = path if path.is_absolute() else invocation_directory / path
    if candidate.exists() or path.is_absolute() or len(path.parts) > 1 or value.startswith("."):
        return str(candidate.resolve())
    return value


def _run_step(command: list[str]) -> int:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            shell=False,
        )
    except OSError as error:
        print(f"无法执行构建命令：{error}", file=sys.stderr)
        return 1
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc", help="MinGW-w64 GCC命令名或路径，默认gcc")
    parser.add_argument("--objdump", help="objdump命令名或路径，默认使用GCC同目录工具")
    parser.add_argument("--loader-zip", help="Bingus Shared Loader v18来源ZIP")
    parser.add_argument("--menu-zip", help="Mod Options Menu v1.2来源ZIP")
    parser.add_argument("--output-dir", help="两种安装包的输出目录")
    args = parser.parse_args(argv)

    try:
        invocation_directory = Path.cwd().resolve()
        native_command = [
            sys.executable,
            str(NATIVE_BUILD_SCRIPT),
            "--output",
            str(DEFAULT_DLL),
            "--meta",
            str(DEFAULT_META),
            "--cc",
            _tool_argument(args.cc, invocation_directory),
        ]
        if args.objdump is not None:
            native_command.extend(
                ("--objdump", _tool_argument(args.objdump, invocation_directory))
            )

        native_result = _run_step(native_command)
        if native_result != 0:
            return native_result

        package_command = [
            sys.executable,
            str(PACKAGE_BUILD_SCRIPT),
            "--native-dll",
            str(DEFAULT_DLL),
            "--native-meta",
            str(DEFAULT_META),
        ]
        if args.loader_zip is not None:
            package_command.extend(
                ("--loader-zip", _resolved_input_path(args.loader_zip, invocation_directory))
            )
        if args.menu_zip is not None:
            package_command.extend(
                ("--menu-zip", _resolved_input_path(args.menu_zip, invocation_directory))
            )
        if args.output_dir is not None:
            package_command.extend(
                ("--output-dir", _resolved_input_path(args.output_dir, invocation_directory))
            )
        return _run_step(package_command)
    except OSError as error:
        print(f"无法解析构建路径：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
