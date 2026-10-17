"""从源码构建 HD2 Chat Translate 的 standalone Arsenal ZIP。"""

# 格式依据公开实现核对：https://github.com/CowboyBingus/BingusSharedLoader/tree/main/scripts

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import uuid
import zipfile
from scripts import menu_locales, target_languages

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
DELIVERY_NAME_PATTERN = re.compile(
    r"^HD2ChatTranslate_([0-9][A-Za-z0-9._+\-]{0,63})_([0-9]{14})\.zip$",
    re.ASCII,
)
VERSION_FILE = ROOT / "VERSION"
STANDALONE_DLL = ROOT / "artifacts" / "native" / "hd2ct_http.dll"
STANDALONE_META = ROOT / "artifacts" / "native" / "hd2ct_http.meta.json"
STANDALONE_LICENSE = ROOT / "native" / "vendor" / "cjson" / "LICENSE"
PROJECT_LICENSE = ROOT / "LICENSE"


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
        or meta.get("abi_version") != 4
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
        "local HD2CT_ABI_VERSION = 4\n"
        f"local HD2CT_DLL_SIZE = {size}\n"
        f'local HD2CT_DLL_SHA256 = "{digest}"\n'
        f'local HD2CT_DLL_HEX = "{dll.hex()}"\n'
    ).encode("ascii")
    embedded = template.replace(NATIVE_PAYLOAD_MARKER, payload, 1)
    if len(embedded) > MAX_SOURCE_BYTES:
        raise ValueError("独立原生Lua模块超过512 KiB源码上限")
    return embedded


