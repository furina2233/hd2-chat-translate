"""校验免费服务私有配置并生成原生客户端认证头文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "free-service.private.json"
GENERATED_HEADER_NAME = "free_service.generated.h"
_CONFIG_KEYS = {"endpoint_url", "key1"}
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    re.ASCII,
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("免费服务配置包含重复字段")
        value[key] = item
    return value


def _valid_endpoint_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("endpoint_url必须是非空字符串")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("endpoint_url必须是有效UTF-8") from error
    if len(encoded) > 2048:
        raise ValueError("endpoint_url超过2048字节")
    if any(character.isspace() or ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
           for character in value):
        raise ValueError("endpoint_url不能包含空白或控制字符")
    if any(character in value for character in "?#\\"):
        raise ValueError("endpoint_url不能包含查询、片段或反斜线")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("endpoint_url格式无效") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint_url仅支持HTTP或HTTPS绝对URL")
    if hostname is None or not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint_url主机名或userinfo无效")
    if "@" in parsed.netloc or parsed.netloc.endswith(":"):
        raise ValueError("endpoint_url authority格式无效")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("endpoint_url端口无效")
    if parsed.path and not parsed.path.startswith("/"):
        raise ValueError("endpoint_url路径格式无效")
    path = parsed.path or "/"
    if path == "/":
        path = "/translate"
    prefix = value[:len(value) - len(parsed.path)] if parsed.path else value
    return prefix + path


def load_config(path: Path | str = DEFAULT_CONFIG) -> tuple[dict[str, str], str]:
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
    except OSError as error:
        example_path = ROOT / "config" / "free-service.example.json"
        raise ValueError(
            f"缺少免费服务私有配置：{config_path}；请复制 {example_path} "
            "为 free-service.private.json 并填写服务端提供的配置，不会自动生成key1"
        ) from error
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("免费服务配置必须是有效UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != _CONFIG_KEYS:
        raise ValueError("免费服务配置必须且只能包含endpoint_url与key1")
    endpoint_url = _valid_endpoint_url(value["endpoint_url"])
    key1 = value["key1"]
    if not isinstance(key1, str) or not key1 or not _UUID_PATTERN.fullmatch(key1):
        raise ValueError("key1必须是非空UUID")
    return {"endpoint_url": endpoint_url, "key1": key1}, hashlib.sha256(raw).hexdigest()


def _c_string(value: str) -> str:
    return '"' + "".join(f"\\x{byte:02x}" for byte in value.encode("utf-8")) + '"'


def write_generated_header(
    config: dict[str, str], digest: str, output_dir: Path | str,
) -> Path:
    output = Path(output_dir) / GENERATED_HEADER_NAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "#ifndef HD2CT_FREE_SERVICE_GENERATED_H\n"
        "#define HD2CT_FREE_SERVICE_GENERATED_H\n\n"
        f"#define HD2CT_FREE_KEY1 {_c_string(config['key1'])}\n"
        f"#define HD2CT_FREE_ENDPOINT_DEFAULT {_c_string(config['endpoint_url'])}\n"
        f'#define HD2CT_FREE_CONFIG_SHA256 "{digest}"\n\n'
        "#endif\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def generate_header(config_path: Path | str, output_dir: Path | str) -> Path:
    config, digest = load_config(config_path)
    return write_generated_header(config, digest, output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    header_parser = commands.add_parser("generate-header")
    header_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    header_parser.add_argument("--output-dir", type=Path, required=True)
    digest_parser = commands.add_parser("digest")
    digest_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if args.command == "generate-header":
        generate_header(args.config, args.output_dir)
    else:
        _, digest = load_config(args.config)
        print(digest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
