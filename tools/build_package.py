"""从源码构建 HD2 Chat Translate 的 standalone Arsenal ZIP。"""

# 格式依据公开实现核对：https://github.com/CowboyBingus/BingusSharedLoader/tree/main/scripts

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import struct
import uuid
import zipfile
import target_languages
import menu_locales

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_NAME = "mods/hd2chat/HD2ChatTranslate"
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
SETTINGS_MARKER = b"--[[HD2CT_TARGET_LANGUAGE_SETTINGS]]"
SETTINGS_CATALOGUE_MARKER = b"--[[HD2CT_TARGET_LANGUAGE_CATALOGUE]]"
MENU_LOCALES_MARKER = b"--[[HD2CT_MENU_LOCALES]]"
MAX_SOURCE_BYTES = 512 * 1024
MAX_ARCHIVE_LUA_RESOURCES = 16
MAX_ARCHIVE_SIZE = 8 * 1024 * 1024
DELIVERY_PREFIX = "HD2ChatTranslate"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
DELIVERY_NAME_PATTERN = re.compile(r"^HD2ChatTranslate([0-9]{14})\.zip$", re.ASCII)
STANDALONE_DLL = ROOT / "artifacts" / "native" / "hd2ct_http.dll"
STANDALONE_META = ROOT / "artifacts" / "native" / "hd2ct_http.meta.json"
STANDALONE_LICENSE = ROOT / "native" / "vendor" / "cjson" / "LICENSE"
MOD_OPTIONS_MENU_ZIP = ROOT / "artifacts" / "Mod-Options-Menu-v1.2.zip"
MOD_OPTIONS_MENU_LICENSE = ROOT / "third_party" / "mod_options_menu" / "LICENSE"
MOD_OPTIONS_MENU_SOURCE = ROOT / "third_party" / "mod_options_menu" / "SOURCE.txt"
PROJECT_LICENSE = ROOT / "LICENSE"
SHARED_LOADER_ZIP = ROOT / "artifacts" / "Bingus-Shared-Loader-v18.zip"
SHARED_LOADER_ZIP_SHA256 = "53af5698aeacfb27b98dfa00054923d11dc854e1e67b4af14798877812a93ba6"
SHARED_LOADER_PATCH_SHA256 = "950a1b29c70a5bf3f94f5b6a154a3935d5c354be20cb7191d803c4c7ed9191e2"
SHARED_LOADER_RESOURCE_SHA256 = "88ccb04f980bf77daa5ad080c9052be1304bdce21d838a2b26a6e73ac2aa2db0"
SHARED_LOADER_RESOURCE_HASH = 0x7251FDD9BB62480A
SHARED_LOADER_PATCH_MEMBER = "data/9ba626afa44a3aa3.patch_0"
SHARED_LOADER_STREAM_MEMBER = SHARED_LOADER_PATCH_MEMBER + ".stream"
SHARED_LOADER_GPU_MEMBER = SHARED_LOADER_PATCH_MEMBER + ".gpu_resources"
SHARED_LOADER_README_MEMBER = "BingusSharedLoader-README.txt"
SHARED_LOADER_MANIFEST_MEMBER = "BingusSharedLoader-manifest.json"
MOD_OPTIONS_MENU_ZIP_SHA256 = "a977d84e7f8fda62c587b5b5af2012aab3fa792e31945a9c8d5f458bfd453e13"
MOD_OPTIONS_MENU_ZIP_SIZE = 1_179_177
MOD_OPTIONS_MENU_PATCH_MEMBER = "Addon/9ba626afa44a3aa3.patch_0"
MOD_OPTIONS_MENU_STREAM_MEMBER = MOD_OPTIONS_MENU_PATCH_MEMBER + ".stream"
MOD_OPTIONS_MENU_GPU_MEMBER = MOD_OPTIONS_MENU_PATCH_MEMBER + ".gpu_resources"
MOD_OPTIONS_MENU_PATCH_SHA256 = "ce6229cffe8c78706a37293ac64f553becb7f3816aa0cc6fbb108d74956b68a5"
MOD_OPTIONS_MENU_BLOB_SHA256 = "ea07ab3bc87bfef067230a0aa236e1f9282cfa54e80f6fc07924bd56ab399ee3"
MOD_OPTIONS_MENU_SOURCE_SHA256 = "e03334c76d9b4b064378076a08d7e85e11e757bafa9fc0380aff5669fee13c07"
MOD_OPTIONS_MENU_LICENSE_SHA256 = "4ee36d70b08394ea8241a6615fdc892802bd8eeac5693dc907631f76afa54d11"
MOD_OPTIONS_MENU_RESOURCE_NAME = "mods/cowboybingus/mod_options_menu"
MOD_OPTIONS_MENU_RESOURCE_HASH = 0xFD50351F21814B0E
MOD_OPTIONS_MENU_RESOURCE_TYPE = 0xA14E8DFA2CD117E2


