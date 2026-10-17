"""原生翻译 worker 的本机回环验证，不读取真实注册表或调用公网服务。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
NATIVE_TEST_ROOT = ROOT / "artifacts" / "validation"
sys.path.insert(0, str(ROOT / "tools"))
import build_native_http
from scripts import target_languages

NATIVE_SOURCES = build_native_http.NATIVE_SOURCES
TARGET_LANGUAGE_CATALOGUE = target_languages.load_catalogue()
INTERFACE_OPTION_ID = TARGET_LANGUAGE_CATALOGUE["menu_options"]["interface_type"]["option_id"]
ENEMY_SLANG_OPTION_ID = TARGET_LANGUAGE_CATALOGUE["menu_options"][
    "enemy_slang"
]["option_id"]
CHAT_RESPONSE_FORMAT = {"type": "json_object"}
MACHINE_ENDPOINTS = {
    4: "https://fanyi-api.baidu.com/api/trans/vip/translate",
    5: "https://openapi.youdao.com/api",
    6: "https://api.translator.azure.cn/translate",
    7: "https://api.cognitive.microsofttranslator.com/translate",
    8: "https://translation.googleapis.com/language/translate/v2",
}
REGISTRY_READ_IDS = {
    1: "HD2CT_API_URL",
    2: "HD2CT_MODEL",
    3: "HD2CT_API_KEY",
    4: "HD2CT_APP_ID",
    5: "HD2CT_REGION",
}


ENVIRONMENT_CHILD = r"""
import ctypes
import json
import os
import sys
import time
from pathlib import Path

dll_path, fixture_json, actions_json = sys.argv[1:4]
fixture = json.loads(fixture_json)
actions = json.loads(actions_json)
for name in tuple(os.environ):
    if name.upper().startswith("HD2CT_"):
        del os.environ[name]
os.environ.update({
    "HD2CT_API_URL": "http://127.0.0.1:1/process-url-must-not-be-used",
    "HD2CT_MODEL": "process-model-must-not-be-used",
    "HD2CT_API_KEY": "process-key-must-not-be-used",
    "HD2CT_APP_ID": "process-app-must-not-be-used",
    "HD2CT_REGION": "process-region-must-not-be-used",
})

lib = ctypes.CDLL(dll_path)
lib.fixture_ClearRegistry.argtypes = []
lib.fixture_ClearRegistry.restype = None
lib.fixture_SetRegistryValue.argtypes = [
    ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
]
lib.fixture_SetRegistryValue.restype = ctypes.c_int
lib.fixture_ResetRegistryReadCounts.argtypes = []
lib.fixture_ResetRegistryReadCounts.restype = None
lib.fixture_GetRegistryReadCount.argtypes = [ctypes.c_uint32]
lib.fixture_GetRegistryReadCount.restype = ctypes.c_uint32
lib.fixture_SetTargetValuesPath.argtypes = [ctypes.c_wchar_p]
lib.fixture_SetTargetValuesPath.restype = ctypes.c_int
lib.fixture_GetMachineEndpoint.argtypes = [
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_GetMachineEndpoint.restype = ctypes.c_int
lib.fixture_SetMachineEndpoint.argtypes = [ctypes.c_uint32, ctypes.c_char_p]
lib.fixture_SetMachineEndpoint.restype = ctypes.c_int
lib.fixture_NormalizeUrl.argtypes = [
    ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
]
lib.fixture_NormalizeUrl.restype = ctypes.c_int
lib.fixture_Initialized.argtypes = []
lib.fixture_Initialized.restype = ctypes.c_uint32
lib.fixture_BootstrapCount.argtypes = []
lib.fixture_BootstrapCount.restype = ctypes.c_uint32
lib.fixture_ForceBootstrapCreateFailure.argtypes = []
lib.fixture_ForceBootstrapCreateFailure.restype = None
lib.fixture_ForceWorkerCreateFailure.argtypes = [ctypes.c_uint32]
lib.fixture_ForceWorkerCreateFailure.restype = None
lib.fixture_CacheCount.argtypes = []
lib.fixture_CacheCount.restype = ctypes.c_uint32
lib.fixture_ExpireActiveRequest.argtypes = [ctypes.c_char_p]
lib.fixture_ExpireActiveRequest.restype = ctypes.c_int
lib.HD2CT_Submit.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
    ctypes.c_char_p,
]
lib.HD2CT_Submit.restype = ctypes.c_uint32
lib.HD2CT_Poll.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
]
lib.HD2CT_Poll.restype = ctypes.c_uint32
lib.HD2CT_Cancel.argtypes = [ctypes.c_char_p]
lib.HD2CT_Cancel.restype = ctypes.c_uint32
lib.fixture_ClearRegistry()
lib.fixture_ResetRegistryReadCounts()

target_values_path = fixture.get("__target_values_path")
if target_values_path is not None:
    if not lib.fixture_SetTargetValuesPath(target_values_path):
        raise RuntimeError("test-only target values path was rejected")
    primary = fixture.get("__target_primary")
    if primary is not None:
        Path(target_values_path).write_bytes(primary.encode("utf-8"))
    backup = fixture.get("__target_backup")
    if backup is not None:
        Path(str(target_values_path) + ".bak").write_bytes(backup.encode("utf-8"))

for hive_name, hive_id in (("user", 0), ("machine", 1)):
    for name, value in fixture.get(hive_name, {}).items():
        if not lib.fixture_SetRegistryValue(hive_id, name, value):
            raise RuntimeError("fixture registry value was rejected")

if fixture.get("__force_bootstrap_create_failure"):
    lib.fixture_ForceBootstrapCreateFailure()
worker_failure_at = fixture.get("__force_worker_create_failure_at", 0)
if worker_failure_at:
    lib.fixture_ForceWorkerCreateFailure(worker_failure_at)

def submit(action):
    body = bytes.fromhex(action["body_hex"])
    backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
    started = time.perf_counter()
    accepted = lib.HD2CT_Submit(
        action["token"].encode("ascii"), ctypes.cast(backing, ctypes.c_void_p),
        len(body), action.get("flags", 0), None,
    )
    return {
        "accepted": accepted,
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
    }

def poll(token):
    output = ctypes.create_string_buffer(16388)
    written = ctypes.c_uint32()
    found = lib.HD2CT_Poll(
        token.encode("ascii"), ctypes.cast(output, ctypes.c_void_p),
        len(output), ctypes.byref(written),
    )
    if not found:
        return None
    return bytes(output.raw[:written.value]).decode("utf-8")

def wait_for_result(token, timeout):
    deadline = time.monotonic() + timeout
    result = poll(token)
    while result is None and time.monotonic() < deadline:
        time.sleep(0.005)
        result = poll(token)
    return result

def registry_counts():
    return {
        name: int(lib.fixture_GetRegistryReadCount(read_id))
        for read_id, name in ((1, "HD2CT_API_URL"), (2, "HD2CT_MODEL"),
                              (3, "HD2CT_API_KEY"), (4, "HD2CT_APP_ID"),
                              (5, "HD2CT_REGION"))
    }

def write_target_values(contents):
    if target_values_path is None:
        raise RuntimeError("target values path is unavailable")
    Path(target_values_path).write_bytes(contents.encode("utf-8"))

