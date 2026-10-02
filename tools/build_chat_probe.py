"""构建 HD2 聊天代码研究、只读观察或固定中文显示测试的 Bingus addon ZIP。"""

# 格式依据公开实现核对：https://github.com/CowboyBingus/BingusSharedLoader/tree/main/scripts

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_NAME = "mods/hd2chat/chat_probe"
ARCHIVE_NAME = "9ba626afa44a3aa3.patch_0"
RESOURCE_TYPE = 0xA14E8DFA2CD117E2
ADDON_GUID = "a741d044-972b-4dc5-b08e-1a68441e1d7f"
CORE_MARKER = b"--[[HD2_CHAT_PROBE_CORE]]"
OBSERVE_MARKER = b"--[[HD2_CHAT_OBSERVER_CORE]]"
OBSERVE_FLAG = b"local OBSERVE_ENABLED = false --[[HD2_CHAT_OBSERVER_ENABLED]]"
DISPLAY_TEST_FLAG = b"local DISPLAY_TEST_ENABLED = false --[[HD2_CHAT_DISPLAY_TEST_ENABLED]]"
MAX_SOURCE_BYTES = 512 * 1024
DEFAULT_OUTPUT = ROOT / "artifacts" / "HD2ChatProbe.zip"
OBSERVE_OUTPUT = ROOT / "artifacts" / "HD2ChatObserve.zip"
DISPLAY_TEST_OUTPUT = ROOT / "artifacts" / "HD2ChatDisplayTest.zip"
DEPLOYMENT_RECEIPT = ROOT / ".local" / "chat-probe-deployment.json"


def resource_hash(name: str) -> int:
    """按公开 archive 格式计算资源名的 64 位 Murmur 风格散列。"""
    data = name.encode("utf-8")
    mask = (1 << 64) - 1
    mix = 0xC6A4A7935BD1E995
    value = len(data) * mix & mask
    cursor = 0
    while cursor + 8 <= len(data):
        word = int.from_bytes(data[cursor : cursor + 8], "little")
        word = word * mix & mask
        word ^= word >> 47
        value = (value ^ (word * mix & mask)) * mix & mask
        cursor += 8
    if cursor < len(data):
        tail = int.from_bytes(data[cursor:], "little")
        value = (value ^ tail) * mix & mask
    value ^= value >> 47
    value = value * mix & mask
    return value ^ (value >> 47)