def validate_native_dll(
    dll: bytes,
    metadata: bytes,
    catalogue: dict[str, object] | None = None,
) -> tuple[int, str]:
    """核验固定原生 helper 的meta、SHA-256和PE入口布局。"""
    try:
        meta = json.loads(metadata.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("原生 helper meta 不是有效 UTF-8 JSON") from error
    if not isinstance(meta, dict):
        raise ValueError("原生 helper meta 格式无效")
    expected_catalogue_sha256 = target_languages.catalogue_sha256(catalogue)
    imports = meta.get("imports")
    if (
        type(meta.get("schema_version")) is not int
        or meta.get("schema_version") != 1
        or type(meta.get("abi_version")) is not int
        or meta.get("abi_version") != 2
        or meta.get("filename") != "hd2ct_http.dll"
        or meta.get("architecture") != "x86_64"
        or isinstance(meta.get("size"), bool)
        or not isinstance(meta.get("size"), int)
        or not isinstance(meta.get("sha256"), str)
        or len(meta.get("sha256", "")) != 64
        or any(char not in "0123456789abcdef" for char in meta.get("sha256", ""))
        or not isinstance(meta.get("target_languages_sha256"), str)
        or len(meta.get("target_languages_sha256", "")) != 64
        or any(char not in "0123456789abcdef" for char in meta.get("target_languages_sha256", ""))
        or not isinstance(imports, list)
        or any(not isinstance(item, str) or not item or len(item) > 260 for item in imports)
    ):
        raise ValueError("原生 helper meta 的ABI、架构或字段不匹配")
    if meta["target_languages_sha256"] != expected_catalogue_sha256:
        raise ValueError("原生 helper meta 的目标语言目录摘要不匹配")
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


def standalone_module_source(
    template: bytes,
    dll: bytes,
    metadata: bytes,
    catalogue: dict[str, object] | None = None,
) -> bytes:
    """将已核验的固定DLL作为hex载荷写入Lua加载器的唯一payload标记。"""
    if template.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in template:
        raise ValueError("原生Lua适配器必须是无BOM、无字节码标记且无NUL的UTF-8文本")
    template.decode("utf-8")
    if template.count(NATIVE_PAYLOAD_MARKER) != 1:
        raise ValueError("原生Lua适配器必须恰好包含一个payload标记")
    size, digest = validate_native_dll(dll, metadata, catalogue)
    if len(template) + size * 2 > MAX_SOURCE_BYTES:
        raise ValueError("DLL hex载荷将超过512 KiB Lua源码上限；请将结果交由主控评估")
    payload = (
        "local HD2CT_ABI_VERSION = 2\n"
        f"local HD2CT_DLL_SIZE = {size}\n"
        f'local HD2CT_DLL_SHA256 = "{digest}"\n'
        f'local HD2CT_DLL_HEX = "{dll.hex()}"\n'
    ).encode("ascii")
    embedded = template.replace(NATIVE_PAYLOAD_MARKER, payload, 1)
    if len(embedded) > MAX_SOURCE_BYTES:
        raise ValueError("独立原生Lua模块超过512 KiB源码上限")
    return embedded


def _lua_string_literal(value: str) -> str:
    """将UTF-8字符串编码成不受引号和反斜线影响的Lua字面量。"""
    return '"' + "".join(f"\\{byte:03d}" for byte in value.encode("utf-8")) + '"'


def target_language_settings_source(
    template: bytes,
    catalogue: dict[str, object],
    localizations: dict[str, object],
) -> bytes:
    """把目标语言目录和已校验UI文本写入菜单注册模块。"""
    if template.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in template:
        raise ValueError("目标语言设置模块必须是无BOM、无字节码标记且无NUL的UTF-8文本")
    template.decode("utf-8")
    if template.count(SETTINGS_CATALOGUE_MARKER) != 1:
        raise ValueError("目标语言设置模块必须恰好包含一个目录注入标记")
    data = target_languages.lua_catalogue_data(catalogue)
    lines = [
        "    option_id = " + _lua_string_literal(data["option_id"]) + ",",
        "    mod_id = " + _lua_string_literal(data["mod_id"]) + ",",
        f"    default_index = {data['default_index']},",
        "    menu_options = {",
    ]
    enabled = data["menu_options"]["enabled"]
    lines.extend([
        "        enabled = {",
        "            option_id = " + _lua_string_literal(enabled["option_id"]) + ",",
        "            type = " + _lua_string_literal(enabled["type"]) + ",",
        "            label = " + _lua_string_literal(enabled["label"]) + ",",
        "            default = true,",
        "            description = " + _lua_string_literal(enabled["description"]) + ",",
        "        },",
    ])
    timeout = data["menu_options"]["timeout"]
    lines.extend([
        "        timeout = {",
        "            option_id = " + _lua_string_literal(timeout["option_id"]) + ",",
        "            type = " + _lua_string_literal(timeout["type"]) + ",",
        "            label = " + _lua_string_literal(timeout["label"]) + ",",
        f"            default_index = {timeout['default_index']},",
        "            description = " + _lua_string_literal(timeout["description"]) + ",",
        "            choices = {",
    ])
    for choice in timeout["choices"]:
        lines.append(
            "                {label = %s, seconds = %d},"
            % (_lua_string_literal(choice["label"]), choice["seconds"])
        )
    lines.extend([
        "            },",
        "        },",
    ])
    outgoing_enabled = data["menu_options"]["outgoing_enabled"]
    lines.extend([
        "        outgoing_enabled = {",
        "            option_id = " + _lua_string_literal(outgoing_enabled["option_id"]) + ",",
        "            type = " + _lua_string_literal(outgoing_enabled["type"]) + ",",
        "            label = " + _lua_string_literal(outgoing_enabled["label"]) + ",",
        f"            default = {str(outgoing_enabled['default']).lower()},",
        "            description = " + _lua_string_literal(outgoing_enabled["description"]) + ",",
        "        },",
    ])
    outgoing_target = data["menu_options"]["outgoing_target"]
    lines.extend([
        "        outgoing_target = {",
        "            option_id = " + _lua_string_literal(outgoing_target["option_id"]) + ",",
        "            type = " + _lua_string_literal(outgoing_target["type"]) + ",",
        "            label = " + _lua_string_literal(outgoing_target["label"]) + ",",
        f"            default_index = {outgoing_target['default_index']},",
        "            description = " + _lua_string_literal(outgoing_target["description"]) + ",",
        "        },",
        "    },",
        "    languages = {",
    ])
    for language in data["languages"]:
        lines.append(
            "        {id = %s, label = %s},"
            % (_lua_string_literal(language["id"]), _lua_string_literal(language["label"]))
        )
    lines.extend(["    },"])
    injected = template.replace(SETTINGS_CATALOGUE_MARKER, "\n".join(lines).encode("ascii"), 1)
    if len(injected) > MAX_SOURCE_BYTES:
        raise ValueError("目标语言设置模块超过512 KiB源码上限")
    return menu_locales_source(injected, catalogue, localizations)


def menu_locales_source(
    template: bytes,
    catalogue: dict[str, object],
    localizations: dict[str, object],
) -> bytes:
    """把一次读取并校验的UI本地化快照编码为Lua纯数据表。"""
    if template.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in template:
        raise ValueError("目标语言设置模块必须是无BOM、无字节码标记且无NUL的UTF-8文本")
    template.decode("utf-8")
    if template.count(MENU_LOCALES_MARKER) != 1:
        raise ValueError("目标语言设置模块必须恰好包含一个菜单本地化注入标记")
    translations = menu_locales.validate_catalogue(localizations)
    languages = catalogue.get("languages")
    if not isinstance(languages, list) or not languages:
        raise ValueError("目标语言目录缺少有序语言列表")
    target_ids = [
        language.get("id") if isinstance(language, dict) else None
        for language in languages
    ]
    if (
        any(not isinstance(target_id, str) for target_id in target_ids)
        or len(target_ids) != len(set(target_ids))
        or set(target_ids) != set(menu_locales.TARGET_LANGUAGE_IDS)
    ):
        raise ValueError("目标语言目录ID与菜单本地化选项不匹配")

    lines: list[str] = []
    locale_rows = translations["locales"]
    for locale_tag in menu_locales.SUPPORTED_LOCALES:
        entry = locale_rows[locale_tag]
        lines.append("        [" + _lua_string_literal(locale_tag) + "] = {")
        for key in menu_locales.TEXT_KEYS:
            lines.append(
                "            " + key + " = " + _lua_string_literal(entry[key]) + ","
            )
        lines.append("            target_language_choices = {")
        target_names = entry["target_languages"]
        for index, target_id in enumerate(target_ids, start=1):
            lines.append(
                "                [%d] = %s,"
                % (index, _lua_string_literal(target_names[target_id]))
            )
        lines.extend(("            },", "        },"))
    injected = template.replace(MENU_LOCALES_MARKER, "\n".join(lines).encode("ascii"), 1)
    if len(injected) > MAX_SOURCE_BYTES:
        raise ValueError("目标语言设置模块超过512 KiB源码上限")
    return injected


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


def make_lua_resource_archive(resources: list[tuple[int, bytes]]) -> bytes:
    """构造有界Lua资源列表的archive；资源名hash必须唯一。"""
    if not isinstance(resources, list) or not resources or len(resources) > MAX_ARCHIVE_LUA_RESOURCES:
        raise ValueError("Lua资源列表为空或超过archive资源数量上限")
    hashes: set[int] = set()
    total_resource_bytes = 0
    for resource_hash_value, resource in resources:
        if (
            type(resource_hash_value) is not int
            or not 0 <= resource_hash_value <= 0xFFFFFFFFFFFFFFFF
            or resource_hash_value in hashes
        ):
            raise ValueError("archive Lua资源名hash无效或重复")
        if not isinstance(resource, bytes) or not resource:
            raise ValueError("archive Lua资源必须是非空字节串")
        if len(resource) > MAX_SOURCE_BYTES:
            raise ValueError("单个archive Lua资源超过512 KiB上限")
        hashes.add(resource_hash_value)
        total_resource_bytes += len(resource)
        if total_resource_bytes > MAX_ARCHIVE_SIZE:
            raise ValueError("archive Lua资源总大小超过构建上限")
    resources = sorted(resources, key=lambda item: item[0])

    header_size = 72
    type_record_size = 32
    entry_size = 80
    first_data_offset = (header_size + type_record_size + entry_size * len(resources) + 15) & ~15
    data_offsets: list[int] = []
    next_data_offset = first_data_offset
    for _, resource in resources:
        data_offsets.append(next_data_offset)
        next_data_offset = (next_data_offset + len(resource) + 15) & ~15
    archive_size = next_data_offset
    if archive_size > MAX_ARCHIVE_SIZE:
        raise ValueError("archive超过构建大小上限")
    archive = bytearray(archive_size)
    struct.pack_into(
        "<III20sQQ24s",
        archive,
        0,
        0xF0000011,
        1,
        len(resources),
        b"",
        archive_size,
        0,
        b"",
    )
    struct.pack_into(
        "<IIQIIII", archive, header_size, 0, 0, RESOURCE_TYPE, len(resources), 0, 16, 16
    )
    for index, ((name_hash, resource), data_offset) in enumerate(zip(resources, data_offsets)):
        entry_offset = header_size + type_record_size + entry_size * index
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
            index,
        )
        archive[data_offset : data_offset + len(resource)] = resource
    return bytes(archive)