results = []
for action in actions:
    op = action["op"]
    if op == "submit":
        results.append(submit(action))
    elif op == "poll":
        results.append({"result": poll(action["token"])})
    elif op == "wait":
        results.append({
            "result": wait_for_result(action["token"], action.get("timeout", 8.0)),
        })
    elif op == "cancel":
        results.append({
            "cancelled": lib.HD2CT_Cancel(action["token"].encode("ascii")),
        })
    elif op == "cancel_all":
        results.append({"cancelled": lib.HD2CT_Cancel(None)})
    elif op == "target_file":
        write_target_values(action["contents"])
        results.append({"written": True})
    elif op == "registry_set":
        hive = 0 if action.get("hive", "user") == "user" else 1
        accepted = lib.fixture_SetRegistryValue(
            hive, action["name"], action["value"],
        )
        results.append({"accepted": accepted})
    elif op == "get_endpoint":
        output = ctypes.create_string_buffer(2049)
        accepted = lib.fixture_GetMachineEndpoint(
            action["interface_type"], ctypes.cast(output, ctypes.c_void_p),
            len(output),
        )
        results.append({
            "accepted": accepted,
            "url": output.value.decode("utf-8"),
        })
    elif op == "set_endpoint":
        url = action.get("url")
        encoded_url = url.encode("utf-8") if url is not None else None
        accepted = lib.fixture_SetMachineEndpoint(
            action["interface_type"], encoded_url,
        )
        if action.get("allow_reject", False):
            if accepted:
                raise RuntimeError("an invalid machine endpoint override was accepted")
        elif not accepted:
            raise RuntimeError("a loopback machine endpoint override was rejected")
        if accepted and url is not None:
            output = ctypes.create_string_buffer(2049)
            resolved = lib.fixture_GetMachineEndpoint(
                action["interface_type"], ctypes.cast(output, ctypes.c_void_p),
                len(output),
            )
            if not resolved or output.value.decode("utf-8") != url:
                raise RuntimeError("machine endpoint override did not resolve")
        results.append({"accepted": accepted})
    elif op == "normalize_url":
        capacity = action["capacity"]
        output = ctypes.create_string_buffer(capacity)
        accepted = lib.fixture_NormalizeUrl(
            action["url"].encode("utf-8"), action["adapter_id"],
            output, capacity,
        )
        results.append({
            "accepted": accepted,
            "url": output.value.decode("utf-8"),
        })
    elif op == "read_counts":
        results.append(registry_counts())
    elif op == "wait_initialized":
        deadline = time.monotonic() + action.get("timeout", 5.0)
        while lib.fixture_Initialized() != 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        results.append({"initialized": lib.fixture_Initialized() == 2})
    elif op == "expire_active":
        expired = lib.fixture_ExpireActiveRequest(action["token"].encode("ascii"))
        results.append({"expired": expired})
    elif op == "wait_file":
        path = Path(action["path"])
        deadline = time.monotonic() + action.get("timeout", 5.0)
        while not path.is_file() and time.monotonic() < deadline:
            time.sleep(0.005)
        results.append({"found": path.is_file()})
    elif op == "sleep":
        time.sleep(action["seconds"])
        results.append({"slept": True})
    else:
        raise RuntimeError("unknown test action")

print(json.dumps({
    "actions": results,
    "bootstrap_count": int(lib.fixture_BootstrapCount()),
    "cache_count": int(lib.fixture_CacheCount()),
    "read_counts": registry_counts(),
}, ensure_ascii=False), flush=True)
"""


ENVIRONMENT_SHIM_C = r"""
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#define WINVER 0x0601
#include <windows.h>
#include <winreg.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

#define FIXTURE_VALUE_COUNT 32u
#define FIXTURE_NAME_CAPACITY 64u
#define FIXTURE_VALUE_CAPACITY 4097u
typedef struct FixtureRegistryValue {
    DWORD hive;
    int used;
    wchar_t name[FIXTURE_NAME_CAPACITY];
    wchar_t value[FIXTURE_VALUE_CAPACITY];
} FixtureRegistryValue;

static FixtureRegistryValue fixture_values[FIXTURE_VALUE_COUNT];
static volatile LONG fixture_read_counts[5];

static int fixture_read_index(LPCWSTR name)
{
    if (_wcsicmp(name, L"HD2CT_API_URL") == 0) return 0;
    if (_wcsicmp(name, L"HD2CT_MODEL") == 0) return 1;
    if (_wcsicmp(name, L"HD2CT_API_KEY") == 0) return 2;
    if (_wcsicmp(name, L"HD2CT_APP_ID") == 0) return 3;
    if (_wcsicmp(name, L"HD2CT_REGION") == 0) return 4;
    return -1;
}

LSTATUS WINAPI fixture_RegGetValueW(
    HKEY root, LPCWSTR subkey, LPCWSTR name, DWORD flags, LPDWORD type,
    PVOID data, LPDWORD bytes)
{
    DWORD hive;
    DWORD index;
    DWORD required;
    int read_index;
    (void)flags;
    if (root == HKEY_CURRENT_USER && subkey != NULL &&
        wcscmp(subkey, L"Environment") == 0) {
        hive = 0u;
    } else if (root == HKEY_LOCAL_MACHINE && subkey != NULL &&
               wcscmp(subkey,
                   L"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment") == 0) {
        hive = 1u;
    } else {
        return ERROR_FILE_NOT_FOUND;
    }
    if (name == NULL || bytes == NULL) return ERROR_INVALID_PARAMETER;
    read_index = fixture_read_index(name);
    if (read_index >= 0) InterlockedIncrement(&fixture_read_counts[read_index]);
    for (index = 0u; index < FIXTURE_VALUE_COUNT; ++index) {
        FixtureRegistryValue *entry = &fixture_values[index];
        if (entry->used && entry->hive == hive &&
            _wcsicmp(entry->name, name) == 0) {
            required = (DWORD)((wcslen(entry->value) + 1u) * sizeof(wchar_t));
            if (type != NULL) *type = REG_SZ;
            if (data == NULL || *bytes < required) {
                *bytes = required;
                return ERROR_MORE_DATA;
            }
            memcpy(data, entry->value, required);
            *bytes = required;
            return ERROR_SUCCESS;
        }
    }
    return ERROR_FILE_NOT_FOUND;
}

#include "client.c"

__declspec(dllexport) void __cdecl fixture_ClearRegistry(void)
{
    SecureZeroMemory(fixture_values, sizeof(fixture_values));
}

__declspec(dllexport) void __cdecl fixture_ResetRegistryReadCounts(void)
{
    SecureZeroMemory((void *)fixture_read_counts, sizeof(fixture_read_counts));
}

__declspec(dllexport) uint32_t __cdecl fixture_GetRegistryReadCount(uint32_t index)
{
    if (index == 0u || index > 5u) return 0u;
    return (uint32_t)InterlockedCompareExchange(&fixture_read_counts[index - 1u], 0, 0);
}

__declspec(dllexport) int __cdecl fixture_SetRegistryValue(
    DWORD hive, LPCWSTR name, LPCWSTR value)
{
    DWORD index;
    DWORD free_index = FIXTURE_VALUE_COUNT;
    size_t name_length;
    size_t value_length;
    if (hive > 1u || name == NULL || value == NULL) return 0;
    name_length = wcslen(name);
    value_length = wcslen(value);
    if (name_length >= FIXTURE_NAME_CAPACITY ||
        value_length >= FIXTURE_VALUE_CAPACITY) return 0;
    for (index = 0u; index < FIXTURE_VALUE_COUNT; ++index) {
        if (fixture_values[index].used && fixture_values[index].hive == hive &&
            _wcsicmp(fixture_values[index].name, name) == 0) {
            free_index = index;
            break;
        }
        if (!fixture_values[index].used && free_index == FIXTURE_VALUE_COUNT) {
            free_index = index;
        }
    }
    if (free_index == FIXTURE_VALUE_COUNT) return 0;
    fixture_values[free_index].hive = hive;
    fixture_values[free_index].used = 1;
    memcpy(fixture_values[free_index].name, name,
           (name_length + 1u) * sizeof(wchar_t));
    memcpy(fixture_values[free_index].value, value,
           (value_length + 1u) * sizeof(wchar_t));
    return 1;
}

__declspec(dllexport) int __cdecl fixture_SetTargetValuesPath(
    const wchar_t *path)
{
    return hd2ct_test_set_values_file_path(path);
}