def make_single_resource_archive(name_hash: int, resource: bytes) -> bytes:
    """构造本工具唯一需要的单资源 archive，并按 16 字节对齐。"""
    header_size = 72
    type_record_size = 32
    entry_size = 80
    entry_offset = header_size + type_record_size
    data_offset = (entry_offset + entry_size + 15) & ~15
    archive_size = (data_offset + len(resource) + 15) & ~15
    archive = bytearray(archive_size)
    struct.pack_into("<III20sQQ24s", archive, 0, 0xF0000011, 1, 1, b"", archive_size, 0, b"")
    struct.pack_into("<IIQIIII", archive, header_size, 0, 0, RESOURCE_TYPE, 1, 0, 16, 16)
    struct.pack_into(
        "<7Q6I",
        archive,
        entry_offset,
        name_hash,
        RESOURCE_TYPE,
        data_offset,
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
    archive[data_offset : data_offset + len(resource)] = resource
    return bytes(archive)


def entry_source(
    source: bytes,
    core_source: bytes,
    observer_source: bytes | None = None,
    display_test: bool = False,
) -> bytes:
    sources = (source, core_source) if observer_source is None else (source, core_source, observer_source)
    if any(len(item) > MAX_SOURCE_BYTES for item in sources):
        raise ValueError("Lua 源文件超过构建大小上限")
    if source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in source:
        raise ValueError("入口必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
    if core_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in core_source:
        raise ValueError("核心必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
    source.decode("utf-8")
    core_source.decode("utf-8")
    if observer_source is not None:
        if observer_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in observer_source:
            raise ValueError("观察核心必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
        observer_source.decode("utf-8")
    if source.count(CORE_MARKER) != 1:
        raise ValueError("入口必须恰好包含一个扫描核心嵌入标记")
    embedded = source.replace(CORE_MARKER, core_source.rstrip() + b"\n", 1)
    if source.count(OBSERVE_MARKER) != 1 or source.count(OBSERVE_FLAG) != 1:
        raise ValueError("入口必须恰好包含一个观察核心标记及默认关闭标记")
    display_flag_count = source.count(DISPLAY_TEST_FLAG)
    if display_test and display_flag_count != 1:
        raise ValueError("固定显示测试模式要求入口恰好包含一个默认关闭标记")
    if display_test and observer_source is None:
        raise ValueError("固定显示测试模式必须嵌入观察核心")
    if display_flag_count > 1:
        raise ValueError("入口包含多个固定显示测试标记")
    embedded = embedded.replace(OBSERVE_MARKER, (observer_source or b"return nil").rstrip() + b"\n", 1)
    if observer_source is not None:
        embedded = embedded.replace(OBSERVE_FLAG, OBSERVE_FLAG.replace(b"= false", b"= true"), 1)
    if display_test:
        embedded = embedded.replace(
            DISPLAY_TEST_FLAG,
            DISPLAY_TEST_FLAG.replace(b"= false", b"= true"),
            1,
        )
    if embedded.startswith(b"-- HD2-Addon:"):
        _, separator, embedded = embedded.partition(b"\n")
        if not separator:
            raise ValueError("addon 声明行不完整")
    declaration = ("-- HD2-Addon: " + RESOURCE_NAME + "\n").encode("utf-8")
    if len(declaration) > 256:
        raise ValueError("addon 声明超过 256 字节")
    if len(declaration + embedded) > MAX_SOURCE_BYTES:
        raise ValueError("嵌入后的 Lua 源文件超过构建大小上限")
    return declaration + embedded


def addon_files(entry: bytes, observe: bool = False, display_test: bool = False) -> dict[str, bytes]:
    resource = struct.pack("<II", len(entry), 2) + entry
    archive = make_single_resource_archive(resource_hash(RESOURCE_NAME), resource)
    description = (
        "只读研究探针：验证指定 game.dll 构建并导出候选字节，不连接聊天或调用游戏函数。"
        "需要 Bingus Shared Loader v15+ / API 1。"
    )
    if display_test:
        description = (
            "固定聊天显示测试：仅将插件识别出的固定 ASCII 测试消息替换为“聊天翻译测试成功”。"
            "不联网、不广播；不会替换其他聊天正文。需要 Bingus Shared Loader v15+ / API 1。"
        )
    elif observe:
        description = (
            "只读聊天观察器：校验指定构建后读取有界历史和 UI 元数据，只报告固定测试消息是否出现。"
            "不保存普通聊天，不调用游戏函数、不写游戏内存、不连接大模型。需要 Bingus Shared Loader v15+ / API 1。"
        )
    manifest = {
        "Version": 1,
        "Guid": str(uuid.UUID(ADDON_GUID)),
        "Name": "HD2 Chat Display Test" if display_test else "HD2 Chat Probe Research Tool",
        "Description": description,
        "Options": [
            {
                "Name": "HD2 Chat Display Test" if display_test else "HD2 Chat Probe Research Tool",
                "Description": description,
                "Include": ["Addon"],
            }
        ],
    }
    return {
        "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "Addon/" + ARCHIVE_NAME: archive,
        "Addon/" + ARCHIVE_NAME + ".stream": b"",
        "Addon/" + ARCHIVE_NAME + ".gpu_resources": b"",
    }


def _same_path(left: Path, right: Path) -> bool:
    """按本机路径规则比较绝对路径，避免大小写或相对路径绕过保护。"""
    return os.path.normcase(str(left.expanduser().resolve())) == os.path.normcase(
        str(right.expanduser().resolve())
    )


def _protect_deployed_source(output_path: Path) -> None:
    """部署收据仍引用旧 ZIP 时，拒绝覆盖可用于回滚的来源包。"""
    if not DEPLOYMENT_RECEIPT.exists():
        return

    is_default = any(
        _same_path(output_path, item)
        for item in (DEFAULT_OUTPUT, OBSERVE_OUTPUT, DISPLAY_TEST_OUTPUT)
    )
    try:
        receipt = json.loads(DEPLOYMENT_RECEIPT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if is_default:
            raise ValueError(
                "无法读取部署收据；为保护可能仍在使用的旧来源 ZIP，拒绝覆盖默认输出。"
                "请先核实并回滚部署，或指定新的 --output 路径。"
            ) from error
        return

    if not isinstance(receipt, dict):
        if is_default:
            raise ValueError("部署收据格式无效，拒绝覆盖默认输出；请先核实并回滚部署。")
        return
    source_zips = receipt.get("sourceZips")
    if not isinstance(source_zips, dict):
        if is_default:
            raise ValueError("部署收据缺少 sourceZips，拒绝覆盖默认输出；请先核实并回滚部署。")
        return

    source_paths: list[Path] = []
    malformed_sources = not all(
        isinstance(source_zips.get(role), dict)
        and isinstance(source_zips[role].get("path"), str)
        and bool(source_zips[role]["path"].strip())
        for role in ("probe", "loader")
    )
    for source in source_zips.values():
        if not isinstance(source, dict):
            malformed_sources = True
            continue
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path.strip():
            malformed_sources = True
            continue
        path = Path(source_path)
        source_paths.append(path)
        if _same_path(output_path, path):
            raise ValueError(
                "输出路径仍被部署收据引用为来源 ZIP，拒绝覆盖；"
                "请先回滚部署，或指定新的 --output 路径。"
            )
    if (malformed_sources or not source_paths) and is_default:
        raise ValueError("部署收据的来源路径不完整，拒绝覆盖默认输出；请先核实并回滚部署。")


def build_artifact(
    output: Path | str | None = None,
    observe: bool = False,
    display_test: bool = False,
) -> Path:
    if observe and display_test:
        raise ValueError("--observe 与 --display-test 不能同时使用")
    entry_path = ROOT / "game" / "chat_probe.lua"
    core_path = ROOT / "game" / "chat_probe_core.lua"
    observer_path = ROOT / "game" / "chat_observe_core.lua"
    if output is not None:
        output_path = Path(output)
    elif display_test:
        output_path = DISPLAY_TEST_OUTPUT
    else:
        output_path = OBSERVE_OUTPUT if observe else DEFAULT_OUTPUT
    _protect_deployed_source(output_path)
    if output_path.resolve() in (entry_path.resolve(), core_path.resolve(), observer_path.resolve()):
        raise ValueError("输出不能覆盖 Lua 源文件")
    packaged_entry = entry_source(
        entry_path.read_bytes(),
        core_path.read_bytes(),
        observer_path.read_bytes() if (observe or display_test) else None,
        display_test=display_test,
    )
    files = addon_files(packaged_entry, observe, display_test)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="ZIP 输出路径，默认 artifacts/HD2ChatProbe.zip")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--observe", action="store_true", help="构建持续只读观察器，默认输出 artifacts/HD2ChatObserve.zip")
    mode.add_argument(
        "--display-test",
        action="store_true",
        help="构建固定中文显示测试 ZIP，默认输出 artifacts/HD2ChatDisplayTest.zip",
    )
    args = parser.parse_args()
    try:
        result = build_artifact(args.output, args.observe, args.display_test)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建HD2 Chat Probe addon：{result}")


if __name__ == "__main__":
    main()