def _verified_shared_loader_resource(patch: bytes) -> bytes:
    """校验固定v18 patch布局后返回原始resource blob。"""
    if len(patch) != 20816 or hashlib.sha256(patch).hexdigest() != SHARED_LOADER_PATCH_SHA256:
        raise ValueError("Bingus Shared Loader v18 patch大小或SHA-256不匹配")
    header = struct.unpack_from("<III20sQQ24s", patch, 0)
    if header != (0xF0000011, 1, 1, bytes(20), len(patch), 0, bytes(24)):
        raise ValueError("Bingus Shared Loader v18 archive头布局不匹配")
    type_record = struct.unpack_from("<IIQIIII", patch, 72)
    if type_record != (0, 0, RESOURCE_TYPE, 1, 0, 16, 16):
        raise ValueError("Bingus Shared Loader v18资源类型布局不匹配")
    entry = struct.unpack_from("<7Q6I", patch, 104)
    expected_entry = (
        SHARED_LOADER_RESOURCE_HASH,
        RESOURCE_TYPE,
        192,
        0,
        0,
        0,
        0,
        20620,
        0,
        0,
        16,
        16,
        0,
    )
    if entry != expected_entry:
        raise ValueError("Bingus Shared Loader v18 TOC entry布局不匹配")
    resource = patch[192 : 192 + entry[7]]
    if len(resource) != entry[7] or patch[192 + entry[7] :] != bytes(len(patch) - 192 - entry[7]):
        raise ValueError("Bingus Shared Loader v18 resource边界或填充不匹配")
    if hashlib.sha256(resource).hexdigest() != SHARED_LOADER_RESOURCE_SHA256:
        raise ValueError("Bingus Shared Loader v18 Lua resource SHA-256不匹配")
    if struct.unpack_from("<II", resource, 0) != (20612, 2) or len(resource) != 20620:
        raise ValueError("Bingus Shared Loader v18 Lua resource envelope不匹配")
    return resource


