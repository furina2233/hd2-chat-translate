"""读取并严格校验游戏菜单使用的静态本地化目录。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PATH = ROOT / "resources" / "menu_locales.json"
MAX_CATALOGUE_BYTES = 256 * 1024

SUPPORTED_LOCALES = (
    "en",
    "en-GB",
    "fr",
    "it",
    "de",
    "es",
    "es-419",
    "ja",
    "ko",
    "pt-BR",
    "pt",
    "pl",
    "ru",
    "zh-Hans",
    "zh-Hant",
)
TARGET_LANGUAGE_IDS = (
    "zh_cn",
    "zh_tw",
    "en",
    "ja",
    "ko",
    "fr",
    "de",
    "es",
    "pt",
    "it",
)
TEXT_KEYS = (
    "target_language_label",
    "target_language_description",
    "enabled_label",
    "enabled_description",
    "timeout_label",
    "timeout_description",
    "timeout_choice_format",
)
LOCALE_KEYS = frozenset((*TEXT_KEYS, "target_languages"))
LABEL_KEYS = frozenset(("target_language_label", "enabled_label", "timeout_label"))
DESCRIPTION_KEYS = frozenset(
    ("target_language_description", "enabled_description", "timeout_description")
)
MAX_LABEL_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 400
MAX_CHOICE_LENGTH = 48
_DANGEROUS_MARKUP = re.compile(r"#[A-Z][A-Z0-9_]*|[<>]", re.ASCII)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"菜单语言目录包含重复JSON键：{key}")
        output[key] = value
    return output


def _require_exact_keys(value: object, expected: frozenset[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"菜单语言目录的{name}必须是对象")
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("缺少 " + ", ".join(sorted(missing)))
        if extra:
            details.append("未知 " + ", ".join(sorted(extra)))
        raise ValueError(f"菜单语言目录的{name}字段无效：" + "；".join(details))
    return value


def _validate_text(value: object, *, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"菜单语言目录的{name}必须是非空文本")
    if len(value) > maximum:
        raise ValueError(f"菜单语言目录的{name}超过{maximum}字符上限")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
        raise ValueError(f"菜单语言目录的{name}包含控制字符")
    if _DANGEROUS_MARKUP.search(value):
        raise ValueError(f"菜单语言目录的{name}包含不允许的标记文本")
    return value


def validate_catalogue(value: object) -> dict[str, object]:
    """校验完整目录，并保留输入对象的值供构建器按ID注入。"""
    root = _require_exact_keys(value, frozenset(("schema_version", "locales")), "根对象")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ValueError("菜单语言目录schema_version必须为1")

    locales = _require_exact_keys(
        root["locales"], frozenset(SUPPORTED_LOCALES), "locale集合"
    )
    for locale in SUPPORTED_LOCALES:
        entry = _require_exact_keys(locales[locale], LOCALE_KEYS, f"{locale}文本")
        for key in TEXT_KEYS:
            if key in LABEL_KEYS:
                maximum = MAX_LABEL_LENGTH
            elif key in DESCRIPTION_KEYS:
                maximum = MAX_DESCRIPTION_LENGTH
            else:
                maximum = MAX_CHOICE_LENGTH
            text = _validate_text(entry[key], name=f"{locale}.{key}", maximum=maximum)
            if key == "timeout_choice_format":
                without_placeholder = text.replace("{seconds}", "", 1)
                if (
                    text.count("{seconds}") != 1
                    or "{" in without_placeholder
                    or "}" in without_placeholder
                ):
                    raise ValueError(
                        f"菜单语言目录的{locale}.{key}必须恰好包含一次{{seconds}}占位符"
                    )

        target_languages = _require_exact_keys(
            entry["target_languages"],
            frozenset(TARGET_LANGUAGE_IDS),
            f"{locale}.target_languages",
        )
        for target_id in TARGET_LANGUAGE_IDS:
            _validate_text(
                target_languages[target_id],
                name=f"{locale}.target_languages.{target_id}",
                maximum=MAX_CHOICE_LENGTH,
            )

    return root


def parse_catalogue(raw: bytes) -> dict[str, object]:
    """解析并校验给定的单次字节快照。"""
    if len(raw) > MAX_CATALOGUE_BYTES:
        raise ValueError("菜单语言目录超过256 KiB上限")
    if raw.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in raw:
        raise ValueError("菜单语言目录必须是无BOM、无字节码标记且无NUL的UTF-8 JSON")
    try:
        source = raw.decode("utf-8")
        value = json.loads(source, object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("菜单语言目录不是有效UTF-8 JSON") from error
    return validate_catalogue(value)


def load_catalogue() -> dict[str, object]:
    """从单次字节快照读取并校验资源目录。"""
    return parse_catalogue(CATALOGUE_PATH.read_bytes())
