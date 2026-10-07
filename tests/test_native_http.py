"""原生 HTTP worker 的本机回环验证，不读取真实配置或调用付费接口。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "tools" / "build_native_http.py"
WINDOWS = os.name == "nt"
sys.path.insert(0, str(ROOT / "tools"))
import build_native_http
import target_languages

NATIVE_SOURCES = build_native_http.NATIVE_SOURCES
NATIVE_TEST_ROOT = ROOT / "artifacts" / "validation"
TARGET_LANGUAGE_CATALOGUE = target_languages.load_catalogue()
AI_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "hd2ct_translation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_target_language": {"type": "boolean"},
                "translation": {"type": "string"},
            },
            "required": ["is_target_language", "translation"],
            "additionalProperties": False,
        },
    },
}

ENVIRONMENT_CHILD = r"""
import ctypes
import json
import os
import sys
import time
import threading
from pathlib import Path

dll_path, registry_json = sys.argv[1], sys.argv[2]
arguments = sys.argv[3:]
expected_config = None
actions = []
if arguments and arguments[0] != "--actions":
    expected_config = json.loads(arguments.pop(0))
if arguments:
    if arguments[0] != "--actions" or len(arguments) != 2:
        raise RuntimeError("invalid environment fixture arguments")
    actions = json.loads(arguments[1])
for name in tuple(os.environ):
    if name.upper().startswith("HD2CT_"):
        del os.environ[name]
os.environ.update({
    "HD2CT_API_URL": "http://127.0.0.1:1/process-fallback",
    "HD2CT_MODEL": "fake-process-model",
    "HD2CT_API_KEY": "fake-process-key",
    "HD2CT_APP_ID": "fake-process-app-id",
    "HD2CT_ENABLED": "1",
    "HD2CT_TIMEOUT_SECONDS": "77",
})

lib = ctypes.CDLL(dll_path)
lib.fixture_ClearRegistry.argtypes = []
lib.fixture_ClearRegistry.restype = None
lib.fixture_SetRegistryDelay.argtypes = [ctypes.c_uint32]
lib.fixture_SetRegistryDelay.restype = None
lib.fixture_SetRegistryValue.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p]
lib.fixture_SetRegistryValue.restype = ctypes.c_int
lib.fixture_ConfigMatches.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
]
lib.fixture_ConfigMatches.restype = ctypes.c_int
lib.fixture_ReadRuntimeSettings.argtypes = [
    ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint32),
]
lib.fixture_ReadRuntimeSettings.restype = None
lib.fixture_RequestDeadlineRemaining.argtypes = [ctypes.c_char_p]
lib.fixture_RequestDeadlineRemaining.restype = ctypes.c_uint64
lib.fixture_NormalizeUrl.argtypes = [
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_NormalizeUrl.restype = ctypes.c_uint32
lib.fixture_CacheCount.argtypes = []
lib.fixture_CacheCount.restype = ctypes.c_uint32
lib.fixture_RateCount.argtypes = []
lib.fixture_RateCount.restype = ctypes.c_uint32
lib.fixture_Initialized.argtypes = []
lib.fixture_Initialized.restype = ctypes.c_uint32
lib.fixture_Status.argtypes = []
lib.fixture_Status.restype = ctypes.c_uint32
lib.fixture_Enabled.argtypes = []
lib.fixture_Enabled.restype = ctypes.c_uint32
lib.fixture_BootstrapCount.argtypes = []
lib.fixture_BootstrapCount.restype = ctypes.c_uint32
lib.fixture_ForceWorkerFailure.argtypes = []
lib.fixture_ForceWorkerFailure.restype = None
lib.fixture_ForceBootstrapCreateFailure.argtypes = []
lib.fixture_ForceBootstrapCreateFailure.restype = None
lib.fixture_ForceWorkerCreateFailure.argtypes = [ctypes.c_uint32]
lib.fixture_ForceWorkerCreateFailure.restype = None
lib.fixture_SetTargetValuesPath.argtypes = [ctypes.c_wchar_p]
lib.fixture_SetTargetValuesPath.restype = ctypes.c_int
lib.fixture_StartLockHold.argtypes = [ctypes.c_uint32]
lib.fixture_StartLockHold.restype = ctypes.c_int
lib.fixture_ReleaseLock.argtypes = []
lib.fixture_ReleaseLock.restype = None
lib.HD2CT_Submit.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
lib.HD2CT_Submit.restype = ctypes.c_uint32
lib.HD2CT_Poll.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
]
lib.HD2CT_Poll.restype = ctypes.c_uint32
lib.HD2CT_Cancel.argtypes = [ctypes.c_char_p]
lib.HD2CT_Cancel.restype = ctypes.c_uint32

lib.fixture_ClearRegistry()
fixture = json.loads(registry_json)
registry_delay = fixture.pop("__registry_delay_ms", 0)
target_values_path = fixture.pop("__target_values_path", None)
has_target_primary = "__target_primary" in fixture
target_primary = fixture.pop("__target_primary", None)
has_target_backup = "__target_backup" in fixture
target_backup = fixture.pop("__target_backup", None)
if fixture.pop("__worker_failure", False):
    lib.fixture_ForceWorkerFailure()
if fixture.pop("__bootstrap_create_failure", False):
    lib.fixture_ForceBootstrapCreateFailure()
worker_create_failure_at = fixture.pop("__worker_create_failure_at", 0)
if worker_create_failure_at:
    lib.fixture_ForceWorkerCreateFailure(worker_create_failure_at)
if target_values_path is not None:
    if not lib.fixture_SetTargetValuesPath(target_values_path):
        raise RuntimeError("test-only target values path was rejected")

def write_target_values_file(kind, contents):
    if target_values_path is None or kind not in ("primary", "backup"):
        raise RuntimeError("target values file override is unavailable")
    path = Path(target_values_path + (".bak" if kind == "backup" else ""))
    if contents is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(contents.encode("utf-8"))

if has_target_primary:
    write_target_values_file("primary", target_primary)
if has_target_backup:
    write_target_values_file("backup", target_backup)
lock_hold_ms = fixture.pop("__lock_hold_ms", 0)
lib.fixture_SetRegistryDelay(registry_delay)
for hive_name, hive in (("user", 0), ("machine", 1)):
    for name, value in fixture.get(hive_name, {}).items():
        if not lib.fixture_SetRegistryValue(hive, name, value):
            raise RuntimeError("fixture registry value was rejected")

def submit(token, body_hex="73616665"):
    body = bytes.fromhex(body_hex)
    backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
    return lib.HD2CT_Submit(
        token.encode("ascii"), ctypes.cast(backing, ctypes.c_void_p), len(body),
    )

def poll(token):
    buffer = ctypes.create_string_buffer(16388)
    written = ctypes.c_uint32(0)
    found = lib.HD2CT_Poll(
        token.encode("ascii"), ctypes.cast(buffer, ctypes.c_void_p),
        len(buffer), ctypes.byref(written),
    )
    if not found:
        return None
    return bytes(buffer.raw[:written.value]).decode("utf-8")

def wait_initialized(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lib.fixture_Initialized() == 2:
            return True
        time.sleep(0.005)
    return lib.fixture_Initialized() == 2

if not actions:
    actions = [
        {"op": "submit", "token": "fixture-bootstrap", "body_hex": "73616665"},
        {"op": "wait", "token": "fixture-bootstrap", "timeout": 2.0},
    ]
action_results = []
for action in actions:
    op = action["op"]
    if op == "submit":
        started = time.perf_counter()
        accepted = submit(action["token"], action.get("body_hex", ""))
        action_results.append({
            "accepted": accepted,
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        })
    elif op in ("poll", "wait"):
        started = time.perf_counter()
        deadline = time.monotonic() + action.get("timeout", 8.0)
        value = poll(action["token"])
        while value is None and op == "wait" and time.monotonic() < deadline:
            time.sleep(0.01)
            value = poll(action["token"])
        action_results.append({
            "result": value,
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        })
    elif op == "cancel":
        action_results.append({
            "cancelled": lib.HD2CT_Cancel(action["token"].encode("ascii")),
        })
    elif op == "cancel_all":
        action_results.append({"cancelled": lib.HD2CT_Cancel(None)})
    elif op == "concurrent_submit":
        results = [None] * len(action["tokens"])
        threads = []
        for index, token in enumerate(action["tokens"]):
            thread = threading.Thread(
                target=lambda i=index, t=token: results.__setitem__(i, submit(t, action.get("body_hex", "73616665")))
            )
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        action_results.append({"accepted": results})
    elif op == "force_worker_failure":
        lib.fixture_ForceWorkerFailure()
        action_results.append({"forced": True})
    elif op == "hold_lock":
        action_results.append({"held": bool(lib.fixture_StartLockHold(lock_hold_ms or action.get("milliseconds", 500)))})
    elif op == "release_lock":
        lib.fixture_ReleaseLock()
        action_results.append({"released": True})
    elif op == "sleep":
        time.sleep(action["seconds"])
        action_results.append({"slept": action["seconds"]})
    elif op == "wait_initialized":
        action_results.append({"initialized": wait_initialized(action.get("timeout", 5.0))})
    elif op == "normalize":
        output = ctypes.create_string_buffer(2049)
        accepted = lib.fixture_NormalizeUrl(
            action["adapter_id"], action["url"].encode("utf-8"),
            ctypes.cast(output, ctypes.c_void_p), len(output),
        )
        action_results.append({"accepted": accepted, "url": output.value.decode("utf-8")})
    elif op == "target_file":
        write_target_values_file(action["kind"], action.get("contents"))
        action_results.append({"written": True})
    elif op == "wait_file":
        marker_path = Path(action["path"])
        deadline = time.monotonic() + action.get("timeout", 5.0)
        while not marker_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.005)
        action_results.append({"found": marker_path.is_file()})
    elif op == "read_settings":
        target_language = ctypes.c_uint32()
        timeout_seconds = ctypes.c_uint32()
        menu_enabled = ctypes.c_uint32()
        lib.fixture_ReadRuntimeSettings(
            ctypes.byref(target_language), ctypes.byref(timeout_seconds),
            ctypes.byref(menu_enabled),
        )
        action_results.append({
            "target_language": target_language.value,
            "timeout_seconds": timeout_seconds.value,
            "menu_enabled": menu_enabled.value,
        })
    elif op == "request_remaining":
        remaining = lib.fixture_RequestDeadlineRemaining(action["token"].encode("ascii"))
        action_results.append({"remaining_ms": int(remaining)})
    else:
        raise RuntimeError("unknown environment fixture action")

