"""构建 HD2 聊天代码研究、只读观察、固定显示测试或翻译桥接的 Bingus addon ZIP。"""

# 格式依据公开实现核对：https://github.com/CowboyBingus/BingusSharedLoader/tree/main/scripts

from __future__ import annotations

import argparse
import hashlib
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
TRANSLATE_MARKER = b"--[[HD2_CHAT_TRANSLATE_CORE]]"
TRANSLATE_FLAG = b"local TRANSLATE_ENABLED = false --[[HD2_CHAT_TRANSLATE_ENABLED]]"
STANDALONE_FLAG = b"local STANDALONE_ENABLED = false --[[HD2_CHAT_STANDALONE_ENABLED]]"
NATIVE_MODULE_MARKER = b"--[[HD2CT_NATIVE_MODULE]]"
STANDALONE_MARKER = NATIVE_MODULE_MARKER
NATIVE_PAYLOAD_MARKER = b"--[[HD2CT_NATIVE_PAYLOAD]]"
MAX_SOURCE_BYTES = 512 * 1024
DEFAULT_OUTPUT = ROOT / "artifacts" / "HD2ChatProbe.zip"
OBSERVE_OUTPUT = ROOT / "artifacts" / "HD2ChatObserve.zip"
DISPLAY_TEST_OUTPUT = ROOT / "artifacts" / "HD2ChatDisplayTest.zip"
TRANSLATE_OUTPUT = ROOT / "artifacts" / "HD2ChatTranslate.zip"
STANDALONE_OUTPUT = ROOT / "artifacts" / "HD2ChatTranslateStandalone.zip"
STANDALONE_DLL = ROOT / "artifacts" / "native" / "hd2ct_http.dll"
STANDALONE_META = ROOT / "artifacts" / "native" / "hd2ct_http.meta.json"
STANDALONE_LICENSE = ROOT / "native" / "vendor" / "cjson" / "LICENSE"
DEPLOYMENT_RECEIPT = ROOT / ".local" / "chat-probe-deployment.json"