def _lua_string_literal(value: str) -> str:
    """生成仅转义语法字符与ASCII控制字符的Lua字符串字面量。"""
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == '"':
            escaped.append('\\"')
        elif character == "\\":
            escaped.append("\\\\")
        elif codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\{codepoint:03d}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


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
    ignore_self = data["menu_options"]["ignore_self"]
    lines.extend([
        "        ignore_self = {",
        "            option_id = " + _lua_string_literal(ignore_self["option_id"]) + ",",
        "            type = " + _lua_string_literal(ignore_self["type"]) + ",",
        "            label = " + _lua_string_literal(ignore_self["label"]) + ",",
        "            default = true,",
        "            description = " + _lua_string_literal(ignore_self["description"]) + ",",
        "        },",
    ])
    enemy_slang = data["menu_options"]["enemy_slang"]
    lines.extend([
        "        enemy_slang = {",
        "            option_id = " + _lua_string_literal(enemy_slang["option_id"]) + ",",
        "            type = " + _lua_string_literal(enemy_slang["type"]) + ",",
        "            label = " + _lua_string_literal(enemy_slang["label"]) + ",",
        "            default = true,",
        "            description = " + _lua_string_literal(enemy_slang["description"]) + ",",
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
    interface_type = data["menu_options"]["interface_type"]
    lines.extend([
        "        interface_type = {",
        "            option_id = " + _lua_string_literal(interface_type["option_id"]) + ",",
        "            type = " + _lua_string_literal(interface_type["type"]) + ",",
        "            label = " + _lua_string_literal(interface_type["label"]) + ",",
        f"            default_index = {interface_type['default_index']},",
        "            description = " + _lua_string_literal(interface_type["description"]) + ",",
        "            choices = {",
    ])
    for choice in interface_type["choices"]:
        lines.append(
            "                {id = %s, label = %s},"
            % (_lua_string_literal(choice["id"]), _lua_string_literal(choice["label"]))
        )
    lines.extend([
        "            },",
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
    injected = template.replace(SETTINGS_CATALOGUE_MARKER, "\n".join(lines).encode("utf-8"), 1)
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
    menu_options = catalogue.get("menu_options")
    interface_type = (
        menu_options.get("interface_type") if isinstance(menu_options, dict) else None
    )
    interface_choices = (
        interface_type.get("choices") if isinstance(interface_type, dict) else None
    )
    interface_ids = [
        choice.get("id") if isinstance(choice, dict) else None
        for choice in interface_choices
    ] if isinstance(interface_choices, list) else []
    if interface_ids != list(menu_locales.INTERFACE_TYPE_IDS):
        raise ValueError("接口类型目录ID与菜单本地化选项不匹配")

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
        lines.append("            },")
        lines.append("            interface_type_choices = {")
        interface_names = entry["interface_types"]
        for index, interface_id in enumerate(interface_ids, start=1):
            lines.append(
                "                [%d] = %s,"
                % (index, _lua_string_literal(interface_names[interface_id]))
            )
        lines.extend(("            },", "        },"))
    injected = template.replace(MENU_LOCALES_MARKER, "\n".join(lines).encode("utf-8"), 1)
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


def _read_common_license_files() -> dict[str, bytes]:
    """读取插件包需要的项目 GPL 与 cJSON 许可。"""
    try:
        project_license = PROJECT_LICENSE.read_bytes()
    except OSError as error:
        raise ValueError("安装包缺少项目 GPLv3 许可证原文") from error
    try:
        cjson_license = STANDALONE_LICENSE.read_bytes()
    except OSError as error:
        raise ValueError("standalone包缺少vendor/cJSON许可证原文") from error
    return {
        "LICENSE": project_license,
        "LICENSES/cJSON-LICENSE.txt": cjson_license,
    }


def _manifest_description() -> str:
    return (
        "进程内聊天翻译：通过游戏内原生网络线程将聊天交给配置的翻译服务，"
        "翻译成功后显示原文、换行和“译文：”加译文；服务判断无需回写时保持原文，请求失败显示简短提示，不广播译文。"
        "读取 HD2CT_API_URL、HD2CT_MODEL、HD2CT_API_KEY 和 HD2CT_APP_ID 环境变量；"
        "启用状态与请求超时由游戏内菜单控制。"
        "Arsenal 默认优先级请放在列表最底；启用 first-mod-wins 时请放在列表最顶。"
    )


def _assemble_addon_files(entry: bytes, *, common_licenses: dict[str, bytes]) -> dict[str, bytes]:
    resource = struct.pack("<II", len(entry), 2) + entry
    archive = make_lua_resource_archive([(resource_hash(RESOURCE_NAME), resource)])
    description = _manifest_description()
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
    return {
        "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "Addon/" + ARCHIVE_NAME: archive,
        "Addon/" + ARCHIVE_NAME + ".stream": b"",
        "Addon/" + ARCHIVE_NAME + ".gpu_resources": b"",
        **common_licenses,
    }


def addon_files(entry: bytes) -> dict[str, bytes]:
    return _assemble_addon_files(entry, common_licenses=_read_common_license_files())


def _same_path(left: Path, right: Path) -> bool:
    """按本机路径规则比较绝对路径，避免大小写或相对路径绕过保护。"""
    return os.path.normcase(str(left.expanduser().resolve())) == os.path.normcase(
        str(right.expanduser().resolve())
    )


def _beijing_moment(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(BEIJING_TIMEZONE)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=BEIJING_TIMEZONE)
    return moment.astimezone(BEIJING_TIMEZONE)


def _validate_version(version: str) -> str:
    if (
        not isinstance(version, str)
        or len(version) > 64
        or ".." in version
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9](?:[A-Za-z0-9._+\-]*[A-Za-z0-9])?)?", version, re.ASCII) is None
    ):
        raise ValueError("VERSION 必须是有效版本号（最多64字符，不能包含路径或空白）")
    return version


def read_version(version_path: Path | str | None = None) -> str:
    """严格读取根目录 VERSION 的单行版本号。"""
    path = Path(version_path) if version_path is not None else VERSION_FILE
    try:
        value = path.read_bytes().decode("utf-8", errors="strict")
    except OSError as error:
        raise ValueError(f"无法读取版本文件 VERSION：{path}") from error
    except UnicodeError as error:
        raise ValueError(f"版本文件 VERSION 不是有效 UTF-8：{path}") from error
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if "\r" in value or "\n" in value:
        raise ValueError("VERSION 必须只包含一行版本号")
    return _validate_version(value)


def _delivery_filename(version: str, moment: datetime) -> str:
    return f"{DELIVERY_PREFIX}_{_validate_version(version)}_{moment:%Y%m%d%H%M%S}.zip"


def default_output_path(
    now: datetime | None = None,
    *,
    output_dir: Path | str | None = None,
    version_path: Path | str | None = None,
) -> Path:
    """返回版本号与北京时间命名的单包路径。"""
    directory = Path(output_dir) if output_dir is not None else ROOT / "artifacts"
    return directory / _delivery_filename(read_version(version_path), _beijing_moment(now))


def _delivery_filename_match(filename: str) -> re.Match[str] | None:
    match = DELIVERY_NAME_PATTERN.fullmatch(filename)
    if match is None:
        return None
    try:
        _validate_version(match.group(1))
        datetime.strptime(match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return match


def is_delivery_filename(filename: str) -> bool:
    """检查交付文件名中的版本号与日历时间。"""
    return _delivery_filename_match(filename) is not None

def _build_input_paths(dll_path: Path, meta_path: Path, version_path: Path) -> tuple[Path, ...]:
    """列出构建读取的全部文件，供输出覆盖保护使用。"""
    return (
        ROOT / "game" / "chat_probe.lua",
        ROOT / "game" / "chat_probe_core.lua",
        ROOT / "game" / "research" / "chat_observe_core.lua",
        ROOT / "game" / "chat_translate_core.lua",
        ROOT / "game" / "chat_http_native.lua",
        ROOT / "game" / "settings.lua",
        dll_path,
        meta_path,
        version_path,
        STANDALONE_LICENSE,
        PROJECT_LICENSE,
        target_languages.CATALOGUE_PATH,
        menu_locales.CATALOGUE_PATH,
    )


def _preflight_output(output_path: Path, input_paths: tuple[Path, ...]) -> None:
    if any(_same_path(output_path, source) for source in input_paths):
        raise ValueError("输出不能覆盖构建输入文件")
    if os.path.lexists(output_path):
        raise ValueError(f"交付包已存在，拒绝覆盖：{output_path.name}")


def _build_packaged_entry(dll_path: Path, meta_path: Path) -> bytes:
    entry_path = ROOT / "game" / "chat_probe.lua"
    core_path = ROOT / "game" / "chat_probe_core.lua"
    observer_path = ROOT / "game" / "research" / "chat_observe_core.lua"
    translate_path = ROOT / "game" / "chat_translate_core.lua"
    standalone_module_path = ROOT / "game" / "chat_http_native.lua"
    settings_path = ROOT / "game" / "settings.lua"
    if not dll_path.is_file() or not meta_path.is_file():
        raise ValueError(
            f"缺少原生 helper DLL 或配套meta：{dll_path} / {meta_path}；"
            "请先构建原生 helper，package builder不会自动编译或下载。"
        )
    catalogue = target_languages.load_catalogue()
    localizations = menu_locales.load_catalogue()
    dll = dll_path.read_bytes()
    metadata = meta_path.read_bytes()
    native_module = standalone_module_source(
        standalone_module_path.read_bytes(), dll, metadata, catalogue,
    )
    settings_module = target_language_settings_source(
        settings_path.read_bytes(), catalogue, localizations
    )
    return entry_source(
        entry_path.read_bytes(),
        core_path.read_bytes(),
        observer_path.read_bytes(),
        translate_source=translate_path.read_bytes(),
        standalone_source=native_module,
        settings_source=settings_module,
        standalone=True,
    )


def _write_archive(file_object, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(file_object, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)


def _write_artifact(path: Path, files: dict[str, bytes]) -> Path:
    """独占创建ZIP；写入失败时仅清理本次创建的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            _write_archive(handle, files)
    except BaseException:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return path


def build_artifact(
    output_dir: Path | str | None = None,
    *,
    native_dll: Path | str | None = None,
    native_meta: Path | str | None = None,
    now: datetime | None = None,
    version_path: Path | str | None = None,
) -> Path:
    """构建唯一的 standalone Arsenal 安装包。"""
    dll_path = Path(native_dll) if native_dll is not None else STANDALONE_DLL
    meta_path = Path(native_meta) if native_meta is not None else STANDALONE_META
    version_file = Path(version_path) if version_path is not None else VERSION_FILE
    output_path = default_output_path(
        now, output_dir=output_dir, version_path=version_file,
    )
    input_paths = _build_input_paths(dll_path, meta_path, version_file)
    _preflight_output(output_path, input_paths)
    packaged_entry = _build_packaged_entry(dll_path, meta_path)
    files = addon_files(packaged_entry)
    return _write_artifact(output_path, files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        help="ZIP输出目录；默认使用项目artifacts目录。",
    )
    parser.add_argument("--native-dll", type=Path, help="原生HTTP helper DLL；默认使用项目artifacts/native路径。")
    parser.add_argument("--native-meta", type=Path, help="原生HTTP helper meta JSON；默认使用项目artifacts/native路径。")
    args = parser.parse_args(argv)
    try:
        output = build_artifact(
            output_dir=args.output_dir,
            native_dll=args.native_dll,
            native_meta=args.native_meta,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建HD2聊天插件ZIP：{output}")
    return 0


if __name__ == "__main__":
    main()
