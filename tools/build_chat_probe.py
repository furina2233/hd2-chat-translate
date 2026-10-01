"""构建只读聊天函数研究探针的 Bingus 单 addon ZIP。"""

# 格式依据公开实现核对：https://github.com/CowboyBingus/BingusSharedLoader/tree/main/scripts

from __future__ import annotations

import argparse
import json
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
MAX_SOURCE_BYTES = 512 * 1024


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


def entry_source(source: bytes, core_source: bytes) -> bytes:
    if len(source) > MAX_SOURCE_BYTES or len(core_source) > MAX_SOURCE_BYTES:
        raise ValueError("Lua 源文件超过构建大小上限")
    if source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in source:
        raise ValueError("入口必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
    if core_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in core_source:
        raise ValueError("核心必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
    source.decode("utf-8")
    core_source.decode("utf-8")
    if source.count(CORE_MARKER) != 1:
        raise ValueError("入口必须恰好包含一个扫描核心嵌入标记")
    embedded = source.replace(CORE_MARKER, core_source.rstrip() + b"\n", 1)
    if embedded.startswith(b"-- HD2-Addon:"):
        _, separator, embedded = embedded.partition(b"\n")
        if not separator:
            raise ValueError("addon 声明行不完整")
    declaration = ("-- HD2-Addon: " + RESOURCE_NAME + "\n").encode("utf-8")
    if len(declaration) > 256:
        raise ValueError("addon 声明超过 256 字节")
    return declaration + embedded


def addon_files(entry: bytes) -> dict[str, bytes]:
    resource = struct.pack("<II", len(entry), 2) + entry
    archive = make_single_resource_archive(resource_hash(RESOURCE_NAME), resource)
    description = (
        "只读研究探针：验证指定 game.dll 构建并导出候选字节，不连接聊天或调用游戏函数。"
        "需要 Bingus Shared Loader v15+ / API 1。"
    )
    manifest = {
        "Version": 1,
        "Guid": str(uuid.UUID(ADDON_GUID)),
        "Name": "HD2 Chat Probe Research Tool",
        "Description": description,
        "Options": [
            {
                "Name": "HD2 Chat Probe Research Tool",
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


def build_artifact(output: Path | str | None = None) -> Path:
    entry_path = ROOT / "game" / "chat_probe.lua"
    core_path = ROOT / "game" / "chat_probe_core.lua"
    output_path = Path(output) if output is not None else ROOT / "artifacts" / "HD2ChatProbe.zip"
    if output_path.resolve() in (entry_path.resolve(), core_path.resolve()):
        raise ValueError("输出不能覆盖 Lua 源文件")
    packaged_entry = entry_source(entry_path.read_bytes(), core_path.read_bytes())
    files = addon_files(packaged_entry)
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
    args = parser.parse_args()
    result = build_artifact(args.output)
    print(f"已构建研究探针：{result}")


if __name__ == "__main__":
    main()