if lib.fixture_Initialized() != 0:
    wait_initialized()
if lock_hold_ms:
    lib.fixture_ReleaseLock()
status = lib.fixture_Status()
enabled = lib.fixture_Enabled()
target_language = ctypes.c_uint32()
timeout_seconds = ctypes.c_uint32()
menu_enabled = ctypes.c_uint32()
lib.fixture_ReadRuntimeSettings(
    ctypes.byref(target_language), ctypes.byref(timeout_seconds),
    ctypes.byref(menu_enabled),
)
result = {
    "status": status,
    "enabled": enabled,
    "init_state": lib.fixture_Initialized(),
    "bootstrap_count": lib.fixture_BootstrapCount(),
    "submit": action_results[0].get("accepted") if action_results else None,
    "timeout": timeout_seconds.value,
    "target_language": target_language.value,
    "menu_enabled": menu_enabled.value,
    "cache_count": lib.fixture_CacheCount(),
    "rate_count": lib.fixture_RateCount(),
    "actions": action_results,
}
if expected_config is not None:
    result["selected_config_matches"] = lib.fixture_ConfigMatches(
        expected_config["HD2CT_API_URL"].encode("utf-8"),
        expected_config.get("HD2CT_MODEL", "").encode("utf-8"),
        expected_config.get("HD2CT_API_KEY", "").encode("utf-8"),
        expected_config.get("HD2CT_APP_ID", "").encode("utf-8"),
    )
print(json.dumps(result), flush=True)
"""

ENVIRONMENT_SHIM_C = r"""
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#define WINVER 0x0601
#include <windows.h>
#include <winreg.h>
#include <process.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

#define FIXTURE_VALUE_COUNT 16u
#define FIXTURE_NAME_CAPACITY 64u
#define FIXTURE_VALUE_CAPACITY 4097u
typedef struct FixtureRegistryValue {
    DWORD hive;
    int used;
    wchar_t name[FIXTURE_NAME_CAPACITY];
    wchar_t value[FIXTURE_VALUE_CAPACITY];
} FixtureRegistryValue;

static FixtureRegistryValue fixture_values[FIXTURE_VALUE_COUNT];
static volatile LONG fixture_registry_delay_ms;
static HANDLE fixture_lock_ready;
static HANDLE fixture_lock_release;
static HANDLE fixture_lock_thread_handle;

LSTATUS WINAPI fixture_RegGetValueW(
    HKEY root, LPCWSTR subkey, LPCWSTR name, DWORD flags, LPDWORD type,
    PVOID data, LPDWORD bytes)
{
    DWORD hive;
    DWORD index;
    DWORD required;
    (void)flags;
    {
        LONG delay = InterlockedCompareExchange(&fixture_registry_delay_ms, 0, 0);
        if (delay > 0) Sleep((DWORD)delay);
    }
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
    if (name == NULL || bytes == NULL) {
        return ERROR_INVALID_PARAMETER;
    }
    for (index = 0u; index < FIXTURE_VALUE_COUNT; ++index) {
        FixtureRegistryValue *entry = &fixture_values[index];
        if (entry->used && entry->hive == hive && _wcsicmp(entry->name, name) == 0) {
            required = (DWORD)((wcslen(entry->value) + 1u) * sizeof(wchar_t));
            if (type != NULL) {
                *type = REG_SZ;
            }
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
    InterlockedExchange(&fixture_registry_delay_ms, 0);
}

__declspec(dllexport) void __cdecl fixture_SetRegistryDelay(uint32_t milliseconds)
{
    InterlockedExchange(&fixture_registry_delay_ms, (LONG)milliseconds);
}

__declspec(dllexport) int __cdecl fixture_SetRegistryValue(
    DWORD hive, LPCWSTR name, LPCWSTR value)
{
    DWORD index;
    DWORD free_index = FIXTURE_VALUE_COUNT;
    size_t name_length;
    size_t value_length;
    if (hive > 1u || name == NULL || value == NULL) {
        return 0;
    }
    name_length = wcslen(name);
    value_length = wcslen(value);
    if (name_length >= FIXTURE_NAME_CAPACITY ||
        value_length >= FIXTURE_VALUE_CAPACITY) {
        return 0;
    }
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
    if (free_index == FIXTURE_VALUE_COUNT) {
        return 0;
    }
    fixture_values[free_index].hive = hive;
    fixture_values[free_index].used = 1;
    memcpy(fixture_values[free_index].name, name,
           (name_length + 1u) * sizeof(wchar_t));
    memcpy(fixture_values[free_index].value, value,
           (value_length + 1u) * sizeof(wchar_t));
    return 1;
}

__declspec(dllexport) int __cdecl fixture_ConfigMatches(
    const char *url, const char *model, const char *api_key, const char *app_id)
{
    return url != NULL && model != NULL && api_key != NULL && app_id != NULL &&
        strcmp(g_url, url) == 0 && strcmp(g_model, model) == 0 &&
        strcmp(g_api_key, api_key) == 0 && strcmp(g_app_id, app_id) == 0;
}

__declspec(dllexport) void __cdecl fixture_ReadRuntimeSettings(
    uint32_t *target_language, uint32_t *timeout_seconds, uint32_t *enabled)
{
    HD2CT_RuntimeSettings settings;
    hd2ct_read_applied_settings(&settings);
    if (target_language != NULL) *target_language = settings.target_language;
    if (timeout_seconds != NULL) *timeout_seconds = settings.timeout_seconds;
    if (enabled != NULL) *enabled = settings.enabled;
}

__declspec(dllexport) uint64_t __cdecl fixture_RequestDeadlineRemaining(
    const char *token)
{
    size_t token_length;
    uint32_t i;
    uint64_t now = GetTickCount64();
    uint64_t remaining = 0u;
    if (!hd2ct_valid_token(token, &token_length)) return 0u;
    AcquireSRWLockExclusive(&g_lock);
    for (i = 0u; i < HD2CT_JOB_COUNT; ++i) {
        HD2CT_JobSlot *slot = &g_jobs[i];
        if (slot->state == HD2CT_SLOT_ACTIVE &&
            strlen(slot->token) == token_length &&
            memcmp(slot->token, token, token_length) == 0 &&
            slot->request_deadline_ms > now) {
            remaining = slot->request_deadline_ms - now;
            break;
        }
    }
    ReleaseSRWLockExclusive(&g_lock);
    return remaining;
}

__declspec(dllexport) uint32_t __cdecl fixture_NormalizeUrl(
    uint32_t adapter_id, const char *url, char *out, uint32_t capacity)
{
    return hd2ct_normalize_url(url, adapter_id, out, capacity) ? 1u : 0u;
}

__declspec(dllexport) uint32_t __cdecl fixture_CacheCount(void)
{
    uint32_t count = 0;
    uint32_t i;
    for (i = 0; i < HD2CT_CACHE_COUNT; ++i) {
        if (g_cache[i].used != 0) ++count;
    }
    return count;
}

__declspec(dllexport) uint32_t __cdecl fixture_RateCount(void)
{
    return g_rate_count;
}

__declspec(dllexport) uint32_t __cdecl fixture_Initialized(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_initialized, 0, 0);
}

__declspec(dllexport) uint32_t __cdecl fixture_Status(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_status, 0, 0);
}

__declspec(dllexport) uint32_t __cdecl fixture_Enabled(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_enabled, 0, 0);
}

__declspec(dllexport) uint32_t __cdecl fixture_BootstrapCount(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_test_bootstrap_count, 0, 0);
}

__declspec(dllexport) void __cdecl fixture_ForceWorkerFailure(void)
{
    InterlockedExchange(&g_test_fail_worker_session, 1);
}

__declspec(dllexport) int __cdecl fixture_SetTargetValuesPath(const wchar_t *path)
{
    return hd2ct_test_set_values_file_path(path);
}

__declspec(dllexport) void __cdecl fixture_ForceBootstrapCreateFailure(void)
{
    InterlockedExchange(&g_test_fail_bootstrap_create, 1);
}

__declspec(dllexport) void __cdecl fixture_ForceWorkerCreateFailure(uint32_t ordinal)
{
    InterlockedExchange(&g_test_fail_worker_create_at, (LONG)ordinal);
}

static unsigned __stdcall fixture_lock_thread_main(void *parameter)
{
    DWORD milliseconds = *(DWORD *)parameter;
    AcquireSRWLockExclusive(&g_lock);
    SetEvent(fixture_lock_ready);
    (void)WaitForSingleObject(fixture_lock_release, milliseconds);
    ReleaseSRWLockExclusive(&g_lock);
    return 0;
}

__declspec(dllexport) int __cdecl fixture_StartLockHold(uint32_t milliseconds)
{
    DWORD timeout = milliseconds;
    uintptr_t thread;
    if (fixture_lock_ready != NULL || fixture_lock_release != NULL ||
        fixture_lock_thread_handle != NULL) return 0;
    fixture_lock_ready = CreateEventW(NULL, TRUE, FALSE, NULL);
    fixture_lock_release = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (fixture_lock_ready == NULL || fixture_lock_release == NULL) return 0;
    thread = _beginthreadex(NULL, 0, fixture_lock_thread_main, &timeout, 0, NULL);
    if (thread == 0) return 0;
    fixture_lock_thread_handle = (HANDLE)thread;
    if (WaitForSingleObject(fixture_lock_ready, 2000u) != WAIT_OBJECT_0) return 0;
    return 1;
}