def load_shared_loader_assets(loader_zip: Path | str | None = None) -> tuple[bytes, bytes, bytes]:
    """从本机固定v18 ZIP读取白名单资源与原始说明文件，不解压其他条目。"""
    loader_zip_path = Path(loader_zip) if loader_zip is not None else SHARED_LOADER_ZIP
    try:
        zip_size = loader_zip_path.stat().st_size
        if zip_size <= 0 or zip_size > 4 * 1024 * 1024:
            raise ValueError("本机 Bingus Shared Loader v18 ZIP 超出大小上限")
        package_bytes = loader_zip_path.read_bytes()
    except OSError as error:
        raise ValueError(f"缺少 Bingus Shared Loader v18 ZIP：{loader_zip_path}") from error
    if len(package_bytes) != zip_size or hashlib.sha256(package_bytes).hexdigest() != SHARED_LOADER_ZIP_SHA256:
        raise ValueError("本机 Bingus Shared Loader v18 ZIP SHA-256不匹配")

    required_names = (
        SHARED_LOADER_PATCH_MEMBER,
        SHARED_LOADER_STREAM_MEMBER,
        SHARED_LOADER_GPU_MEMBER,
        SHARED_LOADER_README_MEMBER,
        SHARED_LOADER_MANIFEST_MEMBER,
    )
    try:
        with zipfile.ZipFile(BytesIO(package_bytes), "r") as package:
            matches: dict[str, list[zipfile.ZipInfo]] = {name: [] for name in required_names}
            for info in package.infolist():
                if info.filename in matches:
                    matches[info.filename].append(info)
            for name, infos in matches.items():
                if len(infos) != 1:
                    raise ValueError(f"Bingus Shared Loader v18 缺少或重复白名单条目：{name}")
                if infos[0].file_size > 64 * 1024:
                    raise ValueError(f"Bingus Shared Loader v18 白名单条目超过大小上限：{name}")
            patch, stream, gpu = (
                package.read(matches[name][0])
                for name in (SHARED_LOADER_PATCH_MEMBER, SHARED_LOADER_STREAM_MEMBER, SHARED_LOADER_GPU_MEMBER)
            )
            readme = package.read(matches[SHARED_LOADER_README_MEMBER][0])
            manifest = package.read(matches[SHARED_LOADER_MANIFEST_MEMBER][0])
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError("本机 Bingus Shared Loader v18 ZIP 无法安全读取") from error
    if stream or gpu:
        raise ValueError("Bingus Shared Loader v18 patch 的 stream/gpu_resources sidecar 必须为空")
    return _verified_shared_loader_resource(patch), readme, manifest