def validate_native_dll(dll: bytes, metadata: bytes) -> tuple[int, str]:
    """核验固定原生 helper 的meta、SHA-256和PE入口布局。"""
    try:
        meta = json.loads(metadata.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("原生 helper meta 不是有效 UTF-8 JSON") from error
    if not isinstance(meta, dict):
        raise ValueError("原生 helper meta 格式无效")
    imports = meta.get("imports")
    if (
        type(meta.get("schema_version")) is not int
        or meta.get("schema_version") != 1
        or type(meta.get("abi_version")) is not int
        or meta.get("abi_version") != 1
        or meta.get("filename") != "hd2ct_http.dll"
        or meta.get("architecture") != "x86_64"
        or isinstance(meta.get("size"), bool)
        or not isinstance(meta.get("size"), int)
        or not isinstance(meta.get("sha256"), str)
        or len(meta.get("sha256", "")) != 64
        or any(char not in "0123456789abcdef" for char in meta.get("sha256", ""))
        or not isinstance(imports, list)
        or any(not isinstance(item, str) or not item or len(item) > 260 for item in imports)
    ):
        raise ValueError("原生 helper meta 的ABI、架构或字段不匹配")
    size = len(dll)
    digest = hashlib.sha256(dll).hexdigest()
    if size == 0 or meta["size"] != size or meta["sha256"].lower() != digest:
        raise ValueError("原生 helper 的大小或 SHA-256 与meta不匹配")
    if len(dll) < 0x40 or dll[:2] != b"MZ":
        raise ValueError("原生 helper 缺少 PE/DOS 头")
    pe_offset = struct.unpack_from("<I", dll, 0x3C)[0]
    if pe_offset < 0x40 or pe_offset > len(dll) - 24 or dll[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("原生 helper 的 PE 头位置无效")
    machine, section_count, _, _, _, optional_size, characteristics = struct.unpack_from(
        "<HHIIIHH", dll, pe_offset + 4
    )
    if machine != 0x8664 or not 1 <= section_count <= 96 or not characteristics & 0x2000:
        raise ValueError("原生 helper 必须是 x64 PE DLL")
    optional_offset = pe_offset + 24
    if optional_size < 112 or optional_offset + optional_size > len(dll):
        raise ValueError("原生 helper 的可选头长度无效")
    if struct.unpack_from("<H", dll, optional_offset)[0] != 0x20B:
        raise ValueError("原生 helper 必须使用 PE32+ 格式")
    entry_rva = struct.unpack_from("<I", dll, optional_offset + 16)[0]
    image_size = struct.unpack_from("<I", dll, optional_offset + 56)[0]
    if image_size == 0 or entry_rva >= image_size and entry_rva != 0:
        raise ValueError("原生 helper 的入口地址超出映像范围")
    section_table = optional_offset + optional_size
    if section_table + section_count * 40 > len(dll):
        raise ValueError("原生 helper 的节表超出文件范围")
    if entry_rva:
        entry_is_executable = False
        for index in range(section_count):
            offset = section_table + index * 40
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", dll, offset + 8)
            extent = max(virtual_size, raw_size)
            if virtual_address <= entry_rva < virtual_address + extent:
                section_flags = struct.unpack_from("<I", dll, offset + 36)[0]
                entry_is_executable = bool(section_flags & 0x20000000)
                break
        if not entry_is_executable:
            raise ValueError("原生 helper 的入口点不在可执行节中")
    return size, digest


def standalone_module_source(template: bytes, dll: bytes, metadata: bytes) -> bytes:
    """将已核验的固定DLL作为hex载荷写入Lua加载器的唯一payload标记。"""
    if template.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in template:
        raise ValueError("原生Lua适配器必须是无BOM、无字节码标记且无NUL的UTF-8文本")
    template.decode("utf-8")
    if template.count(NATIVE_PAYLOAD_MARKER) != 1:
        raise ValueError("原生Lua适配器必须恰好包含一个payload标记")
    size, digest = validate_native_dll(dll, metadata)
    if len(template) + size * 2 > MAX_SOURCE_BYTES:
        raise ValueError("DLL hex载荷将超过512 KiB Lua源码上限；请将结果交由主控评估")
    payload = (
        f"local HD2CT_DLL_SIZE = {size}\n"
        f'local HD2CT_DLL_SHA256 = "{digest}"\n'
        f'local HD2CT_DLL_HEX = "{dll.hex()}"\n'
    ).encode("ascii")
    embedded = template.replace(NATIVE_PAYLOAD_MARKER, payload, 1)
    if len(embedded) > MAX_SOURCE_BYTES:
        raise ValueError("独立原生Lua模块超过512 KiB源码上限")
    return embedded


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
    *,
    translate_source: bytes | None = None,
    translate: bool = False,
    standalone_source: bytes | None = None,
    standalone: bool = False,
) -> bytes:
    if sum((bool(display_test), bool(translate), bool(standalone))) > 1:
        raise ValueError("固定显示测试、伴随翻译和独立翻译模式互斥")
    sources = [source, core_source]
    if observer_source is not None:
        sources.append(observer_source)
    if translate_source is not None:
        sources.append(translate_source)
    if standalone_source is not None:
        sources.append(standalone_source)
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
    if translate_source is not None:
        if translate_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in translate_source:
            raise ValueError("翻译核心必须是无 BOM、无字节码标记的 UTF-8 Lua 文本")
        translate_source.decode("utf-8")
    if standalone_source is not None:
        if standalone_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in standalone_source:
            raise ValueError("原生适配器必须是无BOM、无字节码标记的UTF-8 Lua文本")
        standalone_source.decode("utf-8")
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
    standalone_marker_count = source.count(STANDALONE_MARKER)
    standalone_flag_count = source.count(STANDALONE_FLAG)
    if standalone_marker_count > 1 or standalone_flag_count > 1:
        raise ValueError("入口包含多个独立模块或开关标记")
    if standalone and (standalone_marker_count != 1 or standalone_flag_count != 1):
        raise ValueError("独立翻译模式要求入口恰好包含一个模块标记和默认关闭标记")
    if standalone and (standalone_source is None or observer_source is None):
        raise ValueError("独立翻译模式必须嵌入原生网络模块和只读适配器")
    if standalone and translate_source is None:
        raise ValueError("独立翻译模式必须嵌入翻译核心")
    if standalone_source is not None and not standalone:
        raise ValueError("未启用独立模式时不能注入原生网络模块")
    if translate and standalone:
        raise ValueError("伴随翻译和独立翻译模式互斥")
    translate_marker_count = source.count(TRANSLATE_MARKER)
    translate_flag_count = source.count(TRANSLATE_FLAG)
    if translate_marker_count > 1 or translate_flag_count > 1:
        raise ValueError("入口包含多个翻译核心或开关标记")
    if (translate or standalone) and (translate_marker_count != 1 or translate_flag_count != 1):
        raise ValueError("翻译模式要求入口恰好包含一个核心标记和默认关闭标记")
    if (translate or standalone) and (translate_source is None or observer_source is None):
        raise ValueError("翻译模式必须嵌入翻译核心和只读适配器")
    embedded = embedded.replace(OBSERVE_MARKER, (observer_source or b"return nil").rstrip() + b"\n", 1)
    if observer_source is not None and not translate and not standalone:
        embedded = embedded.replace(OBSERVE_FLAG, OBSERVE_FLAG.replace(b"= false", b"= true"), 1)
    if display_test:
        embedded = embedded.replace(
            DISPLAY_TEST_FLAG,
            DISPLAY_TEST_FLAG.replace(b"= false", b"= true"),
            1,
        )
    if translate_marker_count == 1:
        embedded = embedded.replace(
            TRANSLATE_MARKER,
            (translate_source if translate or standalone else b"return nil").rstrip() + b"\n",
            1,
        )
    if translate or standalone:
        embedded = embedded.replace(TRANSLATE_FLAG, TRANSLATE_FLAG.replace(b"= false", b"= true"), 1)
    if standalone_marker_count == 1:
        embedded = embedded.replace(
            STANDALONE_MARKER,
            (standalone_source if standalone else b"return nil").rstrip() + b"\n",
            1,
        )
    if standalone:
        embedded = embedded.replace(STANDALONE_FLAG, STANDALONE_FLAG.replace(b"= false", b"= true"), 1)
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


def addon_files(
    entry: bytes,
    observe: bool = False,
    display_test: bool = False,
    translate: bool = False,
    *,
    standalone: bool = False,
) -> dict[str, bytes]:
    if sum((bool(observe), bool(display_test), bool(translate), bool(standalone))) > 1:
        raise ValueError("观察、固定显示测试、伴随翻译和独立翻译模式互斥")
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
    elif translate:
        description = (
            "本机聊天翻译桥接：将当前本机游戏会话中符合条件的聊天正文通过固定本地 mailbox 交给本机服务，"
            "再尝试更新本机显示；插件本身不联网、不广播译文。需要 Bingus Shared Loader v15+ / API 1。"
        )
    elif standalone:
        description = (
            "进程内聊天翻译：通过游戏内原生网络线程将聊天交给配置的大模型并更新本机显示，不广播译文。"
            "无需运行伴随程序。读取 HD2CT_API_URL、HD2CT_MODEL、HD2CT_API_KEY、"
            "HD2CT_TIMEOUT_SECONDS（默认20）和 HD2CT_ENABLED（默认1）环境变量。"
            "需要 Bingus Shared Loader v18 / API 1。"
        )
    manifest = {
        "Version": 1,
        "Guid": str(uuid.UUID(ADDON_GUID)),
        "Name": (
            "HD2 Chat Translate" if translate else
            "HD2 Chat Translate Standalone" if standalone else
            "HD2 Chat Display Test" if display_test else
            "HD2 Chat Probe Research Tool"
        ),
        "Description": description,
        "Options": [
            {
                "Name": (
                    "HD2 Chat Translate" if translate else
                    "HD2 Chat Translate Standalone" if standalone else
                    "HD2 Chat Display Test" if display_test else
                    "HD2 Chat Probe Research Tool"
                ),
                "Description": description,
                "Include": ["Addon"],
            }
        ],
    }
    files = {
        "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "Addon/" + ARCHIVE_NAME: archive,
        "Addon/" + ARCHIVE_NAME + ".stream": b"",
        "Addon/" + ARCHIVE_NAME + ".gpu_resources": b"",
    }
    if standalone:
        try:
            files["LICENSES/cJSON-LICENSE.txt"] = STANDALONE_LICENSE.read_bytes()
        except OSError as error:
            raise ValueError("独立包缺少vendor/cJSON许可证原文") from error
    return files


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
        for item in (
            DEFAULT_OUTPUT,
            OBSERVE_OUTPUT,
            DISPLAY_TEST_OUTPUT,
            TRANSLATE_OUTPUT,
            STANDALONE_OUTPUT,
        )
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
    translate: bool = False,
    *,
    standalone: bool = False,
) -> Path:
    if sum((bool(observe), bool(display_test), bool(translate), bool(standalone))) > 1:
        raise ValueError("--observe、--display-test、--translate 与 --standalone 不能同时使用")
    entry_path = ROOT / "game" / "chat_probe.lua"
    core_path = ROOT / "game" / "chat_probe_core.lua"
    observer_path = ROOT / "game" / "chat_observe_core.lua"
    translate_path = ROOT / "game" / "chat_translate_core.lua"
    standalone_module_path = ROOT / "game" / "chat_http_native.lua"
    if output is not None:
        output_path = Path(output)
    elif standalone:
        output_path = STANDALONE_OUTPUT
    elif display_test:
        output_path = DISPLAY_TEST_OUTPUT
    elif translate:
        output_path = TRANSLATE_OUTPUT
    else:
        output_path = OBSERVE_OUTPUT if observe else DEFAULT_OUTPUT
    _protect_deployed_source(output_path)
    source_paths = [entry_path, core_path, observer_path, translate_path, standalone_module_path]
    if standalone:
        source_paths.extend((STANDALONE_DLL, STANDALONE_META, STANDALONE_LICENSE))
    if output_path.resolve() in tuple(path.resolve() for path in source_paths):
        raise ValueError("输出不能覆盖 Lua 源文件")
    native_module = None
    if standalone:
        if not STANDALONE_DLL.is_file() or not STANDALONE_META.is_file():
            raise ValueError(
                "缺少 artifacts/native/hd2ct_http.dll 或配套meta；请先构建原生 helper，builder不会自动编译或下载。"
            )
        if not STANDALONE_LICENSE.is_file():
            raise ValueError("缺少 native/vendor/cjson/LICENSE")
        native_module = standalone_module_source(
            standalone_module_path.read_bytes(),
            STANDALONE_DLL.read_bytes(),
            STANDALONE_META.read_bytes(),
        )
    packaged_entry = entry_source(
        entry_path.read_bytes(),
        core_path.read_bytes(),
        observer_path.read_bytes() if (observe or display_test or translate or standalone) else None,
        display_test=display_test,
        translate_source=translate_path.read_bytes() if (translate or standalone) else None,
        translate=translate,
        standalone_source=native_module,
        standalone=standalone,
    )
    files = addon_files(packaged_entry, observe, display_test, translate, standalone=standalone)
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
    mode.add_argument(
        "--translate",
        action="store_true",
        help="构建本机翻译桥接 ZIP，默认输出 artifacts/HD2ChatTranslate.zip",
    )
    mode.add_argument(
        "--standalone",
        action="store_true",
        help="构建进程内网络翻译 ZIP，默认输出 artifacts/HD2ChatTranslateStandalone.zip",
    )
    args = parser.parse_args()
    try:
        result = build_artifact(
            args.output, args.observe, args.display_test, args.translate, standalone=args.standalone
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建HD2聊天插件ZIP：{result}")


if __name__ == "__main__":
    main()
