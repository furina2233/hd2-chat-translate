"""校验共享目标语言目录并生成原生客户端使用的C表。"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PATH = ROOT / "resources" / "target_languages.json"
GENERATED_HEADER_NAME = "target_languages.generated.h"

_ROOT_KEYS = {
    "schema_version", "option_id", "mod_id", "default_index", "menu_options", "languages"
}
_LANGUAGE_KEYS = {"id", "label", "ai_target", "google", "baidu", "youdao"}
_MENU_OPTIONS_KEYS = {"enabled", "timeout"}
_ENABLED_OPTION_KEYS = {"option_id", "type", "label", "default", "description"}
_TIMEOUT_OPTION_KEYS = {
    "option_id", "type", "label", "default_index", "description", "choices"
}
_TIMEOUT_CHOICE_KEYS = {"label", "seconds"}
_OPTION_ID = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")
_MOD_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_LANGUAGE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SERVICE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_LABEL_MAX_CHARACTERS = 48
_FIELD_ORDER = ("id", "label", "ai_target", "google", "baidu", "youdao")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝JSON对象中的重复键，避免解析器静默覆盖目录数据。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON包含重复键：{key}")
        result[key] = value
    return result


def _plain_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field}必须是非空且无首尾空白的字符串")
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise ValueError(f"{field}不能包含控制字符")
    return value


def validate_catalogue(value: Any) -> dict[str, Any]:
    """验证语言目录、菜单设置项、稳定标识、默认项与服务语言码。"""
    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise ValueError("共享菜单目录顶层字段不符合schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("共享菜单目录schema_version必须为2")

    option_id = _plain_text(value["option_id"], "option_id")
    if not _OPTION_ID.fullmatch(option_id):
        raise ValueError("option_id格式无效")
    mod_id = _plain_text(value["mod_id"], "mod_id")
    if not _MOD_ID.fullmatch(mod_id):
        raise ValueError("mod_id格式无效")

    menu_options = value["menu_options"]
    if not isinstance(menu_options, dict) or set(menu_options) != _MENU_OPTIONS_KEYS:
        raise ValueError("menu_options必须包含enabled与timeout")
    enabled = menu_options["enabled"]
    if not isinstance(enabled, dict) or set(enabled) != _ENABLED_OPTION_KEYS:
        raise ValueError("enabled菜单项字段不符合schema")
    enabled_id = _plain_text(enabled["option_id"], "menu_options.enabled.option_id")
    if not _OPTION_ID.fullmatch(enabled_id):
        raise ValueError("enabled菜单项option_id格式无效")
    enabled_label = _plain_text(enabled["label"], "menu_options.enabled.label")
    if len(enabled_label) > _LABEL_MAX_CHARACTERS:
        raise ValueError("enabled菜单项label超过48字符")
    enabled_description = _plain_text(
        enabled["description"], "menu_options.enabled.description")
    if (enabled["type"] != "toggle" or type(enabled["default"]) is not bool
            or not enabled["default"]):
        raise ValueError("enabled菜单项必须是默认开启的toggle")

    timeout = menu_options["timeout"]
    if not isinstance(timeout, dict) or set(timeout) != _TIMEOUT_OPTION_KEYS:
        raise ValueError("timeout菜单项字段不符合schema")
    timeout_id = _plain_text(timeout["option_id"], "menu_options.timeout.option_id")
    if not _OPTION_ID.fullmatch(timeout_id):
        raise ValueError("timeout菜单项option_id格式无效")
    timeout_label = _plain_text(timeout["label"], "menu_options.timeout.label")
    if len(timeout_label) > _LABEL_MAX_CHARACTERS:
        raise ValueError("timeout菜单项label超过48字符")
    timeout_description = _plain_text(
        timeout["description"], "menu_options.timeout.description")
    choices = timeout["choices"]
    expected_seconds = [10, 20, 30]
    expected_labels = ["10秒", "20秒", "30秒"]
    if (timeout["type"] != "choice" or type(timeout["default_index"]) is not int
            or timeout["default_index"] != 2 or not isinstance(choices, list)
            or len(choices) != len(expected_seconds)):
        raise ValueError("timeout菜单项必须是默认索引2的三项choice")
    normalized_choices: list[dict[str, Any]] = []
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict) or set(choice) != _TIMEOUT_CHOICE_KEYS:
            raise ValueError(f"timeout.choices[{index + 1}]字段不符合schema")
        label = _plain_text(choice["label"], f"timeout.choices[{index + 1}].label")
        seconds = choice["seconds"]
        if (type(seconds) is not int or seconds != expected_seconds[index]
                or label != expected_labels[index]):
            raise ValueError("timeout choices必须依序为10、20、30秒")
        normalized_choices.append({"label": label, "seconds": seconds})
    if len({option_id, enabled_id, timeout_id}) != 3:
        raise ValueError("菜单option_id必须互不重复")
    normalized_menu_options = {
        "enabled": {
            "option_id": enabled_id,
            "type": "toggle",
            "label": enabled_label,
            "default": True,
            "description": enabled_description,
        },
        "timeout": {
            "option_id": timeout_id,
            "type": "choice",
            "label": timeout_label,
            "default_index": 2,
            "description": timeout_description,
            "choices": normalized_choices,
        },
    }

    languages = value["languages"]
    if not isinstance(languages, list) or not 2 <= len(languages) <= 16:
        raise ValueError("目标语言数量必须在2到16项之间")
    default_index = value["default_index"]
    if (type(default_index) is not int or default_index < 1 or
            default_index > len(languages)):
        raise ValueError("default_index必须是有效的1起始语言索引")

    ids: set[str] = set()
    labels: set[str] = set()
    rows: list[dict[str, str]] = []
    for position, row in enumerate(languages, start=1):
        if not isinstance(row, dict) or set(row) != _LANGUAGE_KEYS:
            raise ValueError(f"第{position}项目标语言字段不符合schema")
        language_id = _plain_text(row["id"], f"languages[{position}].id")
        if not _LANGUAGE_ID.fullmatch(language_id) or language_id in ids:
            raise ValueError(f"第{position}项目标语言id无效或重复")
        ids.add(language_id)

        label = _plain_text(row["label"], f"languages[{position}].label")
        if len(label) > _LABEL_MAX_CHARACTERS:
            raise ValueError(f"第{position}项目标语言label超过48字符")
        if label in labels:
            raise ValueError(f"第{position}项目标语言label重复")
        labels.add(label)

        ai_target = _plain_text(row["ai_target"], f"languages[{position}].ai_target")
        for service in ("google", "baidu", "youdao"):
            code = _plain_text(row[service], f"languages[{position}].{service}")
            if not _SERVICE_CODE.fullmatch(code):
                raise ValueError(f"第{position}项目标语言{service}语言码无效")
        rows.append({key: row[key] for key in _FIELD_ORDER})

    return {
        "schema_version": 2,
        "option_id": option_id,
        "mod_id": mod_id,
        "default_index": default_index,
        "menu_options": normalized_menu_options,
        "languages": rows,
    }


def load_catalogue(path: Path | str = CATALOGUE_PATH) -> dict[str, Any]:
    """读取并严格校验共享目标语言JSON。"""
    catalogue_path = Path(path)
    try:
        raw = json.loads(
            catalogue_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取目标语言目录：{catalogue_path}") from error
    return validate_catalogue(raw)


def lua_catalogue_data(catalogue: dict[str, Any] | None = None) -> dict[str, Any]:
    """为Lua菜单注册与原生MOM读取提供同一份已校验设置定义。"""
    source = validate_catalogue(catalogue) if catalogue is not None else load_catalogue()
    return {
        "option_id": source["option_id"],
        "mod_id": source["mod_id"],
        "default_index": source["default_index"],
        "menu_options": source["menu_options"],
        "languages": [
            {"id": row["id"], "label": row["label"]}
            for row in source["languages"]
        ],
    }


def catalogue_sha256(catalogue: dict[str, Any] | None = None) -> str:
    """返回规范化目录内容的SHA256，不受JSON空白或对象键顺序影响。"""
    source = validate_catalogue(catalogue) if catalogue is not None else load_catalogue()
    normalized = {
        "schema_version": 2,
        "option_id": source["option_id"],
        "mod_id": source["mod_id"],
        "default_index": source["default_index"],
        "menu_options": source["menu_options"],
        "languages": [
            {key: row[key] for key in _FIELD_ORDER}
            for row in source["languages"]
        ],
    }
    canonical = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _c_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def c_header_text(catalogue: dict[str, Any] | None = None) -> str:
    """将已校验目录编码为UTF-8静态C表。"""
    source = validate_catalogue(catalogue) if catalogue is not None else load_catalogue()
    rows = source["languages"]
    lines = [
        "#ifndef HD2CT_GENERATED_TARGET_LANGUAGES_H",
        "#define HD2CT_GENERATED_TARGET_LANGUAGES_H",
        "",
        "/* 此文件由 tools/target_languages.py 生成，不要手工编辑。 */",
        f"#define HD2CT_TARGET_LANGUAGE_COUNT {len(rows)}u",
        f"#define HD2CT_DEFAULT_TARGET_LANGUAGE {source['default_index']}u",
        f"#define HD2CT_TARGET_LANGUAGE_OPTION_ID {_c_string(source['option_id'])}",
        f"#define HD2CT_ENABLED_OPTION_ID {_c_string(source['menu_options']['enabled']['option_id'])}",
        "#define HD2CT_ENABLED_DEFAULT 1u",
        f"#define HD2CT_TIMEOUT_OPTION_ID {_c_string(source['menu_options']['timeout']['option_id'])}",
        f"#define HD2CT_DEFAULT_TIMEOUT_INDEX {source['menu_options']['timeout']['default_index']}u",
        f"#define HD2CT_TIMEOUT_CHOICE_COUNT {len(source['menu_options']['timeout']['choices'])}u",
        f"#define HD2CT_DEFAULT_TIMEOUT_SECONDS {source['menu_options']['timeout']['choices'][source['menu_options']['timeout']['default_index'] - 1]['seconds']}u",
        "",
        "static const uint32_t g_hd2ct_timeout_seconds[HD2CT_TIMEOUT_CHOICE_COUNT] = {",
        "    " + ", ".join(
            f"{choice['seconds']}u" for choice in source["menu_options"]["timeout"]["choices"]
        ),
        "};",
        "",
        "static const HD2CT_TargetLanguage g_hd2ct_target_languages[HD2CT_TARGET_LANGUAGE_COUNT] = {",
    ]
    for row in rows:
        is_chinese = 1 if row["id"] in ("zh_cn", "zh_tw") else 0
        fields = (
            _c_string(row["id"]),
            _c_string(row["label"]),
            _c_string(row["ai_target"]),
            _c_string(row["google"]),
            _c_string(row["baidu"]),
            _c_string(row["youdao"]),
        )
        lines.append("    {%s, %s, %s, %s, %s, %s, %du}," % (*fields, is_chinese))
    lines.extend(["};", "", "#endif", ""])
    return "\n".join(lines)


def generate_c_header(output_dir: Path | str,
                      catalogue: dict[str, Any] | None = None) -> Path:
    """在指定构建输出目录生成原生适配器所需的静态表头。"""
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / GENERATED_HEADER_NAME
    destination.write_text(c_header_text(catalogue), encoding="utf-8", newline="\n")
    return destination