__declspec(dllexport) void __cdecl fixture_ReleaseLock(void)
{
    if (fixture_lock_release != NULL) SetEvent(fixture_lock_release);
    if (fixture_lock_thread_handle != NULL) {
        (void)WaitForSingleObject(fixture_lock_thread_handle, 2000u);
        CloseHandle(fixture_lock_thread_handle);
        fixture_lock_thread_handle = NULL;
    }
    if (fixture_lock_ready != NULL) {
        CloseHandle(fixture_lock_ready);
        fixture_lock_ready = NULL;
    }
    if (fixture_lock_release != NULL) {
        CloseHandle(fixture_lock_release);
        fixture_lock_release = NULL;
    }
}
"""


def provider_response(content: str, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": content},
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def result_content(translation: str = "你好，绝地潜兵。",
                  is_target_language: bool = False) -> str:
    return json.dumps(
        {"is_target_language": is_target_language, "translation": translation},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def google_response(translation: str = "你好，绝地潜兵。", source: str = "en") -> bytes:
    return json.dumps(
        {"data": {"translations": [{"translatedText": translation,
                                       "detectedSourceLanguage": source}]}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def baidu_response(
    translations: tuple[str, ...] = ("你好，", "绝地潜兵。"), source: str = "en",
    error_code: int | str | None = None,
) -> bytes:
    response = {
        "from": source,
        "to": "zh",
        "trans_result": [{"src": "source", "dst": item} for item in translations],
    }
    if error_code is not None:
        response["error_code"] = error_code
    return json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def youdao_response(
    translation: str = "你好，绝地潜兵。", language: str = "en2zh-CHS",
    error_code: str = "0",
) -> bytes:
    return json.dumps(
        {"errorCode": error_code, "l": language, "translation": [translation]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.payloads: list[dict] = []
        self.content_types: list[str] = []
        self.status = 200
        self.response = provider_response(result_content())
        self.response_by_target: dict[str, bytes] = {}
        self.delay = 0.0
        self.headers: list[dict[str, str]] = []
        self.request_finished = threading.Event()
        self.request_started_marker: Path | None = None

    def clear(self) -> None:
        with self.lock:
            self.paths.clear()
            self.payloads.clear()
            self.content_types.clear()
            self.headers.clear()
            self.status = 200
            self.delay = 0.0
            self.response_by_target.clear()
        self.request_finished.clear()
        self.request_started_marker = None


@unittest.skipUnless(WINDOWS, "原生 DLL 仅在 Windows 上运行")
class NativeHttpWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        NATIVE_TEST_ROOT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(
            prefix="hd2ct-http-tests-", dir=str(NATIVE_TEST_ROOT)
        )
        cls.temp_path = Path(cls.temp.name)
        cls.dll = cls.temp_path / "hd2ct_http.dll"
        cls.meta = cls.temp_path / "hd2ct_http.meta.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--output",
                str(cls.dll),
                "--meta",
                str(cls.meta),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.meta_json = json.loads(cls.meta.read_text(encoding="utf-8"))

        cls.state = FakeState()
        state = cls.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                try:
                    content_type = self.headers.get("Content-Type", "")
                    if content_type.lower().startswith("application/x-www-form-urlencoded"):
                        fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True,
                                          strict_parsing=True)
                        payload = {key: values[-1] for key, values in fields.items()}
                    else:
                        payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                    content_type = self.headers.get("Content-Type", "")
                with state.lock:
                    state.paths.append(self.path)
                    state.payloads.append(payload)
                    state.content_types.append(content_type)
                    state.headers.append(dict(self.headers.items()))
                    response_status = state.status
                    response = state.response
                    response_by_target = dict(state.response_by_target)
                    delay = state.delay
                    request_started_marker = state.request_started_marker
                response_key = payload.get("target") or payload.get("to")
                if response_key is None and isinstance(payload.get("messages"), list):
                    prompt = payload["messages"][0].get("content", "")
                    if isinstance(prompt, str):
                        response_key = next(
                            (
                                key
                                for key in response_by_target
                                if prompt.startswith(
                                    "你是《绝地潜兵2》的队友聊天翻译助手。目标语言为"
                                    + key
                                    + "。只处理"
                                )
                            ),
                            None,
                        )
                if response_key in response_by_target:
                    response = response_by_target[response_key]
                if request_started_marker is not None:
                    request_started_marker.write_text("started", encoding="ascii")
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

            def log_message(self, _format: str, *_args: object) -> None:
                return

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=3)
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.state.clear()
        with self.state.lock:
            self.state.status = 200
            self.state.response = provider_response(result_content())
            self.state.delay = 0.0

    def run_child(
        self,
        url: str | None = None,
        actions: list[dict] | None = None,
        timeout: float = 15.0,
        timeout_index: int = 2,
        enabled_value: str = "true",
        target_language_index: int = 1,
        model: str = "fake-model",
    ) -> dict:
        selected_url = url if url is not None else self.url + "/"
        config = {
            "HD2CT_API_URL": selected_url,
            "HD2CT_MODEL": model,
            "HD2CT_API_KEY": "fake-api-key",
            "HD2CT_ENABLED": "0",
            "HD2CT_TIMEOUT_SECONDS": "not-a-number",
        }
        if "baidu" in selected_url.lower() or "youdao" in selected_url.lower():
            config["HD2CT_APP_ID"] = "fake-app-id"
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-menu-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            registry = {
                "user": config,
                "__target_values_path": str(values_path),
                "__target_primary": self._runtime_values_text(
                    target_language_index, enabled_value, timeout_index
                ),
            }
            return self.run_environment_child(
                registry, actions=actions, timeout=timeout,
            )

    def run_menu_child(
        self,
        registry: dict[str, dict[str, str]],
        primary_contents: str | None,
        actions: list[dict] | None = None,
        backup_contents: str | None = None,
        expected_config: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> dict:
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-menu-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            fixture = dict(registry)
            fixture["__target_values_path"] = str(values_path)
            fixture["__target_primary"] = primary_contents
            fixture["__target_backup"] = backup_contents
            return self.run_environment_child(
                fixture,
                expected_config=expected_config,
                actions=actions,
                timeout=timeout,
            )

    @classmethod
    def environment_test_dll(cls) -> Path:
        dll = cls.temp_path / "hd2ct_http_environment_test.dll"
        if dll.is_file():
            return dll
        wrapper = cls.temp_path / "hd2ct_http_environment_test.c"
        registry_shim = cls.temp_path / "hd2ct_http_registry_test_shim.h"
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
            encoding="utf-8",
            newline="\n",
        )
        native_root = ROOT / "native"
        command = [
            cls.meta_json["compiler"],
            "-std=c11",
            "-O2",
            "-shared",
            "-s",
            "-static-libgcc",
            "-finput-charset=UTF-8",
            "-fexec-charset=UTF-8",
            "-D_WIN32_WINNT=0x0601",
            "-DWINVER=0x0601",
            "-DHD2CT_TESTING",
            "-DCJSON_NESTING_LIMIT=32",
            "-DCJSON_HIDE_SYMBOLS",
            "-Wl,--exclude-all-symbols",
            "-I",
            str(native_root),
            "-I",
            str(cls.temp_path),
            "-include",
            str(registry_shim),
            str(wrapper),
            *(str(source) for source in NATIVE_SOURCES[1:]),
            "-o",
            str(dll),
            "-lwinhttp",
            "-ladvapi32",
            "-lbcrypt",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        return dll

    def run_environment_child(
        self,
        registry: dict[str, dict[str, str]],
        expected_config: dict[str, str] | None = None,
        actions: list[dict] | None = None,
        timeout: float = 15.0,
    ) -> dict:
        command = [
            sys.executable,
            "-c",
            ENVIRONMENT_CHILD,
            str(self.environment_test_dll()),
            json.dumps(registry),
        ]
        if expected_config is not None:
            command.append(json.dumps(expected_config))
        if actions is not None:
            command.extend(["--actions", json.dumps(actions, ensure_ascii=False)])
        child_environment = {
            name: os.environ[name]
            for name in os.environ
            if not name.upper().startswith("HD2CT_")
        }
        child_environment.update(
            {
                "HD2CT_API_URL": "http://127.0.0.1:1/process-fallback",
                "HD2CT_MODEL": "fake-process-model",
                "HD2CT_API_KEY": "fake-process-key",
                "HD2CT_APP_ID": "fake-process-app-id",
                "HD2CT_ENABLED": "1",
                "HD2CT_TIMEOUT_SECONDS": "77",
            }
        )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=child_environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stdout)
        return json.loads(lines[0])

    def _exercise_target_language_adapter(self, adapter_name: str) -> None:
        languages = TARGET_LANGUAGE_CATALOGUE["languages"]
        source = "The squad is ready."
        config = {
            "HD2CT_API_URL": self.url + f"/{adapter_name}/target-languages",
            "HD2CT_MODEL": "fake-model" if adapter_name == "ai" else "",
            "HD2CT_API_KEY": "fake-api-key",
        }
        code_field = {
            "google": "google",
            "baidu": "baidu",
            "youdao": "youdao",
        }
        if adapter_name in ("baidu", "youdao"):
            config["HD2CT_APP_ID"] = "fake-app-id"
        response_by_target: dict[str, bytes] = {}
        if adapter_name == "ai":
            for row in languages:
                translation = "AI-" + row["id"]
                response_by_target[row["ai_target"]] = provider_response(
                    result_content(translation, is_target_language=False)
                )
        elif adapter_name == "google":
            for row in languages:
                response_by_target[row["google"]] = google_response(
                    "Google-" + row["id"], "en"
                )
        elif adapter_name == "baidu":
            for row in languages:
                response_by_target[row["baidu"]] = baidu_response(
                    ("Baidu-" + row["id"],)
                )
        elif adapter_name == "youdao":
            for row in languages:
                response_by_target[row["youdao"]] = youdao_response(
                    "Youdao-" + row["id"], language="en2en"
                )
        else:
            raise AssertionError(f"unknown adapter fixture: {adapter_name}")

        with tempfile.TemporaryDirectory(
            prefix="hd2ct-target-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            registry = {
                "user": config,
                "__target_values_path": str(values_path),
                "__target_primary": self._target_values_line(1),
            }
            actions: list[dict] = []
            for index, row in enumerate(languages, start=1):
                token = f"{adapter_name}-language-{index:02d}"
                actions.extend([
                    {"op": "target_file", "kind": "primary",
                     "contents": self._target_values_line(index)},
                    {"op": "submit", "token": token,
                     "body_hex": source.encode("utf-8").hex()},
                    {"op": "wait", "token": token},
                ])
            actions.extend([
                {"op": "target_file", "kind": "primary",
                 "contents": self._target_values_line(1)},
                {"op": "submit", "token": f"{adapter_name}-language-cache-return",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": f"{adapter_name}-language-cache-return"},
            ])
            self.state.clear()
            self.state.response_by_target = response_by_target
            result = self.run_environment_child(registry, actions=actions)

        self.assertEqual(len(languages), 10)
        self.assertEqual(TARGET_LANGUAGE_CATALOGUE["default_index"], 1)
        self.assertEqual(len(self.state.paths), len(languages))
        self.assertEqual(result["cache_count"], len(languages))
        for position, row in enumerate(languages):
            payload = self.state.payloads[position]
            if adapter_name == "ai":
                self.assertEqual(payload["response_format"], AI_RESPONSE_FORMAT)
                prompt = payload["messages"][0]["content"]
                self.assertIn(row["ai_target"], prompt)
                self.assertIn("目标语言为" + row["ai_target"], prompt)
                self.assertIn("is_target_language", prompt)
                self.assertIn("若原文已经是目标语言，设置is_target_language=true并原样返回", prompt)
                self.assertIn("只输出JSON对象", prompt)
                self.assertIn("且只能包含is_target_language（布尔值）和translation（字符串）", prompt)
                self.assertNotIn("Translate the input", prompt)
                if row["id"] != "zh_cn":
                    self.assertNotIn("Charger=牛", prompt)
                    self.assertNotIn("Stalker=隐身虫", prompt)
                    self.assertNotIn("lol=哈哈", prompt)
                    self.assertNotIn("以下为游戏内敌人的口语表达", prompt)
                    self.assertTrue(prompt.endswith("translation（字符串）。"))
                if row["id"] == "zh_tw":
                    self.assertIn("目标语言为繁體中文", prompt)
                if row["id"] == "zh_cn":
                    self.assertIn("以下为游戏内敌人的口语表达", prompt)
                    self.assertIn("Charger=牛", prompt)
                    self.assertIn("lol=哈哈", prompt)
                expected_translation = "AI-" + row["id"]
            else:
                self.assertNotIn("response_format", payload)
                self.assertEqual(payload["to"] if adapter_name != "google"
                                 else payload["target"], row[code_field[adapter_name]])
                expected_translation = adapter_name.title() + "-" + row["id"]
            action = result["actions"][position * 3 + 2]
            self.assertEqual(action["result"], "OK\n" + expected_translation)

        self.assertEqual(
            result["actions"][len(languages) * 3 + 2]["result"],
            "OK\n" + ("AI-" if adapter_name == "ai" else adapter_name.title() + "-") + languages[0]["id"],
        )

    @staticmethod
    def _target_values_line(index: int) -> str:
        return f"{TARGET_LANGUAGE_CATALOGUE['option_id']}\t{index}\n"

    @staticmethod
    def _runtime_values_text(
        target_index: int = 1, enabled_value: str = "true", timeout_index: int = 2,
    ) -> str:
        options = TARGET_LANGUAGE_CATALOGUE["menu_options"]
        return "".join((
            f"{TARGET_LANGUAGE_CATALOGUE['option_id']}\t{target_index}\n",
            f"{options['enabled']['option_id']}\t{enabled_value}\n",
            f"{options['timeout']['option_id']}\t{timeout_index}\n",
        ))

    def _exercise_target_values_fallbacks(self) -> None:
        cases = (
            ("backup-after-missing-primary", None,
             self._target_values_line(3), "en"),
            ("backup-after-corrupt-primary", "not a values file\n",
             self._target_values_line(2), "zh-TW"),
            ("valid-primary-missing-option", "othermod.some_option\t2\n",
             self._target_values_line(3), "zh-CN"),
            ("valid-primary-missing-option-no-backup",
             "othermod.some_option\t2\n", None, "zh-CN"),
            ("invalid-index-does-not-use-backup", self._target_values_line(99),
             self._target_values_line(2), "zh-CN"),
            ("corrupt-primary-without-backup", "broken\n", None, "zh-CN"),
        )
        for removed_index in range(11, 15):
            cases += ((
                f"removed-language-index-{removed_index}",
                self._target_values_line(removed_index),
                self._target_values_line(2),
                "zh-CN",
            ),)
        for other_value in ("true", "false", "0", "-1", "0.5", "invalid"):
            cases += ((
                f"othermod-value-{other_value}",
                f"othermod.some_option\t{other_value}\n",
                self._target_values_line(3),
                "zh-CN",
            ),)
        config = {
            "HD2CT_API_URL": self.url + "/Google/target-values-fallback",
            "HD2CT_MODEL": "",
            "HD2CT_API_KEY": "fake-api-key",
        }
        for case_name, primary, backup, expected_target in cases:
            with self.subTest(target_file_case=case_name):
                with tempfile.TemporaryDirectory(
                    prefix="hd2ct-target-values-", dir=str(NATIVE_TEST_ROOT)
                ) as temporary_directory:
                    values_path = Path(temporary_directory) / "ModOptionsMenu.values"
                    registry = {
                        "user": config,
                        "__target_values_path": str(values_path),
                        "__target_primary": primary,
                        "__target_backup": backup,
                    }
                    self.state.clear()
                    self.state.response = google_response("Translated", "en")
                    result = self.run_environment_child(
                        registry,
                        actions=[
                            {"op": "submit", "token": "target-file-fallback",
                             "body_hex": "736f75726365"},
                            {"op": "wait", "token": "target-file-fallback"},
                        ],
                    )
                self.assertEqual(result["actions"][1]["result"], "OK\nTranslated")
                self.assertEqual(self.state.payloads[0]["target"], expected_target)

    def _exercise_ai_target_language_flags(self) -> None:
        config = {
            "HD2CT_API_URL": self.url + "/ai/target-language-flags",
            "HD2CT_MODEL": "fake-model",
            "HD2CT_API_KEY": "fake-api-key",
        }
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-target-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            registry = {
                "user": config,
                "__target_values_path": str(values_path),
                "__target_primary": self._target_values_line(3),
            }

            self.state.clear()
            self.state.response = provider_response(
                result_content("English translation", is_target_language=False)
            )
            source_chinese = "需要支援"
            translated = self.run_environment_child(
                registry,
                actions=[
                    {"op": "submit", "token": "ai-english-from-chinese",
                     "body_hex": source_chinese.encode("utf-8").hex()},
                    {"op": "wait", "token": "ai-english-from-chinese"},
                ],
            )
            self.assertEqual(translated["actions"][1]["result"], "OK\nEnglish translation")
            prompt = self.state.payloads[0]["messages"][0]["content"]
            self.assertIn("英语", prompt)
            self.assertNotIn("Charger=牛", prompt)

            self.state.clear()
            source_english = "Already English"
            self.state.response = provider_response(
                result_content(source_english, is_target_language=True)
            )
            matched = self.run_environment_child(
                registry,
                actions=[
                    {"op": "submit", "token": "ai-target-match",
                     "body_hex": source_english.encode("utf-8").hex()},
                    {"op": "wait", "token": "ai-target-match"},
                    {"op": "submit", "token": "ai-target-match-cache",
                     "body_hex": source_english.encode("utf-8").hex()},
                    {"op": "wait", "token": "ai-target-match-cache"},
                ],
            )
            self.assertEqual(matched["actions"][1]["result"], "SKIP\n")
            self.assertEqual(matched["actions"][3]["result"], "SKIP\n")
            self.assertEqual(len(self.state.paths), 1)
            self.assertEqual(matched["cache_count"], 1)

    def _exercise_catalogue_validation(self) -> None:
        self.assertEqual(
            target_languages.catalogue_sha256(TARGET_LANGUAGE_CATALOGUE),
            target_languages.catalogue_sha256(target_languages.load_catalogue()),
        )
        self.assertEqual(
            [row["id"] for row in TARGET_LANGUAGE_CATALOGUE["languages"]],
            ["zh_cn", "zh_tw", "en", "ja", "ko", "fr", "de", "es", "pt", "it"],
        )
        menu_options = TARGET_LANGUAGE_CATALOGUE["menu_options"]
        self.assertEqual(
            [choice["seconds"] for choice in menu_options["timeout"]["choices"]],
            [10, 20, 30],
        )
        self.assertEqual(menu_options["timeout"]["default_index"], 2)
        self.assertEqual(
            len({TARGET_LANGUAGE_CATALOGUE["option_id"],
                 menu_options["enabled"]["option_id"],
                 menu_options["timeout"]["option_id"]}),
            3,
        )
        invalid_catalogues = []
        duplicate_id = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        duplicate_id["languages"][1]["id"] = duplicate_id["languages"][0]["id"]
        invalid_catalogues.append(("duplicate-id", duplicate_id))
        invalid_default = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        invalid_default["default_index"] = 0
        invalid_catalogues.append(("default-index", invalid_default))
        long_label = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        long_label["languages"][0]["label"] = "x" * 49
        invalid_catalogues.append(("long-label", long_label))
        bad_code = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        bad_code["languages"][0]["google"] = "zh CN"
        invalid_catalogues.append(("bad-provider-code", bad_code))
        bad_timeout_order = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        bad_timeout_order["menu_options"]["timeout"]["choices"][1]["seconds"] = 60
        invalid_catalogues.append(("bad-timeout-order", bad_timeout_order))
        bad_timeout_default = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        bad_timeout_default["menu_options"]["timeout"]["default_index"] = 1
        invalid_catalogues.append(("bad-timeout-default", bad_timeout_default))
        duplicate_menu_id = json.loads(json.dumps(TARGET_LANGUAGE_CATALOGUE))
        duplicate_menu_id["menu_options"]["timeout"]["option_id"] = \
            duplicate_menu_id["menu_options"]["enabled"]["option_id"]
        invalid_catalogues.append(("duplicate-menu-option-id", duplicate_menu_id))
        for name, catalogue in invalid_catalogues:
            with self.subTest(catalogue=name):
                with self.assertRaises(ValueError):
                    target_languages.validate_catalogue(catalogue)

    def assert_environment_initialization_uses_registry(self) -> None:
        user_config = {
            "HD2CT_API_URL": "http://127.0.0.1:1/user",
            "HD2CT_MODEL": "fake-user-model",
            "HD2CT_API_KEY": "fake-user-key",
        }
        machine_config = {
            "HD2CT_API_URL": "http://127.0.0.1:1/machine",
            "HD2CT_MODEL": "fake-machine-model",
            "HD2CT_API_KEY": "fake-machine-key",
        }

        self.state.clear()
        unknown_actions = [
            {"op": "submit", "token": "unknown-service", "body_hex": "73616665"},
            {"op": "wait", "token": "unknown-service"},
            {"op": "submit", "token": "unknown-service-again", "body_hex": "73616665"},
            {"op": "wait", "token": "unknown-service-again"},
        ]
        result = self.run_environment_child({}, actions=unknown_actions)
        self.assertEqual(result["status"], 5)
        self.assertEqual(result["enabled"], 0)
        self.assertEqual([item.get("accepted") for item in result["actions"]],
                         [1, None, 1, None])
        self.assertEqual(result["actions"][1]["result"], "ERR\nUNSUPPORTED_SERVICE")
        self.assertEqual(result["actions"][3]["result"], "ERR\nUNSUPPORTED_SERVICE")
        self.assertEqual(result["cache_count"], 0)
        self.assertEqual(result["rate_count"], 0)
        self.assertEqual(self.state.paths, [])

        for case_name, user, expected_status in (
            ("valid", {
                "HD2CT_API_URL": self.url + "/Google/disabled-valid",
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "fake-api-key",
            }, 0),
            ("missing-config", {
                "HD2CT_API_URL": self.url + "/Google/disabled-missing",
                "HD2CT_MODEL": "",
            }, 1),
            ("invalid-config", {
                "HD2CT_API_URL": "ftp://example.com/Google/disabled-invalid",
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "fake-api-key",
            }, 2),
            ("unknown-service", {
                "HD2CT_API_URL": "http://127.0.0.1:1/unknown-service",
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "fake-api-key",
            }, 5),
        ):
            with self.subTest(disabled_config=case_name):
                self.state.clear()
                disabled = self.run_menu_child(
                    {"user": user},
                    self._runtime_values_text(enabled_value="false"),
                    actions=[
                        {"op": "submit", "token": "disabled-" + case_name,
                         "body_hex": "73616665"},
                        {"op": "wait", "token": "disabled-" + case_name},
                    ],
                )
                self.assertEqual(disabled["status"], expected_status)
                self.assertEqual(disabled["enabled"], int(expected_status == 0))
                self.assertEqual(disabled["menu_enabled"], 0)
                self.assertEqual(disabled["actions"][1]["result"], "SKIP\n")
                self.assertEqual(disabled["cache_count"], 0)
                self.assertEqual(disabled["rate_count"], 0)
                self.assertEqual(self.state.paths, [])

        for timeout_index, expected_timeout in ((1, 10), (2, 20), (3, 30),
                                                (4, 20), (5, 20)):
            with self.subTest(menu_timeout_index=timeout_index):
                settings = self.run_menu_child(
                    {},
                    self._runtime_values_text(timeout_index=timeout_index),
                    actions=[{"op": "read_settings"}],
                )
                self.assertEqual(settings["actions"][0], {
                    "target_language": 1,
                    "timeout_seconds": expected_timeout,
                    "menu_enabled": 1,
                })

        settings = self.run_menu_child(
            {}, None, actions=[{"op": "read_settings"}],
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 1,
            "timeout_seconds": 20,
            "menu_enabled": 1,
        })
        settings = self.run_menu_child(
            {}, self._runtime_values_text(
                target_index=99, enabled_value="True", timeout_index=4,
            ),
            actions=[{"op": "read_settings"}],
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 1,
            "timeout_seconds": 20,
            "menu_enabled": 1,
        })

        primary_with_other_mod = "othermod.some_option\t2\n"
        backup_settings = self._runtime_values_text(
            target_index=3, enabled_value="false", timeout_index=3,
        )
        settings = self.run_menu_child(
            {}, primary_with_other_mod,
            actions=[{"op": "read_settings"}],
            backup_contents=backup_settings,
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 1,
            "timeout_seconds": 20,
            "menu_enabled": 1,
        })
        settings = self.run_menu_child(
            {}, "corrupt\n",
            actions=[{"op": "read_settings"}],
            backup_contents=backup_settings,
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 3,
            "timeout_seconds": 30,
            "menu_enabled": 0,
        })

        self.state.clear()
        self.state.response = google_response("menu translation", "en")
        self.state.delay = 0.4
        request_marker = self.temp_path / "menu-toggle-request-started"
        request_marker.unlink(missing_ok=True)
        self.state.request_started_marker = request_marker
        menu_toggle_config = {
            "HD2CT_API_URL": self.url + "/Google/menu-toggle",
            "HD2CT_MODEL": "",
            "HD2CT_API_KEY": "fake-api-key",
            "HD2CT_ENABLED": "0",
            "HD2CT_TIMEOUT_SECONDS": "invalid",
        }
        source_hex = "6d656e752d736e617073686f74"
        toggle = self.run_menu_child(
            {"user": menu_toggle_config},
            self._runtime_values_text(
                target_index=1, enabled_value="false", timeout_index=2,
            ),
            actions=[
                {"op": "submit", "token": "toggle-off-first", "body_hex": source_hex},
                {"op": "wait", "token": "toggle-off-first"},
                {"op": "target_file", "kind": "primary", "contents":
                 self._runtime_values_text(target_index=1, enabled_value="true", timeout_index=1)},
                {"op": "submit", "token": "toggle-on-snapshot", "body_hex": source_hex},
                {"op": "wait_file", "path": str(request_marker), "timeout": 5.0},
                {"op": "target_file", "kind": "primary", "contents":
                 self._runtime_values_text(target_index=3, enabled_value="false", timeout_index=3)},
                {"op": "wait", "token": "toggle-on-snapshot", "timeout": 8.0},
                {"op": "target_file", "kind": "primary", "contents":
                 self._runtime_values_text(target_index=1, enabled_value="false", timeout_index=2)},
                {"op": "submit", "token": "toggle-off-cached", "body_hex": source_hex},
                {"op": "wait", "token": "toggle-off-cached"},
            ],
            timeout=15.0,
        )
        self.assertEqual(toggle["status"], 0)
        self.assertEqual(toggle["enabled"], 1)
        self.assertEqual(toggle["menu_enabled"], 0)
        self.assertEqual(toggle["timeout"], 20)
        self.assertEqual(toggle["actions"][1]["result"], "SKIP\n")
        self.assertTrue(toggle["actions"][4]["found"])
        self.assertEqual(toggle["actions"][6]["result"], "OK\nmenu translation")
        self.assertEqual(toggle["actions"][9]["result"], "SKIP\n")
        self.assertEqual(toggle["cache_count"], 1)
        self.assertEqual(toggle["rate_count"], 1)
        self.assertEqual(self.state.paths, ["/Google/menu-toggle"])
        self.assertEqual(self.state.payloads[0]["target"], "zh-CN")

        for missing_name in user_config:
            user = dict(user_config)
            machine = dict(machine_config)
            del user[missing_name]
            del machine[missing_name]
            with self.subTest(missing_registry_value=missing_name):
                result = self.run_environment_child({"user": user, "machine": machine})
                if missing_name == "HD2CT_MODEL":
                    self.assertEqual(result["status"], 5)
                    self.assertEqual(result["enabled"], 0)
                    self.assertEqual(result["actions"][1]["result"], "ERR\nUNSUPPORTED_SERVICE")
                else:
                    self.assertEqual(result["status"], 1)
                    self.assertEqual(result["enabled"], 0)
                    self.assertEqual(result["submit"], 1)
                    self.assertEqual(result["actions"][1]["result"], "ERR\nMISSING_CONFIG")

        result = self.run_environment_child(
            {"user": user_config, "machine": machine_config}, user_config
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["timeout"], 20)
        self.assertEqual(result["selected_config_matches"], 1)

        stale_app_id = dict(user_config)
        stale_app_id["HD2CT_APP_ID"] = "invalid\nlegacy-value"
        result = self.run_environment_child(
            {"user": stale_app_id},
            {**user_config, "HD2CT_APP_ID": ""},
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["selected_config_matches"], 1)

        empty_signed_app_id = {
            "HD2CT_API_URL": self.url + "/Baidu/custom",
            "HD2CT_MODEL": "",
            "HD2CT_API_KEY": "bd-secret",
            "HD2CT_APP_ID": "",
        }
        result = self.run_environment_child(
            {"user": empty_signed_app_id,
             "machine": {"HD2CT_APP_ID": "machine-app-id"}},
        )
        self.assertEqual(result["status"], 1)
        self.assertEqual(result["enabled"], 0)

        result = self.run_environment_child(
            {"machine": machine_config}, machine_config
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["selected_config_matches"], 1)

        empty_user = dict(user_config)
        empty_user["HD2CT_MODEL"] = ""
        result = self.run_environment_child(
            {"user": empty_user, "machine": machine_config},
            expected_config={
                "HD2CT_API_URL": empty_user["HD2CT_API_URL"],
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "",
                "HD2CT_APP_ID": "",
            },
        )
        self.assertEqual(result["status"], 5)
        self.assertEqual(result["enabled"], 0)
        self.assertEqual(result["selected_config_matches"], 1)
        self.assertEqual(result["actions"][1]["result"], "ERR\nUNSUPPORTED_SERVICE")

        invalid_user = dict(user_config)
        invalid_user["HD2CT_API_URL"] = "ftp://127.0.0.1:1/user"
        result = self.run_environment_child(
            {"user": invalid_user, "machine": machine_config}
        )
        self.assertEqual(result["status"], 2)
        self.assertEqual(result["enabled"], 0)
        self.assertEqual(result["submit"], 1)
        self.assertEqual(result["actions"][1]["result"], "ERR\nINVALID_CONFIG")

        self.assert_signed_provider_adapters()

    def assert_signed_provider_adapters(self) -> None:
        source = "撤离 & 补给/alpha\n再集合"
        baidu_config = {
            "HD2CT_API_URL": self.url + "/Baidu/custom/path",
            "HD2CT_MODEL": " \t ",
            "HD2CT_API_KEY": "bd-secret",
            "HD2CT_APP_ID": "bd+app&1",
        }
        self.state.clear()
        self.state.response = baidu_response()
        result = self.run_environment_child(
            {"user": baidu_config}, baidu_config,
            [
                {"op": "submit", "token": "baidu-success",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "baidu-success"},
                {"op": "submit", "token": "baidu-cache-hit",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "baidu-cache-hit"},
            ],
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["selected_config_matches"], 1)
        self.assertEqual(result["actions"][1]["result"], "OK\n你好，\n绝地潜兵。")
        self.assertEqual(result["actions"][3]["result"], "OK\n你好，\n绝地潜兵。")
        self.assertEqual(self.state.paths, ["/Baidu/custom/path"])
        self.assertTrue(self.state.content_types[0].startswith("application/x-www-form-urlencoded"))
        form = self.state.payloads[0]
        self.assertEqual(form["q"], source)
        self.assertEqual(form["from"], "auto")
        self.assertEqual(form["to"], "zh")
        self.assertEqual(form["appid"], baidu_config["HD2CT_APP_ID"])
        expected_md5 = hashlib.md5(
            (baidu_config["HD2CT_APP_ID"] + source + form["salt"] +
             baidu_config["HD2CT_API_KEY"]).encode("utf-8")
        ).hexdigest()
        self.assertEqual(form["sign"], expected_md5)

        self.state.clear()
        self.state.response = baidu_response(("不覆盖中文",), source="zh")
        chinese = "我们已在 A1 集合。"
        preserved = self.run_environment_child(
            {"user": baidu_config}, baidu_config,
            [
                {"op": "submit", "token": "baidu-chinese",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "baidu-chinese"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "OK\n不覆盖中文")

        self.state.clear()
        same_chinese = "撤离点集合。"
        self.state.response = baidu_response((same_chinese,), source="zh")
        preserved = self.run_environment_child(
            {"user": baidu_config}, baidu_config,
            [
                {"op": "submit", "token": "baidu-chinese-same",
                 "body_hex": same_chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "baidu-chinese-same"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "SKIP\n")

        self.state.clear()
        self.state.response = baidu_response(("ignored",), error_code=54003)
        provider_error = self.run_environment_child(
            {"user": baidu_config}, baidu_config,
            [
                {"op": "submit", "token": "baidu-error", "body_hex": "6869"},
                {"op": "wait", "token": "baidu-error"},
            ],
        )
        self.assertEqual(provider_error["actions"][1]["result"], "ERR\nRATE_LIMITED")
        self.assertNotIn("bd-secret", json.dumps(provider_error))

        self.state.clear()
        self.state.response = b'{"from":"en","trans_result":[{"dst":""}]}'
        malformed = self.run_environment_child(
            {"user": baidu_config}, baidu_config,
            [
                {"op": "submit", "token": "baidu-malformed", "body_hex": "6869"},
                {"op": "wait", "token": "baidu-malformed"},
            ],
        )
        self.assertEqual(malformed["actions"][1]["result"], "ERR\nBAD_RESPONSE")

        yd_source = "🙂" * 12 + " A&B/撤离 " + "补给🙂" * 12
        youdao_config = {
            "HD2CT_API_URL": self.url + "/Youdao/custom/path",
            "HD2CT_MODEL": "　 ",
            "HD2CT_API_KEY": "yd-secret",
            "HD2CT_APP_ID": "yd+app&1",
        }
        self.state.clear()
        self.state.response = youdao_response()
        result = self.run_environment_child(
            {"user": youdao_config}, youdao_config,
            [
                {"op": "submit", "token": "youdao-success",
                 "body_hex": yd_source.encode("utf-8").hex()},
                {"op": "wait", "token": "youdao-success"},
            ],
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["actions"][1]["result"], "OK\n你好，绝地潜兵。")
        self.assertEqual(self.state.paths, ["/Youdao/custom/path"])
        form = self.state.payloads[0]
        self.assertEqual(form["q"], yd_source)
        self.assertEqual(form["from"], "auto")
        self.assertEqual(form["to"], "zh-CHS")
        self.assertEqual(form["appKey"], youdao_config["HD2CT_APP_ID"])
        self.assertEqual(form["strict"], "true")
        self.assertEqual(form["signType"], "v3")
        expected_input = yd_source[:10] + str(len(yd_source)) + yd_source[-10:]
        self.assertEqual(len(yd_source), len(list(yd_source)))
        self.assertEqual(form["curtime"].isdigit(), True)
        self.assertLessEqual(abs(int(form["curtime"]) - int(time.time())), 5)
        expected_sha256 = hashlib.sha256(
            (youdao_config["HD2CT_APP_ID"] + expected_input + form["salt"] +
             form["curtime"] + youdao_config["HD2CT_API_KEY"]).encode("utf-8")
        ).hexdigest()
        self.assertEqual(form["sign"], expected_sha256)

        self.state.clear()
        self.state.response = youdao_response("不覆盖中文", language="zh-CHS2zh-CHS")
        chinese = "中文原文保留。"
        preserved = self.run_environment_child(
            {"user": youdao_config}, youdao_config,
            [
                {"op": "submit", "token": "youdao-chinese",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "youdao-chinese"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "OK\n不覆盖中文")

        self.state.clear()
        same_chinese = "撤离点集合。"
        self.state.response = youdao_response(same_chinese, language="zh-CHS2zh-CHS")
        preserved = self.run_environment_child(
            {"user": youdao_config}, youdao_config,
            [
                {"op": "submit", "token": "youdao-chinese-same",
                 "body_hex": same_chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "youdao-chinese-same"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "SKIP\n")

        self.state.clear()
        self.state.response = youdao_response("ignored", error_code="401")
        provider_error = self.run_environment_child(
            {"user": youdao_config}, youdao_config,
            [
                {"op": "submit", "token": "youdao-error", "body_hex": "6869"},
                {"op": "wait", "token": "youdao-error"},
            ],
        )
        self.assertEqual(provider_error["actions"][1]["result"], "ERR\nQUOTA_EXCEEDED")
        self.assertNotIn("yd-secret", json.dumps(provider_error))

        self.state.clear()
        self.state.response = b'{"errorCode":"0","l":"en2zh-CHS","translation":[]}'
        malformed = self.run_environment_child(
            {"user": youdao_config}, youdao_config,
            [
                {"op": "submit", "token": "youdao-malformed", "body_hex": "6869"},
                {"op": "wait", "token": "youdao-malformed"},
            ],
        )
        self.assertEqual(malformed["actions"][1]["result"], "ERR\nBAD_RESPONSE")

    def test_endpoint_completion_covers_root_v1_and_custom_paths(self) -> None:
        cases = (
            ("", "/chat/completions"),
            ("/", "/chat/completions"),
            ("/v1", "/v1/chat/completions"),
            ("/v1/", "/v1/chat/completions"),
            ("/custom/openai/compat", "/custom/openai/compat"),
            ("/chat/completions/", "/chat/completions/"),
            ("/chat/compltetion", "/chat/compltetion"),
        )
        for index, (suffix, expected) in enumerate(cases):
            with self.subTest(suffix=suffix):
                self.state.clear()
                token = f"endpoint-{index}"
                result = self.run_child(
                    self.url + suffix,
                    [
                        {"op": "submit", "token": token, "body_hex": "48656c6c6f"},
                        {"op": "wait", "token": token},
                    ],
                )
                self.assertEqual(result["actions"][0]["accepted"], 1)
                self.assertEqual(result["actions"][1]["result"], "OK\n你好，绝地潜兵。")
                self.assertEqual(self.state.paths, [expected])
                self.assertEqual(self.state.payloads[0]["reasoning_effort"], "none")

        root_cases = (
            (1, "/chat/completions"),
            (2, "/language/translate/v2"),
            (3, "/api/trans/vip/translate"),
            (4, "/api"),
        )
        normalize_actions = []
        for adapter_id, path in root_cases:
            normalize_actions.append({
                "op": "normalize", "adapter_id": adapter_id, "url": self.url,
            })
            normalize_actions.append({
                "op": "normalize", "adapter_id": adapter_id,
                "url": self.url + "/custom/Google",
            })
        normalized = self.run_environment_child({}, actions=normalize_actions)
        for i, (_, path) in enumerate(root_cases):
            root_result = normalized["actions"][i * 2]
            custom_result = normalized["actions"][i * 2 + 1]
            self.assertEqual(root_result["accepted"], 1)
            self.assertEqual(root_result["url"], self.url + path)
            self.assertEqual(custom_result["accepted"], 1)
            self.assertEqual(custom_result["url"], self.url + "/custom/Google")

        google_path = "/Google/Baidu/Youdao/custom"
        self.state.clear()
        self.state.response = google_response("Hello &amp; &#x1F642;", "en")
        google_source = "Hello"
        google = self.run_child(
            self.url + google_path,
            [
                {"op": "submit", "token": "google-route",
                 "body_hex": google_source.encode("utf-8").hex()},
                {"op": "wait", "token": "google-route"},
            ],
            model=" \t ",
        )
        self.assertEqual(google["actions"][1]["result"], "OK\nHello & 🙂")
        self.assertEqual(self.state.paths, [google_path])
        self.assertEqual(self.state.payloads[0], {
            "q": google_source, "target": "zh-CN", "format": "text",
        })
        self.assertEqual(self.state.content_types[0], "application/json")
        self.assertEqual(self.state.headers[0].get("x-goog-api-key"), "fake-api-key")
        self.assertNotIn("Authorization", self.state.headers[0])

        self.state.clear()
        self.state.response = google_response("ignored result", "zh-Hans")
        chinese = "中文原文。"
        preserved = self.run_child(
            self.url + "/google/custom",
            [
                {"op": "submit", "token": "google-chinese",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "google-chinese"},
            ],
            model="",
        )
        self.assertEqual(preserved["actions"][1]["result"], "OK\nignored result")

        self.state.clear()
        self.state.response = google_response(chinese, "zh-Hans")
        same_chinese = self.run_child(
            self.url + "/google/custom",
            [
                {"op": "submit", "token": "google-chinese-same",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "google-chinese-same"},
                {"op": "submit", "token": "google-chinese-same-cache",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "google-chinese-same-cache"},
            ],
            model="",
        )
        self.assertEqual(same_chinese["actions"][1]["result"], "SKIP\n")
        self.assertEqual(same_chinese["actions"][3]["result"], "SKIP\n")
        self.assertEqual(self.state.paths, ["/google/custom"])
        self.assertEqual(len(self.state.payloads), 1)
        self.assertEqual(same_chinese["cache_count"], 1)

        stale_app_id = {
            "HD2CT_API_URL": self.url + "/Google/custom",
            "HD2CT_MODEL": "",
            "HD2CT_API_KEY": "google-secret",
            "HD2CT_APP_ID": "invalid\nlegacy-value",
        }
        self.state.clear()
        self.state.response = google_response("你好", "en")
        result = self.run_environment_child(
            {"user": stale_app_id},
            {**stale_app_id, "HD2CT_APP_ID": ""},
            [
                {"op": "submit", "token": "google-no-app-id", "body_hex": "6869"},
                {"op": "wait", "token": "google-no-app-id"},
            ],
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["selected_config_matches"], 1)
        self.assertEqual(result["actions"][1]["result"], "OK\n你好")

        self.state.clear()
        self.state.response = b'{"data":{"translations":[]}}'
        malformed = self.run_child(
            self.url + "/Google/custom",
            [
                {"op": "submit", "token": "google-malformed", "body_hex": "6869"},
                {"op": "wait", "token": "google-malformed"},
            ],
            model="",
        )
        self.assertEqual(malformed["actions"][1]["result"], "ERR\nBAD_RESPONSE")

        self.state.clear()
        self.state.status = 429
        self.state.response = b"private Google quota body"
        denied = self.run_child(
            self.url + "/Google/custom",
            [
                {"op": "submit", "token": "google-http-error", "body_hex": "6869"},
                {"op": "wait", "token": "google-http-error"},
            ],
            model="",
        )
        self.assertEqual(denied["actions"][1]["result"], "ERR\nHTTP_429")
        self.assertNotIn("private Google quota body", json.dumps(denied))

        self.state.clear()
        self.state.response = provider_response(result_content())
        ai_override = self.run_child(
            self.url + google_path,
            [
                {"op": "submit", "token": "ai-overrides-provider", "body_hex": "6869"},
                {"op": "wait", "token": "ai-overrides-provider"},
            ],
            model="fake-model",
        )
        self.assertEqual(ai_override["actions"][1]["result"], "OK\n你好，绝地潜兵。")
        self.assertIn("messages", self.state.payloads[0])
        self.assertEqual(self.state.paths, [google_path])

        missing_signed_app_id = self.run_environment_child(
            {"user": {
                "HD2CT_API_URL": self.url + "/Baidu/missing-app-id",
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "fake-api-key",
            }}
        )
        self.assertEqual(missing_signed_app_id["status"], 1)
        self.assertEqual(missing_signed_app_id["enabled"], 0)
        self.assertEqual(missing_signed_app_id["actions"][1]["result"], "ERR\nMISSING_CONFIG")
        self._exercise_catalogue_validation()
        self._exercise_target_values_fallbacks()
        self._exercise_ai_target_language_flags()
        for adapter_name in ("ai", "google", "baidu", "youdao"):
            self._exercise_target_language_adapter(adapter_name)
        self.assert_environment_initialization_uses_registry()

    def test_english_translation_preserves_chinese_and_model_translation(self) -> None:
        self.state.response = provider_response(result_content("前往撤离点", is_target_language=False))
        english = "Move to extraction"
        translated = self.run_child(
            actions=[
                {"op": "submit", "token": "english", "body_hex": english.encode("utf-8").hex()},
                {"op": "wait", "token": "english"},
            ],
        )
        self.assertEqual(translated["actions"][1]["result"], "OK\n前往撤离点")
        payload = self.state.payloads[0]
        self.assertEqual([item["role"] for item in payload["messages"]], ["system", "user"])
        self.assertEqual(payload["messages"][1]["content"], english)
        self.assertEqual(payload["response_format"], AI_RESPONSE_FORMAT)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["reasoning_effort"], "none")
        example = json.loads(
            (ROOT / "docs" / "ai-request-example.json").read_text(encoding="utf-8")
        )
        expected_example = dict(payload)
        expected_example["model"] = "YOUR_MODEL"
        expected_example["messages"] = [
            payload["messages"][0],
            {
                "role": "user",
                "content": "We need reinforcements. A Charger is approaching A1.",
            },
        ]
        self.assertEqual(example, expected_example)
        prompt = payload["messages"][0]["content"]
        for rule in (
            "目标语言为简体中文",
            "以下为游戏内敌人的口语表达",
            "若原文已经是目标语言，设置is_target_language=true并原样返回",
            "is_target_language",
            "translation",
            "Charger=牛",
        ):
            with self.subTest(prompt_rule=rule):
                self.assertIn(rule, prompt)

        self.state.clear()
        self.state.response = provider_response(
            result_content("不应覆盖中文原文", is_target_language=True))
        chinese = "我们需要在 A1 补给。"
        preserved = self.run_child(
            actions=[
                {"op": "submit", "token": "chinese", "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "chinese"},
                {"op": "submit", "token": "chinese-cache", "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "chinese-cache"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "SKIP\n")
        self.assertEqual(preserved["actions"][3]["result"], "SKIP\n")
        self.assertEqual(len(self.state.paths), 1)
        self.assertEqual(self.state.payloads[0]["messages"][1]["content"], chinese)

        self.state.clear()
        source_equal = "Hold this position."
        self.state.response = provider_response(
            result_content(source_equal, is_target_language=False))
        same_translation = self.run_child(
            actions=[
                {"op": "submit", "token": "same-translation",
                 "body_hex": source_equal.encode("utf-8").hex()},
                {"op": "wait", "token": "same-translation"},
                {"op": "submit", "token": "same-translation-cache",
                 "body_hex": source_equal.encode("utf-8").hex()},
                {"op": "wait", "token": "same-translation-cache"},
            ],
        )
        self.assertEqual(same_translation["actions"][1]["result"], "SKIP\n")
        self.assertEqual(same_translation["actions"][3]["result"], "SKIP\n")
        self.assertEqual(len(self.state.paths), 1)

        self.state.clear()
        self.state.response = provider_response(result_content("打得不错", is_target_language=False))
        raw_gg = "\tGgS \r\n"
        translated_gg = self.run_child(
            actions=[
                {"op": "submit", "token": "gg", "body_hex": raw_gg.encode("utf-8").hex()},
                {"op": "wait", "token": "gg"},
            ],
        )
        self.assertEqual(translated_gg["actions"][1]["result"], "OK\n打得不错")
        self.assertEqual(self.state.payloads[0]["messages"][1]["content"], raw_gg)

    def test_http401_is_redacted_and_invalid_model_result_is_classified(self) -> None:
        self.assertEqual(self.meta_json["abi_version"], 2)
        self.assertEqual(
            self.meta_json["target_languages_sha256"],
            target_languages.catalogue_sha256(TARGET_LANGUAGE_CATALOGUE),
        )
        objdump = build_native_http.resolve_objdump(Path(self.meta_json["compiler"]), None)
        self.assertEqual(
            build_native_http.exported_names(self.dll, objdump),
            {"HD2CT_Submit", "HD2CT_Poll", "HD2CT_Cancel"},
        )
        self.state.status = 401
        self.state.response = b"secret-provider-error-body"
        unauthorized = self.run_child(
            actions=[
                {"op": "submit", "token": "http401", "body_hex": "4869"},
                {"op": "wait", "token": "http401"},
            ],
        )
        self.assertEqual(unauthorized["actions"][1]["result"], "ERR\nHTTP_401")
        self.assertNotIn("secret-provider-error-body", json.dumps(unauthorized))

        self.state.clear()
        self.state.status = 200
        self.state.response = provider_response(
            '{"is_target_language":"yes","translation":"ignored"}')
        malformed = self.run_child(
            actions=[
                {"op": "submit", "token": "bad-model", "body_hex": "4869"},
                {"op": "wait", "token": "bad-model"},
            ],
        )
        self.assertEqual(malformed["actions"][1]["result"], "ERR\nBAD_RESPONSE")

    def test_game_thread_submit_does_not_wait_for_slow_http(self) -> None:
        self.state.delay = 0.6
        result = self.run_child(
            actions=[
                {"op": "submit", "token": "slow-one", "body_hex": "6f6e65"},
                {"op": "submit", "token": "slow-two", "body_hex": "74776f"},
            ],
        )
        self.assertEqual([entry["accepted"] for entry in result["actions"]], [1, 1])
        self.assertLess(max(entry["elapsed_ms"] for entry in result["actions"]), 200.0)
        self.assertEqual(result["bootstrap_count"], 1)

        self.state.clear()
        delayed_registry = {
            "user": {
                "HD2CT_API_URL": self.url + "/async-bootstrap",
                "HD2CT_MODEL": "fake-model",
                "HD2CT_API_KEY": "fake-api-key",
            },
            "__registry_delay_ms": 100,
        }
        asynchronous = self.run_environment_child(
            delayed_registry,
            actions=[
                {"op": "submit", "token": "async-first", "body_hex": "6869"},
                {"op": "poll", "token": "async-first"},
                {"op": "concurrent_submit", "tokens": ["async-second", "async-third"]},
                {"op": "wait", "token": "async-first"},
                {"op": "wait", "token": "async-second"},
                {"op": "wait", "token": "async-third"},
            ],
        )
        self.assertEqual(asynchronous["bootstrap_count"], 1)
        self.assertEqual(asynchronous["actions"][0]["accepted"], 1)
        self.assertLess(asynchronous["actions"][0]["elapsed_ms"], 200.0)
        self.assertIsNone(asynchronous["actions"][1]["result"])
        self.assertEqual(asynchronous["actions"][2]["accepted"], [1, 1])
        self.assertEqual(asynchronous["actions"][3]["result"], "OK\n你好，绝地潜兵。")
        self.assertEqual(asynchronous["actions"][4]["result"], "OK\n你好，绝地潜兵。")
        self.assertEqual(asynchronous["actions"][5]["result"], "OK\n你好，绝地潜兵。")

        self.state.clear()
        self.state.delay = 0.6
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-target-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            marker_path = Path(temporary_directory) / "first-request-started"
            registry = {
                "user": {
                    "HD2CT_API_URL": self.url + "/google/pending-target-snapshot",
                    "HD2CT_MODEL": "",
                    "HD2CT_API_KEY": "fake-api-key",
                },
                "__target_values_path": str(values_path),
                "__target_primary": self._target_values_line(1),
            }
            self.state.request_started_marker = marker_path
            self.state.response_by_target = {
                "zh-CN": google_response("默认语言", "en"),
                "en": google_response("English target", "zh-CN"),
            }
            snapshot = self.run_environment_child(
                registry,
                actions=[
                    {"op": "submit", "token": "target-snapshot-first",
                     "body_hex": "73616d6520736f75726365"},
                    {"op": "wait_file", "path": str(marker_path)},
                    {"op": "target_file", "kind": "primary",
                     "contents": self._target_values_line(3)},
                    {"op": "submit", "token": "target-snapshot-second",
                     "body_hex": "73616d6520736f75726365"},
                    {"op": "wait", "token": "target-snapshot-second"},
                    {"op": "wait", "token": "target-snapshot-first"},
                ],
            )
        self.assertTrue(snapshot["actions"][1]["found"])
        self.assertEqual([payload["target"] for payload in self.state.payloads],
                         ["zh-CN", "en"])
        self.assertEqual(snapshot["actions"][4]["result"], "OK\nEnglish target")
        self.assertEqual(snapshot["actions"][5]["result"], "OK\n默认语言")
        self.assertEqual(snapshot["cache_count"], 2)

        self.state.clear()
        cached = self.run_child(
            actions=[
                {"op": "submit", "token": "cache-one", "body_hex": "6869"},
                {"op": "wait", "token": "cache-one"},
                {"op": "submit", "token": "cache-two", "body_hex": "6869"},
                {"op": "wait", "token": "cache-two"},
            ],
        )
        self.assertEqual(cached["actions"][1]["result"], "OK\n你好，绝地潜兵。")
        self.assertEqual(cached["actions"][3]["result"], "OK\n你好，绝地潜兵。")
        self.assertEqual(len(self.state.paths), 1)

        self.state.clear()
        cancel_all = self.run_environment_child(
            {
                "user": {
                    "HD2CT_API_URL": self.url + "/cancel-all",
                    "HD2CT_MODEL": "fake-model",
                    "HD2CT_API_KEY": "fake-api-key",
                },
                "__lock_hold_ms": 1000,
            },
            actions=[
                {"op": "submit", "token": "cancel-all-old", "body_hex": "6869"},
                {"op": "hold_lock"},
                {"op": "cancel_all"},
                {"op": "release_lock"},
                {"op": "submit", "token": "cancel-all-new", "body_hex": "6869"},
                {"op": "poll", "token": "cancel-all-old"},
                {"op": "wait", "token": "cancel-all-new"},
            ],
        )
        self.assertTrue(cancel_all["actions"][1]["held"])
        self.assertEqual(cancel_all["actions"][2]["cancelled"], 1)
        self.assertEqual(cancel_all["actions"][4]["accepted"], 1)
        self.assertIsNone(cancel_all["actions"][5]["result"])
        self.assertEqual(cancel_all["actions"][6]["result"], "OK\n你好，绝地潜兵。")

        self.state.clear()
        failed_worker = self.run_environment_child(
            {
                "user": {
                    "HD2CT_API_URL": self.url + "/worker-failure",
                    "HD2CT_MODEL": "fake-model",
                    "HD2CT_API_KEY": "fake-api-key",
                },
                "__worker_failure": True,
            },
            actions=[
                {"op": "submit", "token": "worker-failure", "body_hex": "6869"},
                {"op": "wait", "token": "worker-failure"},
            ],
        )
        self.assertEqual(failed_worker["status"], 4)
        self.assertEqual(failed_worker["actions"][1]["result"], "ERR\nSERVICE_ERROR")
        self.assertEqual(self.state.paths, [])

        for failure_case, injected_failure in (
            ("bootstrap", {"__bootstrap_create_failure": True}),
            ("second-worker", {"__worker_create_failure_at": 2}),
        ):
            with self.subTest(thread_creation_failure=failure_case):
                self.state.clear()
                thread_creation_failure = self.run_environment_child(
                    {
                        "user": {
                            "HD2CT_API_URL": self.url + "/thread-create-failure",
                            "HD2CT_MODEL": "fake-model",
                            "HD2CT_API_KEY": "fake-api-key",
                        },
                        **injected_failure,
                    },
                    actions=[
                        {"op": "submit", "token": f"{failure_case}-failure-one",
                         "body_hex": "6869"},
                        {"op": "wait", "token": f"{failure_case}-failure-one"},
                        {"op": "cancel", "token": f"{failure_case}-failure-one"},
                        {"op": "submit", "token": f"{failure_case}-failure-two",
                         "body_hex": "6869"},
                        {"op": "wait", "token": f"{failure_case}-failure-two"},
                    ],
                )
                self.assertEqual(thread_creation_failure["status"], 4)
                self.assertEqual(thread_creation_failure["actions"][0]["accepted"], 1)
                self.assertEqual(thread_creation_failure["actions"][1]["result"],
                                 "ERR\nSERVICE_ERROR")
                self.assertEqual(thread_creation_failure["actions"][2]["cancelled"], 1)
                self.assertEqual(thread_creation_failure["actions"][3]["accepted"], 1)
                self.assertEqual(thread_creation_failure["actions"][4]["result"],
                                 "ERR\nSERVICE_ERROR")
                self.assertEqual(self.state.paths, [])

        self.state.clear()
        self.state.delay = 0.5
        cancelled = self.run_child(
            actions=[
                {"op": "submit", "token": "cancel-me", "body_hex": "6869"},
                {"op": "cancel", "token": "cancel-me"},
                {"op": "sleep", "seconds": 0.6},
                {"op": "poll", "token": "cancel-me"},
            ],
        )
        self.assertEqual(cancelled["actions"][0]["accepted"], 1)
        self.assertEqual(cancelled["actions"][1]["cancelled"], 1)
        self.assertIsNone(cancelled["actions"][3]["result"])

        self.state.clear()
        self.state.delay = 10.5
        timed_out = self.run_child(
            actions=[
                {"op": "submit", "token": "timeout-one", "body_hex": "6869"},
                {"op": "wait", "token": "timeout-one", "timeout": 12.0},
            ],
            timeout=15.0,
            timeout_index=1,
        )
        self.assertEqual(timed_out["timeout"], 10)
        self.assertEqual(timed_out["actions"][1]["result"], "ERR\nTIMEOUT")

if __name__ == "__main__":
    unittest.main()