__declspec(dllexport) int __cdecl fixture_GetMachineEndpoint(
    uint32_t interface_type, char *output, uint32_t capacity)
{
    return hd2ct_machine_endpoint_for_interface(
        interface_type, output, (size_t)capacity);
}

__declspec(dllexport) int __cdecl fixture_SetMachineEndpoint(
    uint32_t interface_type, const char *url)
{
    return hd2ct_test_set_machine_endpoint(interface_type, url);
}

__declspec(dllexport) int __cdecl fixture_NormalizeUrl(
    const char *input, uint32_t adapter_id, char *output, size_t capacity)
{
    return hd2ct_normalize_url(input, adapter_id, output, capacity);
}

__declspec(dllexport) uint32_t __cdecl fixture_Initialized(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_initialized, 0, 0);
}

__declspec(dllexport) uint32_t __cdecl fixture_BootstrapCount(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_test_bootstrap_count, 0, 0);
}

__declspec(dllexport) void __cdecl fixture_ForceBootstrapCreateFailure(void)
{
    InterlockedExchange(&g_test_fail_bootstrap_create, 1);
}

__declspec(dllexport) void __cdecl fixture_ForceWorkerCreateFailure(uint32_t ordinal)
{
    InterlockedExchange(&g_test_fail_worker_create_at, (LONG)ordinal);
}

__declspec(dllexport) uint32_t __cdecl fixture_CacheCount(void)
{
    uint32_t count = 0u;
    uint32_t index;
    for (index = 0u; index < HD2CT_CACHE_COUNT; ++index) {
        if (g_cache[index].used != 0u) ++count;
    }
    return count;
}

