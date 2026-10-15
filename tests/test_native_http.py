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
BUILD_SCRIPT = ROOT / "tools" / "build_native_http.py"
WINDOWS = os.name == "nt"
NATIVE_TEST_ROOT = ROOT / "artifacts" / "validation"
sys.path.insert(0, str(ROOT / "tools"))
import build_native_http
from scripts import target_languages

NATIVE_SOURCES = build_native_http.NATIVE_SOURCES
TARGET_LANGUAGE_CATALOGUE = target_languages.load_catalogue()
INTERFACE_OPTION_ID = TARGET_LANGUAGE_CATALOGUE["menu_options"]["interface_type"]["option_id"]
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
lib.fixture_NormalizeURL.argtypes = [
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_NormalizeURL.restype = ctypes.c_int
lib.fixture_PrepareConfig.argtypes = [
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_PrepareConfig.restype = ctypes.c_uint32
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

for hive_name, hive_id in (("user", 0), ("machine", 1)):
    for name, value in fixture.get(hive_name, {}).items():
        if not lib.fixture_SetRegistryValue(hive_id, name, value):
            raise RuntimeError("fixture registry value was rejected")

if fixture.get("__force_bootstrap_create_failure"):
    lib.fixture_ForceBootstrapCreateFailure()
worker_failure_at = fixture.get("__force_worker_create_failure_at", 0)
if worker_failure_at:
    lib.fixture_ForceWorkerCreateFailure(worker_failure_at)

player_buffers = {}

def submit(action):
    body = bytes.fromhex(action["body_hex"])
    backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
    player_name = action.get("player_name")
    player_pointer = None
    if player_name is not None:
        encoded_name = player_name.encode("utf-8")
        player_buffers[action["token"]] = ctypes.create_string_buffer(
            encoded_name, len(encoded_name) + 1,
        )
        player_pointer = ctypes.cast(
            player_buffers[action["token"]], ctypes.c_char_p,
        )
    started = time.perf_counter()
    accepted = lib.HD2CT_Submit(
        action["token"].encode("ascii"), ctypes.cast(backing, ctypes.c_void_p),
        len(body), action.get("flags", 0), player_pointer,
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
        if not accepted and not action.get("allow_reject", False):
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
        output = ctypes.create_string_buffer(2049)
        accepted = lib.fixture_NormalizeURL(
            action["adapter_id"], action["url"].encode("utf-8"),
            ctypes.cast(output, ctypes.c_void_p), len(output),
        )
        results.append({
            "accepted": accepted,
            "url": output.value.decode("utf-8"),
        })
    elif op == "prepare_config":
        output = ctypes.create_string_buffer(2049)
        status = lib.fixture_PrepareConfig(
            action["interface_type"], ctypes.cast(output, ctypes.c_void_p),
            len(output),
        )
        results.append({
            "status": status,
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

__declspec(dllexport) int __cdecl fixture_NormalizeURL(
    uint32_t adapter_id, const char *url, char *output, uint32_t capacity)
{
    return hd2ct_normalize_url(url, adapter_id, output, (size_t)capacity);
}

__declspec(dllexport) uint32_t __cdecl fixture_PrepareConfig(
    uint32_t interface_type, char *output, uint32_t capacity)
{
    HD2CT_WorkerJob job;
    uint32_t status;
    ZeroMemory(&job, sizeof(job));
    job.target_language = hd2ct_default_target_language();
    status = hd2ct_prepare_job_config(&job, interface_type);
    if (status == HD2CT_STATUS_READY && output != NULL &&
        strlen(job.url) + 1u <= capacity) {
        memcpy(output, job.url, strlen(job.url) + 1u);
    }
    SecureZeroMemory(&job, sizeof(job));
    return status;
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


def free_success_response(text: str) -> bytes:
    return json.dumps(
        {"success": True, "data": {"text": text, "reason": ""}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def free_failure_response(reason: str) -> bytes:
    return json.dumps(
        {"success": False, "data": {"text": "", "reason": reason}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.payloads: list[dict | list] = []
        self.content_types: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.response = chat_response("default")
        self.responses_by_path: dict[str, bytes] = {}
        self.responses_by_kind: dict[str, bytes] = {}
        self.status = 200
        self.status_by_path: dict[str, int] = {}
        self.delay = 0.0
        self.delay_by_path: dict[str, float] = {}
        self.request_finished = threading.Event()
        self.request_started_marker: Path | None = None

    @staticmethod
    def payload_kind(payload: object) -> str | None:
        if isinstance(payload, list):
            return "bing"
        if not isinstance(payload, dict):
            return None
        if "key1" in payload:
            return "free"
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
            self.payloads.clear()
            self.content_types.clear()
            self.headers.clear()
            self.response = chat_response("default")
            self.responses_by_path.clear()
            self.responses_by_kind.clear()
            self.status = 200
            self.status_by_path.clear()
            self.delay = 0.0
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
                    state.paths.append(self.path)
                self.send_error(404)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True,
        )
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.free_url = cls.url + "/free/translate"

        cls.free_config = cls.temp_path / "free-service-test.json"
        cls.free_config.write_text(
            json.dumps({
                "endpoint_url": cls.free_url,
                "key1": "00000000-0000-4000-8000-000000000001",
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        cls.production_dll = cls.temp_path / "hd2ct_http.dll"
        cls.meta = cls.temp_path / "hd2ct_http.meta.json"
        compiler = shutil.which("gcc") or "gcc"
        completed = subprocess.run(
            [
                sys.executable, str(BUILD_SCRIPT), "--cc", compiler,
                "--output", str(cls.production_dll), "--meta", str(cls.meta),
                "--free-config", str(cls.free_config),
            ],
            cwd=ROOT, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=45,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.meta_json = json.loads(cls.meta.read_text(encoding="utf-8"))

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
    ) -> str:
        menu = TARGET_LANGUAGE_CATALOGUE["menu_options"]
        values = [
            f"{TARGET_LANGUAGE_CATALOGUE['option_id']}\t{target_index}\n",
            f"{menu['enabled']['option_id']}\t{'true' if enabled else 'false'}\n",
            f"{menu['ignore_self']['option_id']}\t{'true' if ignore_self else 'false'}\n",
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
        compiler = self.meta_json["compiler"]
        command = [
            compiler, "-std=c11", "-O2", "-shared", "-s", "-static-libgcc",
            "-finput-charset=UTF-8", "-fexec-charset=UTF-8",
            "-D_WIN32_WINNT=0x0601", "-DWINVER=0x0601", "-DHD2CT_TESTING",
            "-DCJSON_NESTING_LIMIT=32", "-DCJSON_HIDE_SYMBOLS",
            "-Wl,--exclude-all-symbols", "-I", str(ROOT / "native"),
            "-I", str(self.temp_path), "-include", str(registry_shim),
            str(wrapper), *(str(source) for source in NATIVE_SOURCES[1:]),
            "-o", str(dll), "-lwinhttp", "-ladvapi32", "-lbcrypt",
            f'-DHD2CT_FREE_SERVICE_URL="{self.free_url}"',
        ]
        completed = subprocess.run(
            command, cwd=ROOT, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=45,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        return dll

    def private_free_endpoint_test_dll(self) -> Path:
        self.environment_test_dll()
        dll = self.temp_path / "hd2ct_http_private_free_endpoint_test.dll"
        if dll.is_file():
            return dll
        wrapper = self.temp_path / "hd2ct_http_environment_test.c"
        registry_shim = self.temp_path / "hd2ct_http_registry_test_shim.h"
        compiler = self.meta_json["compiler"]
        command = [
            compiler, "-std=c11", "-O2", "-shared", "-s", "-static-libgcc",
            "-finput-charset=UTF-8", "-fexec-charset=UTF-8",
            "-D_WIN32_WINNT=0x0601", "-DWINVER=0x0601", "-DHD2CT_TESTING",
            "-DCJSON_NESTING_LIMIT=32", "-DCJSON_HIDE_SYMBOLS",
            "-Wl,--exclude-all-symbols", "-I", str(ROOT / "native"),
            "-I", str(self.temp_path), "-include", str(registry_shim),
            str(wrapper), *(str(source) for source in NATIVE_SOURCES[1:]),
            "-o", str(dll), "-lwinhttp", "-ladvapi32", "-lbcrypt",
            '-DHD2CT_FREE_SERVICE_URL="http://198.51.100.1/translate"',
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
        dll_path: Path | None = None,
    ) -> dict:
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-menu-values-", dir=str(NATIVE_TEST_ROOT),
        ) as directory:
            values_path = Path(directory) / "ModOptionsMenu.values"
            fixture = {
                "user": user or {},
                "machine": machine or {},
                "__target_values_path": str(values_path),
                "__target_primary": self.menu_text(
                    interface_type, enabled, ignore_self, target_index,
                ),
                "__force_bootstrap_create_failure": force_bootstrap_failure,
                "__force_worker_create_failure_at": force_worker_failure_at,
            }
            command = [
                sys.executable, "-c", ENVIRONMENT_CHILD,
                str(dll_path or self.environment_test_dll()),
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

    def submit_wait_actions(
        self,
        token: str,
        body: str,
        player_name: str | None = None,
    ) -> list[dict]:
        submit_action = {
            "op": "submit", "token": token,
            "body_hex": body.encode("utf-8").hex(),
        }
        if player_name is not None:
            submit_action["player_name"] = player_name
        return [submit_action, {"op": "wait", "token": token}]

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
                "Fixture Player" if interface_type == 1 else None,
            ))
        actions.extend([
            {"op": "registry_set", "name": "HD2CT_API_KEY",
             "value": "changed-key"},
            {"op": "target_file", "contents": self.menu_text(2)},
            *self.submit_wait_actions("switch-chat-changed-key", source),
        ])
        actions.append({"op": "read_counts"})
        self.state.responses_by_kind = {
            "chat": chat_response("Chat result"),
            "anthropic": anthropic_response([
                result_content("Anthropic result"),
            ]),
            "free": free_success_response("FREE result"),
        }
        self.state.responses_by_path.update({
            "/fixture/machine-4/api/trans/vip/translate": baidu_response("Baidu result"),
            "/fixture/machine-5/api": youdao_response("Youdao result"),
            "/fixture/machine-6/translate": bing_response("Bing CN result"),
            "/fixture/machine-7/translate": bing_response("Bing global result"),
            "/fixture/machine-8/language/translate/v2": google_response("Google result"),
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
        self.assertEqual(self.state.payloads[0]["response_format"], CHAT_RESPONSE_FORMAT)
        self.assertEqual(self.state.payloads[1]["max_tokens"], 4096)
        self.assertEqual(self.state.payloads[6], {
            "q": source, "target": "zh-CN", "format": "text",
        })
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

    def test_free_defaults_skips_and_async_worker_lifecycle(self) -> None:
        free_source = "Extraction point is clear."
        private_http_free = "http://198.51.100.1/translate"
        private_endpoint = self.run_worker(
            [
                {"op": "normalize_url", "adapter_id": 6,
                 "url": private_http_free},
                {"op": "normalize_url", "adapter_id": 6,
                 "url": "http://198.51.100.2/translate"},
                {"op": "prepare_config", "interface_type": 1},
                {"op": "prepare_config", "interface_type": 2},
            ],
            user={
                "HD2CT_API_URL": "http://198.51.100.1/v1/chat/completions",
                "HD2CT_MODEL": "fixture-model",
                "HD2CT_API_KEY": "fixture-key",
            },
            interface_type=1,
            dll_path=self.private_free_endpoint_test_dll(),
        )
        self.assertEqual(private_endpoint["actions"][0], {
            "accepted": 1, "url": private_http_free,
        })
        self.assertEqual(private_endpoint["actions"][1]["accepted"], 0)
        self.assertEqual(private_endpoint["actions"][2], {
            "status": 0, "url": private_http_free,
        })
        self.assertEqual(private_endpoint["actions"][3], {
            "status": 2, "url": "",
        })
        self.assertEqual(self.state.paths, [])

        invalid_user = {
            "HD2CT_API_URL": "ftp://wrong.invalid/" + "x" * 1200,
            "HD2CT_MODEL": "m" * 300,
            "HD2CT_API_KEY": "bad key",
            "HD2CT_APP_ID": "bad app id",
            "HD2CT_REGION": "bad_region",
        }
        self.state.responses_by_path["/free/translate"] = free_success_response(
            free_source,
        )
        no_interface_text = self.menu_text(None)
        default = self.run_worker(
            self.submit_wait_actions("free-default", free_source, "Fixture Player")
            + [
                {"op": "submit", "token": "free-default-cache",
                 "body_hex": free_source.encode("utf-8").hex(),
                 "player_name": "Fixture Player"},
                {"op": "wait", "token": "free-default-cache"},
                {"op": "read_counts"},
            ],
            user=invalid_user, interface_type=None,
        )
        self.assertEqual(default["actions"][1]["result"], "SKIP\n")
        self.assertEqual(default["actions"][3]["result"], "SKIP\n")
        self.assertEqual(self.state.paths, ["/free/translate"])
        self.assertEqual(default["cache_count"], 1)
        self.assertEqual(default["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
        self.assertNotIn(INTERFACE_OPTION_ID, no_interface_text)

        self.state.clear()
        self.state.responses_by_path["/free/translate"] = free_success_response(
            "invalid menu falls back to FREE",
        )
        bad_menu = self.run_worker(
            [{"op": "target_file",
              "contents": self.menu_text(None)
              + f"{INTERFACE_OPTION_ID}\t9\n"},
             *self.submit_wait_actions(
                 "free-invalid-choice", "Fallback", "Fixture Player",
             ),
             {"op": "read_counts"}],
            user=invalid_user, interface_type=None,
        )
        self.assertEqual(bad_menu["actions"][2]["result"],
                         "OK\ninvalid menu falls back to FREE")
        self.assertEqual(self.state.paths, ["/free/translate"])
        self.assertEqual(bad_menu["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })

        self.state.clear()
        skipped = self.run_worker(
            [
                {"op": "submit", "token": "disabled-google", "body_hex": "6869"},
                {"op": "wait", "token": "disabled-google"},
                {"op": "target_file",
                 "contents": self.menu_text(8, enabled=True, ignore_self=True)},
                {"op": "submit", "token": "self-google", "body_hex": "6869",
                 "flags": 1},
                {"op": "wait", "token": "self-google"},
                {"op": "read_counts"},
            ],
            user={}, interface_type=8, enabled=False,
        )
        self.assertEqual(skipped["actions"][1]["result"], "SKIP\n")
        self.assertEqual(skipped["actions"][4]["result"], "SKIP\n")
        self.assertEqual(skipped["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        self.state.responses_by_path["/fixture/machine-8/language/translate/v2"] = (
            google_response("async translation")
        )
        self.state.delay_by_path["/fixture/machine-8/language/translate/v2"] = 0.5
        async_result = self.run_worker(
            self.endpoint_setup_actions() + [
                {"op": "submit", "token": "async-google", "body_hex": "6869"},
                {"op": "poll", "token": "async-google"},
                {"op": "wait", "token": "async-google"},
            ],
            user={"HD2CT_API_KEY": "fake-key"},
            interface_type=8,
        )
        self.assertEqual(async_result["actions"][-1]["result"],
                         "OK\nasync translation")
        self.assertLess(async_result["actions"][-3]["elapsed_ms"], 200.0)
        self.assertIsNone(async_result["actions"][-2]["result"])

        self.state.clear()
        cancel_marker = self.temp_path / "cancel-request-started"
        cancel_marker.unlink(missing_ok=True)
        self.state.request_started_marker = cancel_marker
        self.state.responses_by_path[
            "/fixture/machine-8/language/translate/v2"
        ] = google_response("cancelled translation")
        self.state.delay_by_path[
            "/fixture/machine-8/language/translate/v2"
        ] = 0.5
        cancelled = self.run_worker(
            self.endpoint_setup_actions() + [
                {"op": "submit", "token": "cancel-google", "body_hex": "6869"},
                {"op": "wait_file", "path": str(cancel_marker)},
                {"op": "cancel", "token": "cancel-google"},
                {"op": "poll", "token": "cancel-google"},
            ],
            user={"HD2CT_API_KEY": "fake-key"},
            interface_type=8,
        )
        self.assertTrue(cancelled["actions"][6]["found"])
        self.assertEqual(cancelled["actions"][7]["cancelled"], 1)
        self.assertIsNone(cancelled["actions"][8]["result"])
        self.assertEqual(
            self.state.paths,
            ["/fixture/machine-8/language/translate/v2"],
        )
        self.assertTrue(self.state.request_finished.wait(2.0))

        self.state.clear()
        self.state.request_started_marker = self.temp_path / "timeout-request-started"
        self.state.request_started_marker.unlink(missing_ok=True)
        self.state.responses_by_path["/fixture/machine-8/language/translate/v2"] = (
            google_response("late translation")
        )
        self.state.delay_by_path["/fixture/machine-8/language/translate/v2"] = 0.5
        timeout_result = self.run_worker(
            self.endpoint_setup_actions() + [
                {"op": "submit", "token": "timeout-google", "body_hex": "6869"},
                {"op": "wait_file", "path": str(self.state.request_started_marker)},
                {"op": "expire_active", "token": "timeout-google"},
                {"op": "poll", "token": "timeout-google"},
            ],
            user={"HD2CT_API_KEY": "fake-key"},
            interface_type=8,
        )
        self.assertTrue(timeout_result["actions"][-2]["expired"])
        self.assertEqual(timeout_result["actions"][-1]["result"], "ERR\nTIMEOUT")
        self.state.request_finished.wait(2.0)

        self.state.clear()
        bootstrap_failure = self.run_worker(
            [{"op": "submit", "token": "bootstrap-failure", "body_hex": "6869"},
             {"op": "wait", "token": "bootstrap-failure"},
             {"op": "read_counts"}],
            user={"HD2CT_API_KEY": "fake-key"},
            interface_type=8,
            force_bootstrap_failure=True,
        )
        self.assertEqual(bootstrap_failure["actions"][1]["result"],
                         "ERR\nSERVICE_ERROR")
        self.assertEqual(bootstrap_failure["read_counts"], {
            name: 0 for name in REGISTRY_READ_IDS.values()
        })