def _verified_mod_options_menu_resource(patch: bytes) -> bytes:
    """校验固定MOM v1.2 patch边界并返回未改写的Lua resource。"""
    if resource_hash(MOD_OPTIONS_MENU_RESOURCE_NAME) != MOD_OPTIONS_MENU_RESOURCE_HASH:
        raise ValueError("ModOptionsMenu v1.2资源名hash与固定值不匹配")
    if len(patch) != 139_136 or hashlib.sha256(patch).hexdigest() != MOD_OPTIONS_MENU_PATCH_SHA256:
        raise ValueError("ModOptionsMenu v1.2 patch大小或SHA-256不匹配")
    header = struct.unpack_from("<III20sQQ24s", patch, 0)
    if header != (0xF0000011, 1, 1, bytes(20), len(patch), 0, bytes(24)):
        raise ValueError("ModOptionsMenu v1.2 archive头布局不匹配")
    type_record = struct.unpack_from("<IIQIIII", patch, 72)
    if type_record != (0, 0, MOD_OPTIONS_MENU_RESOURCE_TYPE, 1, 0, 16, 16):
        raise ValueError("ModOptionsMenu v1.2资源类型或计数不匹配")
    entry = struct.unpack_from("<7Q6I", patch, 104)
    expected_entry = (
        MOD_OPTIONS_MENU_RESOURCE_HASH,
        MOD_OPTIONS_MENU_RESOURCE_TYPE,
        192,
        0,
        0,
        0,
        0,
        138_936,
        0,
        0,
        16,
        16,
        0,
    )
    if entry != expected_entry:
        raise ValueError("ModOptionsMenu v1.2 TOC entry布局不匹配")
    resource_start = entry[2]
    resource_end = resource_start + entry[7]
    if resource_end > len(patch) or patch[resource_end:] != bytes(len(patch) - resource_end):
        raise ValueError("ModOptionsMenu v1.2 resource边界或尾部填充不匹配")
    resource = patch[resource_start:resource_end]
    if hashlib.sha256(resource).hexdigest() != MOD_OPTIONS_MENU_BLOB_SHA256:
        raise ValueError("ModOptionsMenu v1.2 Lua resource SHA-256不匹配")
    if len(resource) != 138_936 or struct.unpack_from("<II", resource, 0) != (138_928, 2):
        raise ValueError("ModOptionsMenu v1.2 Lua resource envelope不匹配")
    source = resource[8:]
    if hashlib.sha256(source).hexdigest() != MOD_OPTIONS_MENU_SOURCE_SHA256:
        raise ValueError("ModOptionsMenu v1.2 Lua source SHA-256不匹配")
    try:
        source.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("ModOptionsMenu v1.2 Lua source不是有效UTF-8") from error
    return resource