__declspec(dllexport) int __cdecl fixture_ExpireActiveRequest(const char *token)
{
    uint32_t index;
    int found = 0;
    if (token == NULL) return 0;
    AcquireSRWLockExclusive(&g_lock);
    for (index = 0u; index < HD2CT_JOB_COUNT; ++index) {
        HD2CT_JobSlot *slot = &g_jobs[index];
        if (slot->state == HD2CT_SLOT_ACTIVE && strcmp(slot->token, token) == 0) {
            slot->request_deadline_ms = GetTickCount64() - 1u;
            found = 1;
            break;
        }
    }
    ReleaseSRWLockExclusive(&g_lock);
    return found;
}
"""


def result_content(translation: str, is_target_language: bool = False) -> str:
    return json.dumps(
        {"is_target_language": is_target_language, "translation": translation},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def chat_response(translation: str, is_target_language: bool = False) -> bytes:
    content = result_content(translation, is_target_language)
    return json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def anthropic_response(
    blocks: list[str], stop_reason: str = "end_turn", **extra: object,
) -> bytes:
    response = {
        "type": "message",
        "role": "assistant",
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": block} for block in blocks],
    }
    response.update(extra)
    return json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def google_response(translation: str) -> bytes:
    return json.dumps(
        {"data": {"translations": [{"translatedText": translation}]}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def baidu_response(translation: str = "百度翻译") -> bytes:
    return json.dumps(
        {"from": "en", "to": "zh",
         "trans_result": [{"src": "source", "dst": translation}]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def youdao_response(translation: str = "有道翻译") -> bytes:
    return json.dumps(
        {"errorCode": "0", "l": "en2zh-CHS", "translation": [translation]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def bing_response(translation: str = "必应翻译", target: str = "zh-Hans") -> bytes:
    return json.dumps(
        [{"translations": [{"text": translation, "to": target}]}],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def bing_page() -> bytes:
    return (
        '<script>var _G={x:"x",IG:"0123456789ABCDEF"}; '
        'var params_AbusePreventionHelper = '
        '[1790000000000,"fixture-token",3600000];</script>'
        '<div data-iid="translator.5022"></div>'
    ).encode("utf-8")


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.get_paths: list[str] = []
        self.payloads: list[dict | list] = []
        self.content_types: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.response = chat_response("default")
        self.get_response = bing_page()
        self.responses_by_path: dict[str, bytes] = {}
        self.responses_by_kind: dict[str, bytes] = {}
        self.status = 200
        self.get_status = 200
        self.status_by_path: dict[str, int] = {}
        self.delay = 0.0
        self.get_delay = 0.0
        self.delay_by_path: dict[str, float] = {}
        self.request_finished = threading.Event()
        self.request_started_marker: Path | None = None

    @staticmethod
    def payload_kind(payload: object) -> str | None:
        if isinstance(payload, list):
            return "bing"
        if not isinstance(payload, dict):
            return None
        if "response_format" in payload:
            return "chat"
        if "max_tokens" in payload:
            return "anthropic"
        if "appid" in payload:
            return "baidu"
        if "appKey" in payload:
            return "youdao"
        if "target" in payload and "q" in payload:
            return "google"
        return None

    def clear(self) -> None:
        with self.lock:
            self.paths.clear()
            self.get_paths.clear()
            self.payloads.clear()
            self.content_types.clear()
            self.headers.clear()
            self.response = chat_response("default")
            self.get_response = bing_page()
            self.responses_by_path.clear()
            self.responses_by_kind.clear()
            self.status = 200
            self.get_status = 200
            self.status_by_path.clear()
            self.delay = 0.0
            self.get_delay = 0.0
            self.delay_by_path.clear()
        self.request_finished.clear()
        self.request_started_marker = None


@unittest.skipUnless(WINDOWS, "原生 DLL 仅在 Windows 上运行")
class NativeHttpWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        NATIVE_TEST_ROOT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(
            prefix="hd2ct-http-tests-", dir=str(NATIVE_TEST_ROOT),
        )
        cls.temp_path = Path(cls.temp.name)
        cls.state = FakeState()
        state = cls.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                content_type = self.headers.get("Content-Type", "")
                try:
                    if content_type.lower().startswith(
                        "application/x-www-form-urlencoded"
                    ):
                        fields = parse_qs(
                            raw.decode("utf-8"), keep_blank_values=True,
                        )
                        payload: object = {
                            name: values[-1] for name, values in fields.items()
                        }
                    else:
                        payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                with state.lock:
                    state.paths.append(self.path)
                    state.payloads.append(payload if isinstance(payload, (dict, list)) else {})
                    state.content_types.append(content_type)
                    state.headers.append(dict(self.headers.items()))
                    response_status = state.status_by_path.get(path, state.status)
                    response = state.responses_by_path.get(path)
                    if response is None:
                        kind = state.payload_kind(payload)
                        response = state.responses_by_kind.get(kind, state.response)
                    delay = state.delay_by_path.get(path, state.delay)
                    started_marker = state.request_started_marker
                if started_marker is not None:
                    started_marker.write_text("started", encoding="ascii")
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(response_status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    state.request_finished.set()

            def do_GET(self) -> None:  # noqa: N802
                with state.lock:
                    state.get_paths.append(self.path)
                    response = state.get_response
                    response_status = state.get_status
                    delay = state.get_delay
                    started_marker = state.request_started_marker
                if started_marker is not None:
                    started_marker.write_text("started", encoding="ascii")
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(response_status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    state.request_finished.set()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True,
        )
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.compiler = shutil.which("gcc") or "gcc"
        target_languages.generate_c_header(cls.temp_path, TARGET_LANGUAGE_CATALOGUE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=3)
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.state.clear()

    @staticmethod
    def menu_text(
        interface_type: int | str | None,
        enabled: bool = True,
        ignore_self: bool = True,
        target_index: int = 1,
        enemy_slang: bool = True,
    ) -> str:
        menu = TARGET_LANGUAGE_CATALOGUE["menu_options"]
        values = [
            f"{TARGET_LANGUAGE_CATALOGUE['option_id']}\t{target_index}\n",
            f"{menu['enabled']['option_id']}\t{'true' if enabled else 'false'}\n",
            f"{menu['ignore_self']['option_id']}\t{'true' if ignore_self else 'false'}\n",
            f"{menu['enemy_slang']['option_id']}\t{'true' if enemy_slang else 'false'}\n",
            f"{menu['timeout']['option_id']}\t1\n",
        ]
        if interface_type is not None:
            values.append(f"{INTERFACE_OPTION_ID}\t{interface_type}\n")
        return "".join(values)

    def environment_test_dll(self) -> Path:
        dll = self.temp_path / "hd2ct_http_environment_test.dll"
        if dll.is_file():
            return dll
        wrapper = self.temp_path / "hd2ct_http_environment_test.c"
        registry_shim = self.temp_path / "hd2ct_http_registry_test_shim.h"
        wrapper.write_text(ENVIRONMENT_SHIM_C, encoding="utf-8", newline="\n")
        registry_shim.write_text(
            "#ifndef WIN32_LEAN_AND_MEAN\n"
            "#define WIN32_LEAN_AND_MEAN\n"
            "#endif\n"
            "#include <windows.h>\n"
            "#include <winreg.h>\n"
            "LSTATUS WINAPI fixture_RegGetValueW(\n"
            "    HKEY root, LPCWSTR subkey, LPCWSTR name, DWORD flags, LPDWORD type,\n"
            "    PVOID data, LPDWORD bytes);\n"
            "#define RegGetValueW fixture_RegGetValueW\n",
            encoding="utf-8", newline="\n",
        )
        command = [
            self.compiler, "-std=c11", "-O2", "-shared", "-s", "-static-libgcc",
            "-finput-charset=UTF-8", "-fexec-charset=UTF-8",
            "-D_WIN32_WINNT=0x0601", "-DWINVER=0x0601", "-DHD2CT_TESTING",
            "-DCJSON_NESTING_LIMIT=32", "-DCJSON_HIDE_SYMBOLS",
            "-Wl,--exclude-all-symbols", "-I", str(ROOT / "native"),
            "-I", str(self.temp_path), "-include", str(registry_shim),
            str(wrapper), *(str(source) for source in NATIVE_SOURCES[1:]),
            "-o", str(dll), "-lwinhttp", "-ladvapi32", "-lbcrypt",
            f'-DHD2CT_BING_TEST_BASE_URL="{self.url}"',
        ]
        completed = subprocess.run(
            command, cwd=ROOT, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=45,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        return dll

    def run_worker(
        self,
        actions: list[dict],
        user: dict[str, str] | None = None,
        machine: dict[str, str] | None = None,
        interface_type: int | str | None = 1,
        enabled: bool = True,
        ignore_self: bool = True,
        target_index: int = 1,
        force_bootstrap_failure: bool = False,
        force_worker_failure_at: int = 0,
        timeout: float = 15.0,
        enemy_slang: bool = True,
        target_contents: str | None = None,
        backup_contents: str | None = None,
    ) -> dict:
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-menu-values-", dir=str(NATIVE_TEST_ROOT),
        ) as directory:
            values_path = Path(directory) / "ModOptionsMenu.values"
            fixture = {
                "user": user or {},
                "machine": machine or {},
                "__target_values_path": str(values_path),
                "__target_primary": (
                    target_contents if target_contents is not None else self.menu_text(
                        interface_type, enabled, ignore_self, target_index,
                        enemy_slang,
                    )
                ),
                "__target_backup": backup_contents,
                "__force_bootstrap_create_failure": force_bootstrap_failure,
                "__force_worker_create_failure_at": force_worker_failure_at,
            }
            command = [
                sys.executable, "-c", ENVIRONMENT_CHILD,
                str(self.environment_test_dll()),
                json.dumps(fixture, ensure_ascii=False),
                json.dumps(actions, ensure_ascii=False),
            ]
            child_environment = {
                name: value for name, value in os.environ.items()
                if not name.upper().startswith("HD2CT_")
            }
            completed = subprocess.run(
                command, cwd=ROOT, env=child_environment, check=False,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stdout)
        return json.loads(lines[0])

    def endpoint_setup_actions(self) -> list[dict]:
        return [
            {
                "op": "set_endpoint",
                "interface_type": interface_type,
                "url": self.url + f"/fixture/machine-{interface_type}"
                + {
                    4: "/api/trans/vip/translate",
                    5: "/api",
                    6: "/translate",
                    7: "/translate",
                    8: "/language/translate/v2",
                }[interface_type],
            }
            for interface_type in range(4, 9)
        ]

    def submit_wait_actions(self, token: str, body: str) -> list[dict]:
        return [
            {"op": "submit", "token": token,
             "body_hex": body.encode("utf-8").hex()},
            {"op": "wait", "token": token},
        ]

    def test_hardcoded_machine_endpoints_and_job_scoped_registry(self) -> None:
        source = "Hold position & resupply."
        endpoint_actions = [
            {"op": "get_endpoint", "interface_type": interface_type}
            for interface_type in range(4, 9)
        ]
        endpoint_actions.extend(self.endpoint_setup_actions())
        endpoint_actions.extend([
            {"op": "set_endpoint", "interface_type": 8,
             "url": "https://example.invalid/not-local",
             "allow_reject": True},
            {"op": "set_endpoint", "interface_type": 2,
             "url": self.url + "/invalid-ai-endpoint",
             "allow_reject": True},
            {"op": "get_endpoint", "interface_type": 8},
        ])
        local_paths = {
            4: "/fixture/machine-4/api/trans/vip/translate",
            5: "/fixture/machine-5/api",
            6: "/fixture/machine-6/translate",
            7: "/fixture/machine-7/translate",
            8: "/fixture/machine-8/language/translate/v2",
        }
        for interface_type in range(4, 9):
            endpoint_actions.extend([
                {"op": "target_file",
                 "contents": self.menu_text(interface_type)},
                *self.submit_wait_actions(
                    f"fixed-machine-{interface_type}", source,
                ),
            ])
        endpoint_actions.extend([
            {"op": "registry_set", "name": "HD2CT_REGION", "value": ""},
            {"op": "target_file", "contents": self.menu_text(6)},
            *self.submit_wait_actions("empty-user-region", source),
            {"op": "registry_set", "name": "HD2CT_API_KEY", "value": ""},
            {"op": "target_file", "contents": self.menu_text(8)},
            *self.submit_wait_actions("empty-user-key", source),
            {"op": "read_counts"},
        ])
        long_url = "x" * 3000
        user = {
            "HD2CT_API_URL": long_url,
            "HD2CT_MODEL": "m" * 300,
            "HD2CT_API_KEY": "user-key",
            "HD2CT_APP_ID": "user-app",
            "HD2CT_REGION": "user-region",
        }
        machine = {
            "HD2CT_API_URL": self.url + "/machine-fallback-must-not-be-used",
            "HD2CT_MODEL": "machine-model",
            "HD2CT_API_KEY": "machine-key",
            "HD2CT_APP_ID": "machine-app",
            "HD2CT_REGION": "machine-region",
        }
        for interface_type in range(4, 9):
            self.state.responses_by_path[local_paths[interface_type]] = {
                4: baidu_response("百度翻译"),
                5: youdao_response("有道翻译"),
                6: bing_response("必应国内"),
                7: bing_response("必应全球"),
                8: google_response("谷歌翻译"),
            }[interface_type]
        result = self.run_worker(
            endpoint_actions, user=user, machine=machine, interface_type=4,
        )
        for offset, interface_type in enumerate(range(4, 9)):
            self.assertEqual(
                result["actions"][offset],
                {"accepted": 1, "url": MACHINE_ENDPOINTS[interface_type]},
            )
        invalid_setter_base = 10
        self.assertEqual(result["actions"][invalid_setter_base], {"accepted": 0})
        self.assertEqual(result["actions"][invalid_setter_base + 1], {"accepted": 0})
        self.assertEqual(result["actions"][invalid_setter_base + 2], {
            "accepted": 1,
            "url": self.url + "/fixture/machine-8/language/translate/v2",
        })
        self.assertEqual(
            result["actions"][invalid_setter_base + 2]["url"],
            self.url + "/fixture/machine-8/language/translate/v2",
        )
        self.assertEqual(
            [result["actions"][index]["result"]
             for index in (15, 18, 21, 24, 27)],
            ["OK\n百度翻译", "OK\n有道翻译", "OK\n必应国内",
             "OK\n必应全球", "OK\n谷歌翻译"],
        )
        self.assertEqual(result["actions"][31]["result"], "OK\n必应国内")
        self.assertEqual(result["actions"][35]["result"], "ERR\nMISSING_CONFIG")
        self.assertEqual(self.state.paths, [
            local_paths[4], local_paths[5],
            local_paths[6] + "?api-version=3.0&to=zh-Hans",
            local_paths[7] + "?api-version=3.0&to=zh-Hans",
            local_paths[8],
            local_paths[6] + "?api-version=3.0&to=zh-Hans",
        ])
        self.assertEqual(self.state.payloads[0]["q"], source)
        baidu_form = self.state.payloads[0]
        self.assertEqual(baidu_form["from"], "auto")
        self.assertEqual(baidu_form["to"], "zh")
        self.assertEqual(baidu_form["appid"], "user-app")
        self.assertEqual(
            baidu_form["sign"],
            hashlib.md5(
                ("user-app" + source + baidu_form["salt"] + "user-key").encode("utf-8")
            ).hexdigest(),
        )
        youdao_form = self.state.payloads[1]
        self.assertEqual(youdao_form["from"], "auto")
        self.assertEqual(youdao_form["to"], "zh-CHS")
        self.assertEqual(youdao_form["appKey"], "user-app")
        self.assertEqual(youdao_form["signType"], "v3")
        expected_input = (
            source if len(source) <= 20
            else source[:10] + str(len(source)) + source[-10:]
        )
        self.assertEqual(
            youdao_form["sign"],
            hashlib.sha256(
                ("user-app" + expected_input + youdao_form["salt"] +
                 youdao_form["curtime"] + "user-key").encode("utf-8")
            ).hexdigest(),
        )
        for index in (2, 3, 5):
            headers = {key.lower(): value for key, value in self.state.headers[index].items()}
            self.assertEqual(headers.get("ocp-apim-subscription-key"), "user-key")
            self.assertEqual(
                parse_qs(urlsplit(self.state.paths[index]).query),
                {"api-version": ["3.0"], "to": ["zh-Hans"]},
            )
            self.assertEqual(self.state.payloads[index], [{"Text": source}])
        self.assertEqual(
            self.state.headers[2].get("Ocp-Apim-Subscription-Region"),
            "user-region",
        )
        self.assertNotIn(
            "Ocp-Apim-Subscription-Region",
            self.state.headers[5],
        )
        self.assertEqual(self.state.payloads[4], {
            "q": source, "target": "zh-CN", "format": "text",
        })
        google_headers = {
            key.lower(): value for key, value in self.state.headers[4].items()
        }
        self.assertEqual(google_headers.get("x-goog-api-key"), "user-key")
        self.assertEqual(result["read_counts"], {
            "HD2CT_API_URL": 0,
            "HD2CT_MODEL": 0,
            "HD2CT_API_KEY": 7,
            "HD2CT_APP_ID": 2,
            "HD2CT_REGION": 3,
        })

    def test_applied_interface_switches_protocol_and_isolates_cache(self) -> None:
        source = "Move to extraction."
        actions = self.endpoint_setup_actions()
        actions.extend(self.submit_wait_actions("switch-chat", source))
        for interface_type in (3, 2, 4, 5, 6, 7, 8, 1):
            actions.append({
                "op": "target_file",
                "contents": self.menu_text(interface_type),
            })
            actions.extend(self.submit_wait_actions(
                f"switch-{interface_type}-{len(actions)}",
                source,
            ))
        actions.extend([
            {"op": "registry_set", "name": "HD2CT_API_KEY",
             "value": "changed-key"},
            {"op": "target_file", "contents": self.menu_text(2)},
            *self.submit_wait_actions("switch-chat-changed-key", source),
        ])
        actions.append({"op": "read_counts"})
        self.state.get_response = bing_page()
        self.state.responses_by_kind = {
            "chat": chat_response("Chat result"),
            "anthropic": anthropic_response([
                result_content("Anthropic result"),
            ]),
        }
        self.state.responses_by_path.update({
            "/fixture/machine-4/api/trans/vip/translate": baidu_response("Baidu result"),
            "/fixture/machine-5/api": youdao_response("Youdao result"),
            "/fixture/machine-6/translate": bing_response("Bing CN result"),
            "/fixture/machine-7/translate": bing_response("Bing global result"),
            "/fixture/machine-8/language/translate/v2": google_response("Google result"),
            "/ttranslatev3": bing_response("FREE result"),
        })
        user = {
            "HD2CT_API_URL": self.url + "/same-custom-endpoint",
            "HD2CT_MODEL": "fixture-model",
            "HD2CT_API_KEY": "initial-key",
            "HD2CT_APP_ID": "fixture-app",
            "HD2CT_REGION": "fixture-region",
        }
        result = self.run_worker(
            actions, user=user, machine={}, interface_type=2,
        )
        result_actions = result["actions"]
        job_results = [
            item["result"] for index, item in enumerate(result_actions)
            if index > 0 and "result" in item
        ]
        self.assertEqual(job_results, [
            "OK\nChat result", "OK\nAnthropic result", "OK\nChat result",
            "OK\nBaidu result", "OK\nYoudao result", "OK\nBing CN result",
            "OK\nBing global result", "OK\nGoogle result", "OK\nFREE result",
            "OK\nChat result",
        ])
        self.assertEqual(result["cache_count"], 9)
        self.assertEqual(self.state.paths.count("/same-custom-endpoint"), 3)
        self.assertEqual(len(self.state.paths), 9)
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(self.state.payloads[0]["response_format"], CHAT_RESPONSE_FORMAT)
        self.assertEqual(self.state.payloads[1]["max_tokens"], 4096)
        self.assertEqual(self.state.payloads[6], {
            "q": source, "target": "zh-CN", "format": "text",
        })
        self.assertEqual(self.state.payloads[7], {
            "fromLang": "auto-detect", "text": source, "to": "zh-Hans",
            "key": "1790000000000", "token": "fixture-token",
        })
        self.assertEqual(
            urlsplit(self.state.paths[7]).path,
            "/ttranslatev3",
        )
        self.assertEqual(
            urlsplit(self.state.paths[7]).query,
            "isVertical=1&&IG=0123456789ABCDEF&IID=translator.5022.1",
        )
        self.assertEqual(self.state.payloads[-1]["response_format"], CHAT_RESPONSE_FORMAT)
        self.assertEqual(result["read_counts"]["HD2CT_API_URL"], 4)
        self.assertEqual(result["read_counts"]["HD2CT_MODEL"], 4)
        self.assertEqual(result["read_counts"]["HD2CT_API_KEY"], 9)
        self.assertEqual(result["read_counts"]["HD2CT_APP_ID"], 2)
        self.assertEqual(result["read_counts"]["HD2CT_REGION"], 2)
        self.assertEqual(self.state.payloads[-1]["model"], "fixture-model")
        final_chat_headers = {
            key.lower(): value for key, value in self.state.headers[-1].items()
        }
        self.assertEqual(final_chat_headers.get("authorization"), "Bearer changed-key")

    def test_ai_custom_protocols_strict_responses_and_error_redaction(self) -> None:
        base = "https://api.example.invalid"
        normalization_cases = (
            (base, 1, base + "/chat/completions"),
            (base + "/", 1, base + "/chat/completions"),
            (base + "/v1", 1, base + "/v1/chat/completions"),
            (base + "/v1/", 1, base + "/v1/chat/completions"),
            (base + "/proxy/v1", 1, base + "/proxy/v1/chat/completions"),
            (base + "/proxy/v1/", 1, base + "/proxy/v1/chat/completions"),
            (base + "/api/paas/v4/", 1, base + "/api/paas/v4/"),
            (base + "/api/paas/v4/chat/completions", 1,
             base + "/api/paas/v4/chat/completions"),
            (base + "/proxy/v10", 1, base + "/proxy/v10"),
            (base + "/proxy/v1beta", 1, base + "/proxy/v1beta"),
            (base + "/custom/chat/path", 1, base + "/custom/chat/path"),
            (base + "/proxy/v1/chat/completions", 1,
             base + "/proxy/v1/chat/completions"),
            (base, 8, base + "/v1/messages"),
            (base + "/", 8, base + "/v1/messages"),
            (base + "/v1", 8, base + "/v1/messages"),
            (base + "/v1/", 8, base + "/v1/messages"),
            (base + "/proxy/v1", 8, base + "/proxy/v1/messages"),
            (base + "/proxy/v1/", 8, base + "/proxy/v1/messages"),
            (base + "/api/anthropic", 8, base + "/api/anthropic"),
            (base + "/api/anthropic/", 8, base + "/api/anthropic/"),
            (base + "/api/paas/v4/", 8, base + "/api/paas/v4/"),
            (base + "/v1beta", 8, base + "/v1beta"),
            (base + "/v1/messages", 8, base + "/v1/messages"),
            (base + "/proxy/v10", 8, base + "/proxy/v10"),
            (base + "/proxy/v1/messages", 8, base + "/proxy/v1/messages"),
        )
        normalization_actions = [
            {
                "op": "normalize_url", "url": input_url,
                "adapter_id": adapter_id, "capacity": 4096,
            }
            for input_url, adapter_id, _expected in normalization_cases
        ]

        expanded_input = base + "/proxy/v1"
        expanded_expected = base + "/proxy/v1/chat/completions"
        exact_output_capacity = len(expanded_expected.encode("utf-8")) + 1
        normalization_actions.extend([
            {
                "op": "normalize_url", "url": expanded_input,
                "adapter_id": 1, "capacity": exact_output_capacity,
            },
            {
                "op": "normalize_url", "url": expanded_input,
                "adapter_id": 1, "capacity": exact_output_capacity - 1,
            },
        ])

        anthropic_expanded_input = base + "/proxy/v1/"
        anthropic_expanded_expected = base + "/proxy/v1/messages"
        anthropic_output_capacity = len(anthropic_expanded_expected.encode("utf-8")) + 1
        normalization_actions.extend([
            {
                "op": "normalize_url", "url": anthropic_expanded_input,
                "adapter_id": 8, "capacity": anthropic_output_capacity,
            },
            {
                "op": "normalize_url", "url": anthropic_expanded_input,
                "adapter_id": 8, "capacity": anthropic_output_capacity - 1,
            },
        ])

        maximum_url_prefix = "https://api.example.invalid/"
        maximum_url = maximum_url_prefix + "x" * (2048 - len(maximum_url_prefix))
        self.assertEqual(len(maximum_url.encode("utf-8")), 2048)
        normalization_actions.extend([
            {
                "op": "normalize_url", "url": maximum_url,
                "adapter_id": 1, "capacity": 2049,
            },
            {
                "op": "normalize_url", "url": maximum_url + "x",
                "adapter_id": 1, "capacity": 2050,
            },
        ])
        normalization_result = self.run_worker(normalization_actions)
        expected_normalized = [
            (1, expected) for _input_url, _adapter_id, expected in normalization_cases
        ] + [
            (1, expanded_expected), (0, ""),
            (1, anthropic_expanded_expected), (0, ""),
            (1, maximum_url), (0, ""),
        ]
        self.assertEqual(
            [
                (result["accepted"], result["url"])
                for result in normalization_result["actions"]
            ],
            expected_normalized,
        )

        source = "Hold position."
        user = {
            "HD2CT_API_URL": self.url + "/custom/chat/path",
            "HD2CT_MODEL": "fixture-model",
            "HD2CT_API_KEY": "fake-api-key",
        }
        self.state.responses_by_path["/custom/chat/path"] = chat_response(
            "Chat translation",
        )
        chat = self.run_worker(
            self.submit_wait_actions("chat-request", source),
            user=user, interface_type=2,
        )
        self.assertEqual(chat["actions"][1]["result"], "OK\nChat translation")
        self.assertEqual(self.state.paths, ["/custom/chat/path"])
        self.assertEqual(set(self.state.payloads[0]), {
            "model", "messages", "response_format",
        })
        self.assertEqual(self.state.payloads[0]["response_format"], CHAT_RESPONSE_FORMAT)
        chat_system_prompt = self.state.payloads[0]["messages"][0]["content"]
        self.assertIn("lol=哈哈", chat_system_prompt)
        self.assertIn("Charger=牛", chat_system_prompt)
        self.assertIn("reinforce=增援", chat_system_prompt)
        self.assertLess(len(chat_system_prompt.encode("utf-8")), 2048)
        request_example = json.loads(
            (ROOT / "docs" / "ai-request-example.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            chat_system_prompt,
            request_example["messages"][0]["content"],
        )
        self.assertEqual(self.state.payloads[0]["messages"][1], {
            "role": "user", "content": source,
        })
        chat_headers = {
            key.lower(): value for key, value in self.state.headers[0].items()
        }
        self.assertEqual(chat_headers.get("authorization"), "Bearer fake-api-key")

        self.state.clear()
        anthropic_inner = result_content("繁體翻譯")
        self.state.responses_by_path["/custom/anthropic/messages"] = anthropic_response([
            anthropic_inner[:9], anthropic_inner[9:],
        ])
        anthropic_user = dict(user)
        anthropic_user["HD2CT_API_URL"] = self.url + "/custom/anthropic/messages"
        anthropic = self.run_worker(
            self.submit_wait_actions("anthropic-request", source),
            user=anthropic_user, interface_type=3, target_index=2,
        )
        self.assertEqual(anthropic["actions"][1]["result"], "OK\n繁體翻譯")
        self.assertEqual(self.state.paths, ["/custom/anthropic/messages"])
        self.assertEqual(set(self.state.payloads[0]), {
            "model", "max_tokens", "system", "messages",
        })
        self.assertEqual(self.state.payloads[0]["max_tokens"], 4096)
        self.assertEqual(self.state.payloads[0]["messages"], [
            {"role": "user", "content": source},
        ])
        self.assertIn("繁體中文", self.state.payloads[0]["system"])
        self.assertNotIn("Charger=牛", self.state.payloads[0]["system"])
        self.assertNotIn("lol=哈哈", self.state.payloads[0]["system"])
        anthropic_headers = {
            key.lower(): value for key, value in self.state.headers[0].items()
        }
        self.assertEqual(anthropic_headers.get("x-api-key"), "fake-api-key")
        self.assertEqual(anthropic_headers.get("anthropic-version"), "2023-06-01")
        self.assertNotIn("authorization", anthropic_headers)

        anthropic_worker_cases = (
            ("root", self.url, "/v1/messages",
             "Root and version endpoint translation"),
            ("v1-slash", self.url + "/v1/", "/v1/messages",
             "Root and version endpoint translation"),
            ("proxy-v1-slash", self.url + "/proxy/v1/",
             "/proxy/v1/messages", "Proxy endpoint translation"),
        )
        self.state.clear()
        anthropic_worker_actions = []
        for index, (name, api_url, expected_path, translation) in enumerate(
            anthropic_worker_cases,
        ):
            self.state.responses_by_path[expected_path] = anthropic_response([
                result_content(translation),
            ])
            if index != 0:
                anthropic_worker_actions.append({
                    "op": "registry_set", "name": "HD2CT_API_URL",
                    "value": api_url,
                })
            anthropic_worker_actions.extend(self.submit_wait_actions(
                f"anthropic-normalized-{name}", source + name,
            ))
        anthropic_worker_user = dict(user)
        anthropic_worker_user["HD2CT_API_URL"] = self.url
        anthropic_worker_result = self.run_worker(
            anthropic_worker_actions, user=anthropic_worker_user,
            interface_type=3, target_index=2,
        )
        self.assertEqual(self.state.paths, [
            expected_path for _name, _api_url, expected_path, _translation
            in anthropic_worker_cases
        ])
        self.assertEqual([
            item["result"] for item in anthropic_worker_result["actions"]
            if "result" in item
        ], [
            "OK\n" + translation
            for _name, _api_url, _expected_path, translation
            in anthropic_worker_cases
        ])

        for name, interface_type, url, status, response, expected_code in (
            ("chat-http-error", 2, "/custom/chat/failure", 401,
             b"private chat credential diagnostic", "HTTP_401"),
            ("anthropic-error-object", 3, "/custom/anthropic/error", 200,
             b'{"type":"error","error":{"message":"private Anthropic diagnostic"}}',
             "SERVICE_ERROR"),
            ("anthropic-max-tokens", 3, "/custom/anthropic/truncated", 200,
             anthropic_response([result_content("partial")], stop_reason="max_tokens"),
             "BAD_RESPONSE"),
        ):
            with self.subTest(ai_failure=name):
                self.state.clear()
                self.state.status_by_path[url] = status
                self.state.responses_by_path[url] = response
                config = dict(user)
                config["HD2CT_API_URL"] = self.url + url
                failure = self.run_worker(
                    self.submit_wait_actions(name, source + name),
                    user=config, interface_type=interface_type,
                )
                self.assertEqual(failure["actions"][1]["result"],
                                 "ERR\n" + expected_code)
                self.assertNotIn(response.decode("utf-8", errors="ignore"),
                                 json.dumps(failure))

        menu_without_enemy_slang = "".join(
            line for line in self.menu_text(2).splitlines(keepends=True)
            if not line.startswith(ENEMY_SLANG_OPTION_ID + "\t")
        )

        def menu_with_enemy_slang(*values: str) -> str:
            return menu_without_enemy_slang + "".join(
                f"{ENEMY_SLANG_OPTION_ID}\t{value}\n" for value in values
            )

        self.state.clear()
        self.state.responses_by_path["/toggle/chat"] = chat_response(
            "Toggle chat translation",
        )
        chat_toggle_actions = self.submit_wait_actions("enemy-chat-on", source)
        for token, contents in (
            ("enemy-chat-off", self.menu_text(2, enemy_slang=False)),
            ("enemy-chat-invalid", menu_with_enemy_slang("invalid")),
            ("enemy-chat-duplicate-on", menu_with_enemy_slang("false", "true")),
            ("enemy-chat-duplicate-off", menu_with_enemy_slang("true", "false")),
            ("enemy-chat-missing", menu_without_enemy_slang),
            ("enemy-chat-backup", "unparseable primary"),
        ):
            chat_toggle_actions.extend([
                {"op": "target_file", "contents": contents},
                *self.submit_wait_actions(token, source),
            ])
        chat_toggle = self.run_worker(
            chat_toggle_actions,
            user={
                "HD2CT_API_URL": self.url + "/toggle/chat",
                "HD2CT_MODEL": "fixture-model",
                "HD2CT_API_KEY": "fake-api-key",
            },
            interface_type=2,
            target_contents=self.menu_text(2, enemy_slang=True),
            backup_contents=self.menu_text(2, enemy_slang=False),
        )
        self.assertEqual(chat_toggle["cache_count"], 2)
        self.assertEqual(self.state.paths, ["/toggle/chat", "/toggle/chat"])
        self.assertEqual(len(self.state.payloads), 2)
        chat_on_prompt = self.state.payloads[0]["messages"][0]["content"]
        chat_off_prompt = self.state.payloads[1]["messages"][0]["content"]
        self.assertIn("Charger=牛", chat_on_prompt)
        self.assertNotIn("Charger=牛", chat_off_prompt)
        for retained_rule in ("lol=哈哈", "brb=马上回来", "idk=不知道", "reinforce=增援"):
            self.assertIn(retained_rule, chat_off_prompt)
        self.assertEqual(
            [item["result"] for item in chat_toggle["actions"] if "result" in item],
            ["OK\nToggle chat translation"] * 7,
        )

        self.state.clear()
        anthropic_path = "/toggle/anthropic/v1/messages"
        self.state.responses_by_path[anthropic_path] = anthropic_response([
            result_content("Toggle Anthropic translation"),
        ])
        anthropic_toggle_actions = self.submit_wait_actions(
            "enemy-anthropic-on", source,
        )
        anthropic_toggle_actions.extend([
            {"op": "target_file", "contents": self.menu_text(3, enemy_slang=False)},
            *self.submit_wait_actions("enemy-anthropic-off", source),
            {"op": "target_file", "contents": self.menu_text(3, enemy_slang=True)},
            *self.submit_wait_actions("enemy-anthropic-on-cache", source),
        ])
        anthropic_toggle = self.run_worker(
            anthropic_toggle_actions,
            user={
                "HD2CT_API_URL": self.url + anthropic_path,
                "HD2CT_MODEL": "fixture-model",
                "HD2CT_API_KEY": "fake-api-key",
            },
            interface_type=3,
        )
        self.assertEqual(anthropic_toggle["cache_count"], 2)
        self.assertEqual(self.state.paths, [anthropic_path, anthropic_path])
        anthropic_on_prompt = self.state.payloads[0]["system"]
        anthropic_off_prompt = self.state.payloads[1]["system"]
        self.assertIn("Charger=牛", anthropic_on_prompt)
        self.assertIn("lol=哈哈", anthropic_off_prompt)
        self.assertIn("reinforce=增援", anthropic_off_prompt)
        self.assertNotIn("Charger=牛", anthropic_off_prompt)

        for target_index, target_name in ((2, "繁體中文"), (3, "英语")):
            with self.subTest(target_language=target_name):
                self.state.clear()
                self.state.responses_by_path["/toggle/nonchinese"] = chat_response(
                    "Non-Chinese target translation",
                )
                nonchinese_actions = self.submit_wait_actions(
                    f"enemy-disabled-target-{target_index}", source,
                )
                nonchinese_actions.extend([
                    {
                        "op": "target_file",
                        "contents": self.menu_text(
                            2, target_index=target_index, enemy_slang=False,
                        ),
                    },
                    *self.submit_wait_actions(
                        f"enemy-disabled-target-cache-{target_index}", source,
                    ),
                ])
                nonchinese = self.run_worker(
                    nonchinese_actions,
                    user={
                        "HD2CT_API_URL": self.url + "/toggle/nonchinese",
                        "HD2CT_MODEL": "fixture-model",
                        "HD2CT_API_KEY": "fake-api-key",
                    },
                    interface_type=2,
                    target_index=target_index,
                    enemy_slang=True,
                )
                self.assertEqual(nonchinese["cache_count"], 1)
                self.assertEqual(len(self.state.payloads), 1)
                target_prompt = self.state.payloads[0]["messages"][0]["content"]
                self.assertIn("目标语言为" + target_name, target_prompt)
                for chinese_rule in ("lol=哈哈", "Charger=牛", "reinforce=增援"):
                    self.assertNotIn(chinese_rule, target_prompt)

        self.state.clear()
        machine_endpoint = "/fixture/machine-8/language/translate/v2"
        self.state.responses_by_path[machine_endpoint] = google_response(
            "Google translation",
        )
        machine_actions = self.endpoint_setup_actions()
        machine_actions.extend(self.submit_wait_actions("enemy-machine-on", source))
        machine_actions.extend([
            {"op": "target_file", "contents": self.menu_text(8, enemy_slang=False)},
            *self.submit_wait_actions("enemy-machine-off", source),
        ])
        machine_toggle = self.run_worker(
            machine_actions,
            user={"HD2CT_API_KEY": "fake-api-key"},
            interface_type=8,
        )
        self.assertEqual(machine_toggle["cache_count"], 1)
        self.assertEqual(self.state.paths, [machine_endpoint])

    def test_free_defaults_skips_and_async_worker_lifecycle(self) -> None:
        free_source = "Extraction point is clear."
        invalid_user = {
            "HD2CT_API_URL": "ftp://wrong.invalid/" + "x" * 1200,
            "HD2CT_MODEL": "m" * 300,
            "HD2CT_API_KEY": "bad key",
            "HD2CT_APP_ID": "bad app id",
            "HD2CT_REGION": "bad_region",
        }
        no_interface_text = self.menu_text(None)
        self.state.responses_by_path["/ttranslatev3"] = bing_response("必应翻译")
        default = self.run_worker(
            self.submit_wait_actions("free-default", free_source),
            user=invalid_user, interface_type=None,
        )
        self.assertEqual(default["actions"][1]["result"], "OK\n必应翻译")
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(
            urlsplit(self.state.paths[0]).path,
            "/ttranslatev3",
        )
        self.assertEqual(
            urlsplit(self.state.paths[0]).query,
            "isVertical=1&&IG=0123456789ABCDEF&IID=translator.5022.1",
        )
        self.assertEqual(self.state.payloads[0], {
            "fromLang": "auto-detect", "text": free_source, "to": "zh-Hans",
            "key": "1790000000000", "token": "fixture-token",
        })
        self.assertTrue(self.state.content_types[0].lower().startswith(
            "application/x-www-form-urlencoded",
        ))
        self.assertEqual(default["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
        self.assertNotIn(INTERFACE_OPTION_ID, no_interface_text)

        self.state.clear()
        same_source = "Already in the target language."
        self.state.responses_by_path["/ttranslatev3"] = bing_response(same_source)
        same_text = self.run_worker(
            self.submit_wait_actions("free-same-text", same_source),
            user=invalid_user, interface_type=1,
        )
        self.assertEqual(same_text["actions"][1]["result"], "SKIP\n")
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/ttranslatev3")

        self.state.clear()
        self.state.responses_by_path["/ttranslatev3"] = bing_response(
            "invalid menu falls back to FREE",
        )
        bad_menu = self.run_worker(
            [{"op": "target_file",
              "contents": self.menu_text(None)
              + f"{INTERFACE_OPTION_ID}\t9\n"},
             *self.submit_wait_actions(
                 "free-invalid-choice", "Fallback",
             ),
             {"op": "read_counts"}],
            user=invalid_user, interface_type=None,
        )
        self.assertEqual(bad_menu["actions"][2]["result"],
                         "OK\ninvalid menu falls back to FREE")
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/ttranslatev3")
        self.assertEqual(bad_menu["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })

        self.state.clear()
        skipped = self.run_worker(
            [
                {"op": "submit", "token": "disabled-free", "body_hex": "6869"},
                {"op": "wait", "token": "disabled-free"},
                {"op": "target_file",
                 "contents": self.menu_text(1, enabled=True, ignore_self=True)},
                {"op": "submit", "token": "self-free", "body_hex": "6869",
                 "flags": 1},
                {"op": "wait", "token": "self-free"},
                {"op": "read_counts"},
            ],
            user=invalid_user, interface_type=1, enabled=False,
        )
        self.assertEqual(skipped["actions"][1]["result"], "SKIP\n")
        self.assertEqual(skipped["actions"][4]["result"], "SKIP\n")
        self.assertEqual(skipped["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
        self.assertEqual(self.state.paths, [])
        self.assertEqual(self.state.get_paths, [])

        self.state.clear()
        self.state.responses_by_path["/ttranslatev3"] = bing_response("async translation")
        self.state.get_delay = 0.5
        async_result = self.run_worker(
            [
                {"op": "submit", "token": "async-free", "body_hex": "6869"},
                {"op": "poll", "token": "async-free"},
                {"op": "wait", "token": "async-free"},
            ],
            user=invalid_user, interface_type=1,
        )
        self.assertEqual(async_result["actions"][-1]["result"],
                         "OK\nasync translation")
        self.assertLess(async_result["actions"][-3]["elapsed_ms"], 200.0)
        self.assertIsNone(async_result["actions"][-2]["result"])
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/ttranslatev3")

        self.state.clear()
        cancel_marker = self.temp_path / "cancel-request-started"
        cancel_marker.unlink(missing_ok=True)
        self.state.request_started_marker = cancel_marker
        self.state.responses_by_path["/ttranslatev3"] = bing_response("cancelled")
        self.state.get_delay = 0.5
        cancelled = self.run_worker(
            [
                {"op": "submit", "token": "cancel-free", "body_hex": "6869"},
                {"op": "wait_file", "path": str(cancel_marker)},
                {"op": "cancel", "token": "cancel-free"},
                {"op": "sleep", "seconds": 0.7},
                {"op": "poll", "token": "cancel-free"},
            ],
            user=invalid_user, interface_type=1,
        )
        self.assertTrue(cancelled["actions"][1]["found"])
        self.assertEqual(cancelled["actions"][2]["cancelled"], 1)
        self.assertIsNone(cancelled["actions"][4]["result"])
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(self.state.paths, [])
        self.assertTrue(self.state.request_finished.wait(2.0))

        self.state.clear()
        timeout_marker = self.temp_path / "timeout-request-started"
        timeout_marker.unlink(missing_ok=True)
        self.state.request_started_marker = timeout_marker
        self.state.responses_by_path["/ttranslatev3"] = bing_response("late translation")
        self.state.get_delay = 0.5
        timeout_result = self.run_worker(
            [
                {"op": "submit", "token": "timeout-free", "body_hex": "6869"},
                {"op": "wait_file", "path": str(timeout_marker)},
                {"op": "expire_active", "token": "timeout-free"},
                {"op": "poll", "token": "timeout-free"},
            ],
            user=invalid_user, interface_type=1,
        )
        self.assertTrue(timeout_result["actions"][-2]["expired"])
        self.assertEqual(timeout_result["actions"][-1]["result"], "ERR\nTIMEOUT")
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(self.state.paths, [])
        self.assertTrue(self.state.request_finished.wait(2.0))

        self.state.clear()
        self.state.get_response = b"<html>missing Bing translation markers</html>"
        malformed = self.run_worker(
            self.submit_wait_actions("free-bad-page", free_source),
            user=invalid_user, interface_type=1,
        )
        self.assertEqual(malformed["actions"][1]["result"], "ERR\nBAD_RESPONSE")
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        bootstrap_failure = self.run_worker(
            [{"op": "submit", "token": "bootstrap-failure", "body_hex": "6869"},
             {"op": "wait", "token": "bootstrap-failure"},
             {"op": "read_counts"}],
            user=invalid_user, interface_type=1,
            force_bootstrap_failure=True,
        )
        self.assertEqual(bootstrap_failure["actions"][1]["result"],
                         "ERR\nSERVICE_ERROR")
        self.assertEqual(bootstrap_failure["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
        self.assertEqual(self.state.get_paths, [])
        self.assertEqual(self.state.paths, [])
