"""应用设置与本机密钥存储。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppConfig:
    """翻译服务设置；密钥只保存在内存或 Windows DPAPI 密文中。"""

    url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout: float = 20.0
    enabled: bool = False


class ConfigError(ValueError):
    """用户设置不符合要求。"""


def _app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "HD2ChatTranslate"
    # Windows 上 LOCALAPPDATA 缺失时仍使用用户目录；其它系统用于临时开发。
    return Path.home() / ".local" / "share" / "HD2ChatTranslate"


def config_path() -> Path:
    return _app_dir() / "config.json"


def mailbox_path() -> Path:
    return _app_dir() / "mailbox"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    """使用当前 Windows 用户的 DPAPI 加密密钥。"""
    source, source_buffer = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "HD2 Chat Translate",
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise OSError("DPAPI 加密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    """使用当前 Windows 用户的 DPAPI 解密密钥。"""
    source, source_buffer = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise OSError("DPAPI 解密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _validate_saved_values(raw: dict[str, Any]) -> AppConfig:
    url = raw.get("url", "")
    model = raw.get("model", "")
    timeout = raw.get("timeout", 20.0)
    enabled = raw.get("enabled", False)
    if not isinstance(url, str) or not isinstance(model, str):
        raise ConfigError("配置文件中的 URL 或模型名称无效")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ConfigError("配置文件中的超时值无效")
    if not isinstance(enabled, bool):
        raise ConfigError("配置文件中的启用状态无效")
    return AppConfig(url=url, model=model, timeout=float(timeout), enabled=enabled)


def load_config(path: Path | None = None) -> AppConfig:
    """读取设置；非 Windows 系统不会从文件读取密钥。"""
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppConfig()
        config = _validate_saved_values(raw)
        key = ""
        protected = raw.get("api_key_protected")
        if os.name == "nt" and isinstance(protected, str) and protected:
            try:
                key = _dpapi_unprotect(base64.b64decode(protected, validate=True)).decode("utf-8")
            except Exception:
                key = ""
        return AppConfig(
            url=config.url,
            model=config.model,
            api_key=key,
            timeout=config.timeout,
            enabled=config.enabled,
        )
    except FileNotFoundError:
        return AppConfig()
    except (OSError, UnicodeError, json.JSONDecodeError, ConfigError):
        # 配置损坏时以空设置启动，不把文件内容写入日志或界面。
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """原子保存设置；Windows 使用 DPAPI，其它系统不持久化密钥。"""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "url": config.url,
        "model": config.model,
        "timeout": config.timeout,
        "enabled": config.enabled,
    }
    if os.name == "nt" and config.api_key:
        encrypted = _dpapi_protect(config.api_key.encode("utf-8"))
        raw["api_key_protected"] = base64.b64encode(encrypted).decode("ascii")
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