def load_mod_options_menu_resource(menu_zip: Path | str | None = None) -> bytes:
    """读取固定release ZIP中的MOM patch，只校验并返回原始Lua resource。"""
    menu_zip_path = Path(menu_zip) if menu_zip is not None else MOD_OPTIONS_MENU_ZIP
    try:
        zip_size = menu_zip_path.stat().st_size
        if zip_size != MOD_OPTIONS_MENU_ZIP_SIZE or zip_size > MAX_ARCHIVE_SIZE:
            raise ValueError("ModOptionsMenu v1.2 ZIP大小不匹配或超过上限")
        package_bytes = menu_zip_path.read_bytes()
    except OSError as error:
        raise ValueError(f"缺少ModOptionsMenu v1.2 ZIP：{menu_zip_path}") from error
    if len(package_bytes) != zip_size or hashlib.sha256(package_bytes).hexdigest() != MOD_OPTIONS_MENU_ZIP_SHA256:
        raise ValueError("ModOptionsMenu v1.2 ZIP SHA-256不匹配")

    required_names = (
        MOD_OPTIONS_MENU_PATCH_MEMBER,
        MOD_OPTIONS_MENU_STREAM_MEMBER,
        MOD_OPTIONS_MENU_GPU_MEMBER,
    )
    try:
        with zipfile.ZipFile(BytesIO(package_bytes), "r") as package:
            matches: dict[str, list[zipfile.ZipInfo]] = {name: [] for name in required_names}
            for info in package.infolist():
                if info.filename in matches:
                    matches[info.filename].append(info)
            for name, infos in matches.items():
                if len(infos) != 1:
                    raise ValueError(f"ModOptionsMenu v1.2 ZIP缺少或重复白名单条目：{name}")
            if matches[MOD_OPTIONS_MENU_PATCH_MEMBER][0].file_size != 139_136:
                raise ValueError("ModOptionsMenu v1.2 patch条目长度不匹配")
            for name in (MOD_OPTIONS_MENU_STREAM_MEMBER, MOD_OPTIONS_MENU_GPU_MEMBER):
                if matches[name][0].file_size != 0:
                    raise ValueError("ModOptionsMenu v1.2 patch sidecar必须为空")
            patch = package.read(matches[MOD_OPTIONS_MENU_PATCH_MEMBER][0])
            stream = package.read(matches[MOD_OPTIONS_MENU_STREAM_MEMBER][0])
            gpu = package.read(matches[MOD_OPTIONS_MENU_GPU_MEMBER][0])
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError("ModOptionsMenu v1.2 ZIP无法安全读取") from error
    if stream or gpu:
        raise ValueError("ModOptionsMenu v1.2 patch sidecar必须为空")
    return _verified_mod_options_menu_resource(patch)


