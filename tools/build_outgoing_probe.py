"""构建临时只读发送代码窗口诊断包。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import zipfile

import build_package as builder


OUTGOING_PROBE_FLAG = b"local OUTGOING_PROBE_ENABLED = false --[[HD2_CHAT_OUTGOING_PROBE_ENABLED]]"
DISABLED_FLAGS = (
    builder.OBSERVE_FLAG,
    builder.DISPLAY_TEST_FLAG,
    builder.TRANSLATE_FLAG,
    builder.STANDALONE_FLAG,
)
PROBE_DESCRIPTION = (
    "临时只读发送代码窗口诊断包。仅在游戏构建指纹门禁通过后读取四个固定代码窗口，"
    "不读取聊天正文、不修改游戏内存、不调用候选函数、不翻译或发送网络请求；"
    "候选函数及其 ABI 均未验证。此包会暂时替换同 GUID 模组，采集后请重新导入正常版本。"
)


def _diagnostic_entry() -> bytes:
    """生成只含扫描核心的入口，并只启用有界代码窗口探针。"""
    entry_path = builder.ROOT / "game" / "chat_probe.lua"
    core_path = builder.ROOT / "game" / "chat_probe_core.lua"
    entry = builder.entry_source(entry_path.read_bytes(), core_path.read_bytes())
    if entry.count(OUTGOING_PROBE_FLAG) != 1:
        raise ValueError("入口必须恰好包含一个默认关闭的发送代码窗口探针开关")
    for flag in DISABLED_FLAGS:
        if entry.count(flag) != 1:
            raise ValueError("入口必须恰好包含一个默认关闭的观察、显示或翻译开关")
    enabled = OUTGOING_PROBE_FLAG.replace(b"= false", b"= true")
    if enabled == OUTGOING_PROBE_FLAG:
        raise ValueError("发送代码窗口探针开关格式无效")
    entry = entry.replace(OUTGOING_PROBE_FLAG, enabled, 1)
    if any(flag.replace(b"= false", b"= true") in entry for flag in DISABLED_FLAGS):
        raise ValueError("诊断入口意外启用了观察、显示或翻译功能")
    return entry


def build_artifact(
    output: Path | str | None = None,
    *,
    loader_zip: Path | str | None = None,
    menu_zip: Path | str | None = None,
    now: datetime | None = None,
) -> Path:
    """用固定上游资源生成同 GUID 的临时诊断 ZIP，不覆盖已有文件。"""
    output_path = builder._select_output_path(output, now=now)
    if output is None and os.path.lexists(output_path):
        raise ValueError(f"默认诊断包已存在，拒绝覆盖：{output_path.name}")
    input_paths = (
        builder.ROOT / "game" / "chat_probe.lua",
        builder.ROOT / "game" / "chat_probe_core.lua",
        builder.PROJECT_LICENSE,
        builder.STANDALONE_LICENSE,
        builder.MOD_OPTIONS_MENU_LICENSE,
        builder.MOD_OPTIONS_MENU_SOURCE,
        builder.SHARED_LOADER_ZIP if loader_zip is None else Path(loader_zip),
        builder.MOD_OPTIONS_MENU_ZIP if menu_zip is None else Path(menu_zip),
    )
    if any(builder._same_path(output_path, path) for path in input_paths):
        raise ValueError("输出不能覆盖诊断包构建输入")

    files = builder.addon_files(
        _diagnostic_entry(),
        loader_zip=loader_zip,
        menu_zip=menu_zip,
    )
    if len(files) != 11:
        raise ValueError("诊断包资源清单与固定 11 个成员不匹配")
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("诊断包 manifest 无法安全读取") from error
    if not isinstance(manifest, dict) or manifest.get("Guid") != builder.ADDON_GUID:
        raise ValueError("诊断包必须保留现有模组 GUID")
    manifest["Name"] = "HD2 Chat Outgoing Code Probe"
    manifest["Description"] = PROBE_DESCRIPTION
    for option in manifest.get("Options", []):
        if isinstance(option, dict):
            option["Name"] = "HD2 Chat Outgoing Code Probe"
            option["Description"] = PROBE_DESCRIPTION
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="显式指定 ZIP 路径；CLI 文件名须为 HD2ChatTranslateYYYYMMDDHHMMSS.zip。",
    )
    parser.add_argument("--loader-zip", type=Path, help="固定 Bingus Shared Loader v18 来源 ZIP。")
    parser.add_argument("--menu-zip", type=Path, help="固定 ModOptionsMenu v1.2 来源 ZIP。")
    args = parser.parse_args()
    if args.output is not None and not builder.is_delivery_filename(args.output.name):
        parser.error("CLI 诊断文件名必须为 HD2ChatTranslateYYYYMMDDHHMMSS.zip，时间使用北京时间")
    try:
        result = build_artifact(args.output, loader_zip=args.loader_zip, menu_zip=args.menu_zip)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建临时只读发送代码窗口诊断 ZIP：{result}")


if __name__ == "__main__":
    main()