def entry_source(
    source: bytes,
    core_source: bytes,
    observer_source: bytes | None = None,
    display_test: bool = False,
    *,
    translate_source: bytes | None = None,
    translate: bool = False,
    standalone_source: bytes | None = None,
    settings_source: bytes | None = None,
    standalone: bool = False,
) -> bytes:
    if sum((bool(display_test), bool(translate), bool(standalone))) > 1:
        raise ValueError("固定显示测试和翻译模式互斥")
    sources = [source, core_source]
    if observer_source is not None:
        sources.append(observer_source)
    if translate_source is not None:
        sources.append(translate_source)
    if standalone_source is not None:
        sources.append(standalone_source)
    if settings_source is not None:
        sources.append(settings_source)
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
    if settings_source is not None:
        if settings_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in settings_source:
            raise ValueError("目标语言设置必须是无BOM、无字节码标记的UTF-8文本")
        settings_source.decode("utf-8")
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
    if source.count(SETTINGS_MARKER) != 1:
        raise ValueError("入口必须恰好包含一个目标语言设置标记")
    if standalone and (standalone_marker_count != 1 or standalone_flag_count != 1):
        raise ValueError("独立翻译模式要求入口恰好包含一个模块标记和默认关闭标记")
    if standalone and (standalone_source is None or observer_source is None):
        raise ValueError("独立翻译模式必须嵌入原生网络模块和只读适配器")
    if standalone and settings_source is None:
        raise ValueError("独立翻译模式必须嵌入目标语言设置模块")
    if standalone and translate_source is None:
        raise ValueError("独立翻译模式必须嵌入翻译核心")
    if standalone_source is not None and not standalone:
        raise ValueError("未启用独立模式时不能注入原生网络模块")
    if settings_source is not None and not standalone:
        raise ValueError("未启用独立模式时不能注入目标语言设置模块")
    if translate and standalone:
        raise ValueError("两种翻译模式互斥")
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
    embedded = embedded.replace(
        SETTINGS_MARKER,
        (settings_source if standalone else b"return nil").rstrip() + b"\n",
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


def addon_files(
    entry: bytes,
    *,
    loader_zip: Path | str | None = None,
    menu_zip: Path | str | None = None,
) -> dict[str, bytes]:
    resource = struct.pack("<II", len(entry), 2) + entry
    loader_resource, loader_readme, loader_manifest = load_shared_loader_assets(loader_zip)
    menu_resource = load_mod_options_menu_resource(menu_zip)
    archive = make_lua_resource_archive(
        [
            (SHARED_LOADER_RESOURCE_HASH, loader_resource),
            (resource_hash(RESOURCE_NAME), resource),
            (MOD_OPTIONS_MENU_RESOURCE_HASH, menu_resource),
        ]
    )
    description = (
        "进程内聊天翻译：通过游戏内原生网络线程将聊天交给配置的翻译服务，"
        "收到消息时本地显示原文、换行和“译文：”加译文；开启发送前翻译后按独立目标语言发送，失败或超时发送原文。"
        "读取 HD2CT_API_URL、HD2CT_MODEL、HD2CT_API_KEY 和 HD2CT_APP_ID 环境变量；"
        "启用状态与请求超时由游戏内菜单控制。"
        "内置 Bingus Shared Loader v18，无需另外导入。Arsenal 默认优先级请放在列表最底；"
        "启用 first-mod-wins 时请放在列表最顶。"
    )
    manifest = {
        "Version": 1,
        "Guid": str(uuid.UUID(ADDON_GUID)),
        "Name": "HD2 Chat Translate Standalone",
        "Description": description,
        "Options": [
            {
                "Name": "HD2 Chat Translate Standalone",
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
    try:
        files["LICENSE"] = PROJECT_LICENSE.read_bytes()
    except OSError as error:
        raise ValueError("安装包缺少项目 GPLv3 许可证原文") from error
    try:
        files["LICENSES/cJSON-LICENSE.txt"] = STANDALONE_LICENSE.read_bytes()
    except OSError as error:
        raise ValueError("standalone包缺少vendor/cJSON许可证原文") from error
    try:
        menu_license = MOD_OPTIONS_MENU_LICENSE.read_bytes()
        menu_source_note = MOD_OPTIONS_MENU_SOURCE.read_bytes()
    except OSError as error:
        raise ValueError("standalone包缺少ModOptionsMenu许可证或来源说明") from error
    if len(menu_license) != 674 or hashlib.sha256(menu_license).hexdigest() != MOD_OPTIONS_MENU_LICENSE_SHA256:
        raise ValueError("ModOptionsMenu许可证字节与固定上游原文不匹配")
    loader_zip_path = Path(loader_zip) if loader_zip is not None else SHARED_LOADER_ZIP
    loader_input_note = (
        "artifacts/Bingus-Shared-Loader-v18.zip"
        if _same_path(loader_zip_path, SHARED_LOADER_ZIP)
        else "命令行 --loader-zip 指定的 ZIP"
    )
    source_note = (
        "Bingus Shared Loader v18 来源说明\n\n"
        "上游项目：https://github.com/CowboyBingus/BingusSharedLoader\n"
        f"本机构建输入：{loader_input_note}\n"
        f"输入 ZIP SHA-256：{SHARED_LOADER_ZIP_SHA256}\n\n"
        "本包从用户本机已有的上游 ZIP 中读取固定白名单条目。Loader Lua resource 在合并进本插件 patch 时保持原始字节。\n"
        "随包保留的 README 和 manifest 是上游文件原文。本说明不为 Bingus Shared Loader 声明或新增许可证。\n"
    ).encode("utf-8")
    files["LICENSES/BingusSharedLoader-README.txt"] = loader_readme
    files["LICENSES/BingusSharedLoader-manifest.json"] = loader_manifest
    files["LICENSES/BingusSharedLoader-SOURCE.txt"] = source_note
    files["LICENSES/ModOptionsMenu-LICENSE.txt"] = menu_license
    files["LICENSES/ModOptionsMenu-SOURCE.txt"] = menu_source_note
    return files


def _same_path(left: Path, right: Path) -> bool:
    """按本机路径规则比较绝对路径，避免大小写或相对路径绕过保护。"""
    return os.path.normcase(str(left.expanduser().resolve())) == os.path.normcase(
        str(right.expanduser().resolve())
    )


def default_output_path(now: datetime | None = None) -> Path:
    """返回北京时间命名的默认交付包路径。"""
    moment = now or datetime.now(BEIJING_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        moment = moment.astimezone(BEIJING_TIMEZONE)
    return ROOT / "artifacts" / f"{DELIVERY_PREFIX}{moment:%Y%m%d%H%M%S}.zip"


def is_delivery_filename(filename: str) -> bool:
    """检查命令行交付包名称及其中的日期时间是否有效。"""
    match = DELIVERY_NAME_PATTERN.fullmatch(filename)
    if match is None:
        return False
    try:
        datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def _select_output_path(
    output: Path | str | None,
    *,
    now: datetime | None = None,
) -> Path:
    """CLI默认使用时间戳名称；内部测试可传入临时输出路径。"""
    return Path(output) if output is not None else default_output_path(now)


def build_artifact(
    output: Path | str | None = None,
    *,
    loader_zip: Path | str | None = None,
    menu_zip: Path | str | None = None,
    native_dll: Path | str | None = None,
    native_meta: Path | str | None = None,
) -> Path:
    entry_path = ROOT / "game" / "chat_probe.lua"
    core_path = ROOT / "game" / "chat_probe_core.lua"
    observer_path = ROOT / "game" / "chat_observe_core.lua"
    translate_path = ROOT / "game" / "chat_translate_core.lua"
    standalone_module_path = ROOT / "game" / "chat_http_native.lua"
    settings_path = ROOT / "game" / "settings.lua"
    dll_path = Path(native_dll) if native_dll is not None else STANDALONE_DLL
    meta_path = Path(native_meta) if native_meta is not None else STANDALONE_META
    loader_zip_path = Path(loader_zip) if loader_zip is not None else SHARED_LOADER_ZIP
    menu_zip_path = Path(menu_zip) if menu_zip is not None else MOD_OPTIONS_MENU_ZIP
    catalogue = target_languages.load_catalogue()
    localizations = menu_locales.load_catalogue()
    output_path = _select_output_path(output)
    if output is None and os.path.lexists(output_path):
        raise ValueError(
            f"默认交付包已存在，拒绝覆盖：{output_path.name}。"
            "请等待下一秒重新构建，或通过 --output 指定新的时间戳文件名。"
        )
    source_paths = (
        entry_path, core_path, observer_path, translate_path, standalone_module_path,
        settings_path, dll_path, meta_path, STANDALONE_LICENSE, PROJECT_LICENSE,
        MOD_OPTIONS_MENU_LICENSE, MOD_OPTIONS_MENU_SOURCE,
        target_languages.CATALOGUE_PATH, loader_zip_path, menu_zip_path,
        menu_locales.CATALOGUE_PATH,
    )
    if any(_same_path(output_path, path) for path in source_paths):
        raise ValueError("输出不能覆盖构建输入文件")
    if not dll_path.is_file() or not meta_path.is_file():
        raise ValueError(
            f"缺少原生 helper DLL 或配套meta：{dll_path} / {meta_path}；"
            "请先构建原生 helper，package builder不会自动编译或下载。"
        )
    if not STANDALONE_LICENSE.is_file():
        raise ValueError("缺少 native/vendor/cjson/LICENSE")
    if not PROJECT_LICENSE.is_file():
        raise ValueError("缺少项目 LICENSE")
    if not MOD_OPTIONS_MENU_LICENSE.is_file() or not MOD_OPTIONS_MENU_SOURCE.is_file():
        raise ValueError("缺少ModOptionsMenu许可证或来源说明")
    native_module = standalone_module_source(
        standalone_module_path.read_bytes(),
        dll_path.read_bytes(),
        meta_path.read_bytes(),
        catalogue,
    )
    settings_module = target_language_settings_source(
        settings_path.read_bytes(),
        catalogue,
        localizations,
    )
    packaged_entry = entry_source(
        entry_path.read_bytes(),
        core_path.read_bytes(),
        observer_path.read_bytes(),
        translate_source=translate_path.read_bytes(),
        standalone_source=native_module,
        settings_source=settings_module,
        standalone=True,
    )
    files = addon_files(packaged_entry, loader_zip=loader_zip_path, menu_zip=menu_zip_path)
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
        help="显式指定ZIP路径；文件名须为HD2ChatTranslateYYYYMMDDHHMMSS.zip。",
    )
    parser.add_argument("--loader-zip", type=Path, help="Bingus Shared Loader v18来源ZIP；默认使用项目artifacts路径。")
    parser.add_argument("--menu-zip", type=Path, help="ModOptionsMenu v1.2来源ZIP；默认使用项目artifacts路径。")
    parser.add_argument("--native-dll", type=Path, help="原生HTTP helper DLL；默认使用项目artifacts/native路径。")
    parser.add_argument("--native-meta", type=Path, help="原生HTTP helper meta JSON；默认使用项目artifacts/native路径。")
    args = parser.parse_args()
    if args.output is not None and not is_delivery_filename(args.output.name):
        parser.error("CLI交付文件名必须为 HD2ChatTranslateYYYYMMDDHHMMSS.zip，时间使用北京时间")
    try:
        result = build_artifact(
            args.output,
            loader_zip=args.loader_zip,
            menu_zip=args.menu_zip,
            native_dll=args.native_dll,
            native_meta=args.native_meta,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建HD2聊天插件ZIP：{result}")


if __name__ == "__main__":
    main()
