"""原生 HTTP worker 的本机回环验证，不读取真实配置或调用付费接口。"""

from __future__ import annotations

import ctypes
import gc
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
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "tools" / "build_native_http.py"
WINDOWS = os.name == "nt"
sys.path.insert(0, str(ROOT / "tools"))
import build_native_http
from scripts import target_languages
from scripts import free_service_config

NATIVE_SOURCES = build_native_http.NATIVE_SOURCES
NATIVE_TEST_ROOT = ROOT / "artifacts" / "validation"
TARGET_LANGUAGE_CATALOGUE = target_languages.load_catalogue()
AI_RESPONSE_FORMAT = {
    "type": "json_object",
}
INTERFACE_TYPE_OPTION_ID = "hd2chattranslate.interface_type"
ANTHROPIC_VERSION = "2023-06-01"

ENVIRONMENT_CHILD = r"""
import ctypes
import json
import os
import sys
import time
import threading
from pathlib import Path

dll_path, registry_json = sys.argv[1], sys.argv[2]
fixture = json.loads(registry_json)
process_region = fixture.pop("__process_region", None)
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
if process_region is not None:
    os.environ["HD2CT_REGION"] = process_region

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
    ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
]
lib.fixture_ReadRuntimeSettings.restype = None
lib.fixture_RequestDeadlineRemaining.argtypes = [ctypes.c_char_p]
lib.fixture_RequestDeadlineRemaining.restype = ctypes.c_uint64
lib.fixture_NormalizeUrl.argtypes = [
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_NormalizeUrl.restype = ctypes.c_uint32
lib.fixture_PrepareJobConfig.argtypes = [
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
]
lib.fixture_PrepareJobConfig.restype = ctypes.c_uint32
lib.fixture_CacheCount.argtypes = []
lib.fixture_CacheCount.restype = ctypes.c_uint32
lib.fixture_RateCount.argtypes = []
lib.fixture_RateCount.restype = ctypes.c_uint32
lib.fixture_FillRateLimit.argtypes = []
lib.fixture_FillRateLimit.restype = None
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
lib.fixture_ParseBingPage.argtypes = [
    ctypes.c_char_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
]
lib.fixture_ParseBingPage.restype = ctypes.c_int
lib.fixture_ParseBingResponse.argtypes = [
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_char_p),
]
lib.fixture_ParseBingResponse.restype = ctypes.c_int
lib.fixture_BingReset.argtypes = [ctypes.c_uint32]
lib.fixture_BingReset.restype = ctypes.c_int
lib.fixture_BingTranslate.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_BingTranslate.restype = ctypes.c_int

lib.fixture_ClearRegistry()
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

player_name_buffers = {}

def submit(token, body_hex="73616665", flags=0, player_name=None,
           player_name_hex=None):
    body = bytes.fromhex(body_hex)
    backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
    if player_name is None and player_name_hex is None:
        player_name_pointer = None
    else:
        player_name_bytes = (
            bytes.fromhex(player_name_hex) if player_name_hex is not None
            else player_name.encode("utf-8")
        )
        player_name_buffers[token] = ctypes.create_string_buffer(
            player_name_bytes, len(player_name_bytes) + 1,
        )
        player_name_pointer = ctypes.cast(
            player_name_buffers[token], ctypes.c_char_p,
        )
    return lib.HD2CT_Submit(
        token.encode("ascii"), ctypes.cast(backing, ctypes.c_void_p), len(body),
        flags, player_name_pointer,
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
        accepted = submit(
            action["token"], action.get("body_hex", ""), action.get("flags", 0),
            action.get("player_name"), action.get("player_name_hex"),
        )
        replacement = action.get("player_name_after_submit")
        if replacement is not None and accepted:
            replacement_bytes = replacement.encode("utf-8")
            name_buffer = player_name_buffers[action["token"]]
            if len(replacement_bytes) != len(name_buffer) - 1:
                raise RuntimeError("replacement player name must keep the same byte length")
            ctypes.memmove(name_buffer, replacement_bytes, len(replacement_bytes))
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
    elif op == "prepare_config":
        url = ctypes.create_string_buffer(2049)
        region = ctypes.create_string_buffer(65)
        adapter_id = ctypes.c_uint32()
        status = lib.fixture_PrepareJobConfig(
            action["interface_type"], ctypes.cast(url, ctypes.c_void_p),
            len(url), ctypes.cast(region, ctypes.c_void_p), len(region),
            ctypes.byref(adapter_id),
        )
        action_results.append({
            "status": status,
            "url": url.value.decode("utf-8"),
            "region": region.value.decode("utf-8"),
            "adapter_id": adapter_id.value,
        })
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
        ignore_self = ctypes.c_uint32()
        lib.fixture_ReadRuntimeSettings(
            ctypes.byref(target_language), ctypes.byref(timeout_seconds),
            ctypes.byref(menu_enabled), ctypes.byref(ignore_self),
        )
        action_results.append({
            "target_language": target_language.value,
            "timeout_seconds": timeout_seconds.value,
            "menu_enabled": menu_enabled.value,
            "ignore_self": ignore_self.value,
        })
    elif op == "request_remaining":
        remaining = lib.fixture_RequestDeadlineRemaining(action["token"].encode("ascii"))
        action_results.append({"remaining_ms": int(remaining)})
    elif op == "fill_rate_limit":
        lib.fixture_FillRateLimit()
        action_results.append({"filled": True})
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
ignore_self = ctypes.c_uint32()
lib.fixture_ReadRuntimeSettings(
    ctypes.byref(target_language), ctypes.byref(timeout_seconds),
    ctypes.byref(menu_enabled), ctypes.byref(ignore_self),
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
    "ignore_self": ignore_self.value,
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

__declspec(dllexport) int __cdecl fixture_ParseFreeResponse(
    char *response, uint32_t response_bytes, char *translation,
    uint32_t *translation_bytes, char *failure_code, uint32_t failure_capacity)
{
    HD2CT_WorkerJob job;
    const char *code = NULL;
    int accepted;
    memset(&job, 0, sizeof(job));
    if (response == NULL || translation == NULL || translation_bytes == NULL ||
        failure_code == NULL || failure_capacity == 0) return 0;
    *translation_bytes = 0;
    accepted = hd2ct_parse_free_response(&job, response, response_bytes,
                                         translation, translation_bytes, &code);
    if (code == NULL || strlen(code) + 1u > failure_capacity) return 0;
    memcpy(failure_code, code, strlen(code) + 1u);
    return accepted;
}

__declspec(dllexport) int __cdecl fixture_ParseBingPage(
    const char *html, size_t html_bytes,
    char *ig, size_t ig_capacity, char *iid, size_t iid_capacity,
    char *key, size_t key_capacity, char *token, size_t token_capacity)
{
    return hd2ct_bing_test_parse_page(
        html, html_bytes, ig, ig_capacity, iid, iid_capacity,
        key, key_capacity, token, token_capacity);
}

__declspec(dllexport) int __cdecl fixture_ParseBingResponse(
    const char *body, size_t body_bytes, const char *target,
    char *translation, uint32_t *translation_bytes,
    const char **failure_code)
{
    return hd2ct_bing_test_parse_response(
        body, body_bytes, target, translation, translation_bytes, failure_code);
}

__declspec(dllexport) int __cdecl fixture_ParseOfficialBingResponse(
    char *response, uint32_t response_bytes, uint32_t target_language,
    char *translation, uint32_t *translation_bytes, char *failure_code,
    uint32_t failure_capacity)
{
    HD2CT_WorkerJob job;
    const char *code = "BAD_RESPONSE";
    int accepted;
    if (response == NULL || translation == NULL || translation_bytes == NULL ||
        failure_code == NULL || failure_capacity == 0u) return 0;
    memset(&job, 0, sizeof(job));
    memcpy(job.source, "Hold this position.", sizeof("Hold this position."));
    job.source_bytes = (uint32_t)(sizeof("Hold this position.") - 1u);
    job.target_language = target_language;
    *translation_bytes = 0u;
    accepted = hd2ct_parse_bing_response(
        &job, response, response_bytes, translation, translation_bytes, &code);
    if (code == NULL) code = "BAD_RESPONSE";
    if (strlen(code) + 1u > failure_capacity) return 0;
    memcpy(failure_code, code, strlen(code) + 1u);
    return accepted;
}

static HD2CT_BingState fixture_bing_states[2];
static volatile LONG fixture_bing_state_initialized[2];
static volatile LONG64 fixture_bing_serial = 1000;

__declspec(dllexport) int __cdecl fixture_BingReset(uint32_t state_index)
{
    if (state_index >= 2u) return 0;
    if (InterlockedCompareExchange(&fixture_bing_state_initialized[state_index],
                                   0, 0) != 0) {
        hd2ct_bing_state_destroy(&fixture_bing_states[state_index]);
    }
    if (!hd2ct_bing_state_init(&fixture_bing_states[state_index])) return 0;
    InterlockedExchange(&fixture_bing_state_initialized[state_index], 1);
    AcquireSRWLockExclusive(&g_lock);
    memset(&g_jobs[state_index], 0, sizeof(g_jobs[state_index]));
    ReleaseSRWLockExclusive(&g_lock);
    return 1;
}

__declspec(dllexport) int __cdecl fixture_BingExpire(uint32_t state_index)
{
    if (state_index >= 2u ||
        InterlockedCompareExchange(&fixture_bing_state_initialized[state_index],
                                   0, 0) == 0) return 0;
    fixture_bing_states[state_index].initialized_ms = 0u;
    return 1;
}

__declspec(dllexport) int __cdecl fixture_BingTranslate(
    uint32_t state_index, uint32_t target_language, const char *source,
    uint32_t timeout_ms, char *translation, uint32_t translation_capacity,
    uint32_t *translation_bytes, int *request_attempted,
    char *failure_code, uint32_t failure_capacity)
{
    HD2CT_WorkerJob job;
    HD2CT_BingContext context;
    const char *code = "NETWORK";
    size_t source_bytes;
    int attempted = 0;
    int result;
    if (state_index >= 2u || target_language == 0u || source == NULL ||
        translation == NULL || translation_bytes == NULL ||
        request_attempted == NULL || failure_code == NULL ||
        failure_capacity == 0u ||
        InterlockedCompareExchange(&fixture_bing_state_initialized[state_index],
                                   0, 0) == 0) return -1;
    source_bytes = strlen(source);
    if (source_bytes == 0u || source_bytes > HD2CT_MAX_SOURCE) return -1;
    memset(&job, 0, sizeof(job));
    job.slot_index = state_index;
    job.serial = (uint64_t)InterlockedIncrement64(&fixture_bing_serial);
    job.target_language = target_language;
    memcpy(job.source, source, source_bytes + 1u);
    job.source_bytes = (uint32_t)source_bytes;
    AcquireSRWLockExclusive(&g_lock);
    memset(&g_jobs[state_index], 0, sizeof(g_jobs[state_index]));
    g_jobs[state_index].state = HD2CT_SLOT_ACTIVE;
    g_jobs[state_index].serial = job.serial;
    ReleaseSRWLockExclusive(&g_lock);
    context.state = &fixture_bing_states[state_index];
    context.job = &job;
    context.deadline = GetTickCount64() + timeout_ms;
    context.request_attempted = &attempted;
    context.failure_code = &code;
    *translation_bytes = 0u;
    result = hd2ct_bing_translate(&context, translation, translation_bytes);
    *request_attempted = attempted;
    if (code == NULL) code = "";
    if (strlen(code) + 1u > failure_capacity) result = -1;
    else memcpy(failure_code, code, strlen(code) + 1u);
    if (*translation_bytes >= translation_capacity) result = -1;
    AcquireSRWLockExclusive(&g_lock);
    if (g_jobs[state_index].serial == job.serial) {
        g_jobs[state_index].state = HD2CT_SLOT_FREE;
    }
    ReleaseSRWLockExclusive(&g_lock);
    SecureZeroMemory(&job, sizeof(job));
    return result;
}

__declspec(dllexport) uint32_t __cdecl fixture_SelectAdapter(
    const char *url, const char *model)
{
    return hd2ct_select_adapter(url, model);
}

__declspec(dllexport) int __cdecl fixture_BuildAiRequest(
    const char *url, uint32_t target_language, char *body, uint32_t body_capacity,
    wchar_t *request_path, uint32_t request_path_capacity)
{
    HD2CT_WorkerJob job;
    HD2CT_BuiltRequest request;
    size_t url_bytes;
    int result = 0;
    memset(&job, 0, sizeof(job));
    memset(&request, 0, sizeof(request));
    if (url == NULL || body == NULL || body_capacity == 0 ||
        request_path == NULL || request_path_capacity == 0) return 0;
    url_bytes = strlen(url);
    if (url_bytes == 0 || url_bytes > HD2CT_MAX_URL) return 0;
    memcpy(job.url, url, url_bytes + 1u);
    memcpy(job.model, "fixture-model", sizeof("fixture-model"));
    memcpy(job.api_key, "fixture-key", sizeof("fixture-key"));
    memcpy(job.source, "Hold this position.", sizeof("Hold this position."));
    job.source_bytes = (uint32_t)(sizeof("Hold this position.") - 1u);
    job.target_language = target_language;
    request_path[0] = L'\0';
    if (!hd2ct_build_ai_request(&job, &request) || request.body == NULL ||
        request.body_bytes + 1u > body_capacity) goto cleanup;
    memcpy(body, request.body, request.body_bytes + 1u);
    result = 1;

cleanup:
    if (request.body != NULL) {
        SecureZeroMemory(request.body, request.body_bytes);
        cJSON_free(request.body);
    }
    return result;
}

__declspec(dllexport) int __cdecl fixture_ParseAiResponse(
    const char *url, char *response, uint32_t response_bytes, char *translation,
    uint32_t *translation_bytes)
{
    HD2CT_WorkerJob job;
    const char *failure_code = NULL;
    size_t url_bytes;
    int result;
    memset(&job, 0, sizeof(job));
    if (url == NULL || response == NULL || translation == NULL ||
        translation_bytes == NULL) return 0;
    url_bytes = strlen(url);
    if (url_bytes == 0 || url_bytes > HD2CT_MAX_URL) return 0;
    memcpy(job.url, url, url_bytes + 1u);
    memcpy(job.source, "Hold this position.", sizeof("Hold this position."));
    job.source_bytes = (uint32_t)(sizeof("Hold this position.") - 1u);
    *translation_bytes = 0;
    result = hd2ct_parse_ai_response(&job, response, response_bytes, translation,
                                     translation_bytes, &failure_code);
    return result;
}

__declspec(dllexport) int __cdecl fixture_ParseAnthropicResponse(
    char *response, uint32_t response_bytes, uint32_t target_language,
    char *translation, uint32_t *translation_bytes, char *failure_code,
    uint32_t failure_capacity)
{
    HD2CT_WorkerJob job;
    const char *code = "BAD_RESPONSE";
    int accepted;
    if (response == NULL || translation == NULL || translation_bytes == NULL ||
        failure_code == NULL || failure_capacity == 0u) return 0;
    memset(&job, 0, sizeof(job));
    memcpy(job.source, "Hold this position.", sizeof("Hold this position."));
    job.source_bytes = (uint32_t)(sizeof("Hold this position.") - 1u);
    job.target_language = target_language;
    *translation_bytes = 0u;
    accepted = hd2ct_parse_anthropic_response(
        &job, response, response_bytes, translation, translation_bytes, &code);
    if (code == NULL) code = "BAD_RESPONSE";
    if (strlen(code) + 1u > failure_capacity) return 0;
    memcpy(failure_code, code, strlen(code) + 1u);
    return accepted;
}

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
    uint32_t *target_language, uint32_t *timeout_seconds, uint32_t *enabled,
    uint32_t *ignore_self)
{
    HD2CT_RuntimeSettings settings;
    hd2ct_read_applied_settings(&settings);
    if (target_language != NULL) *target_language = settings.target_language;
    if (timeout_seconds != NULL) *timeout_seconds = settings.timeout_seconds;
    if (enabled != NULL) *enabled = settings.enabled;
    if (ignore_self != NULL) *ignore_self = settings.ignore_self;
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

__declspec(dllexport) uint32_t __cdecl fixture_PrepareJobConfig(
    uint32_t interface_type, char *url, uint32_t url_capacity,
    char *region, uint32_t region_capacity, uint32_t *adapter_id)
{
    HD2CT_WorkerJob job;
    uint32_t status;
    size_t url_length;
    size_t region_length;
    if (url == NULL || url_capacity == 0u || region == NULL ||
        region_capacity == 0u || adapter_id == NULL) {
        return HD2CT_STATUS_INVALID_CONFIG;
    }
    url[0] = '\0';
    region[0] = '\0';
    *adapter_id = HD2CT_ADAPTER_UNKNOWN;
    memset(&job, 0, sizeof(job));
    status = hd2ct_prepare_job_config(&job, interface_type);
    if (status == HD2CT_STATUS_READY) {
        url_length = strlen(job.url);
        region_length = strlen(job.region);
        if (url_length >= url_capacity || region_length >= region_capacity) {
            status = HD2CT_STATUS_INVALID_CONFIG;
        } else {
            memcpy(url, job.url, url_length + 1u);
            memcpy(region, job.region, region_length + 1u);
            *adapter_id = job.adapter_id;
        }
    }
    SecureZeroMemory(&job, sizeof(job));
    return status;
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

__declspec(dllexport) void __cdecl fixture_FillRateLimit(void)
{
    uint32_t index;
    uint64_t now = GetTickCount64();
    AcquireSRWLockExclusive(&g_lock);
    for (index = 0u; index < HD2CT_REQUESTS_PER_PERIOD; ++index) {
        g_rate_times[index] = now;
    }
    g_rate_count = HD2CT_REQUESTS_PER_PERIOD;
    ReleaseSRWLockExclusive(&g_lock);
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


def free_success_response(text: str = "你好，绝地潜兵。") -> bytes:
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


def bing_page(ig: str = "0123456789ABCDEF", iid: str = "translator.5022",
              token: str = "fixture-token") -> bytes:
    return (
        '<script>var _G={x:"x",IG:"' + ig + '"}; '
        'var params_AbusePreventionHelper = [1790000000000,"' + token
        + '",3600000]; var tokenCopy=params_AbusePreventionHelper[1];</script>'
        '<div data-iid="' + iid + '"></div>'
        '<div data-iid="translator.5026"></div>'
        '<div data-iid="translator.5024"></div>'
    ).encode("utf-8")


def bing_response(translation: str, target: str = "zh-Hans") -> bytes:
    return json.dumps(
        [{"translations": [{"text": translation, "to": target}]}],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def anthropic_response(content: list[dict], stop_reason: str = "end_turn",
                       **extra: object) -> bytes:
    response = {
        "type": "message",
        "role": "assistant",
        "stop_reason": stop_reason,
        "content": content,
    }
    response.update(extra)
    return json.dumps(response, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.get_paths: list[str] = []
        self.get_headers: list[dict[str, str]] = []
        self.payloads: list[dict | list] = []
        self.content_types: list[str] = []
        self.status = 200
        self.response = provider_response(result_content())
        self.get_status = 200
        self.get_response = bing_page()
        self.set_cookie = "hd2ct-bing=fixture-cookie; Path=/; HttpOnly"
        self.response_by_target: dict[str, bytes] = {}
        self.response_by_path: dict[str, bytes] = {}
        self.response_by_payload_kind: dict[str, bytes] = {}
        self.status_by_path: dict[str, int] = {}
        self.delay = 0.0
        self.get_delay = 0.0
        self.delay_by_path: dict[str, float] = {}
        self.response_headers_by_path: dict[str, dict[str, str]] = {}
        self.headers: list[dict[str, str]] = []
        self.request_finished = threading.Event()
        self.request_started_marker: Path | None = None

    def clear(self) -> None:
        with self.lock:
            self.paths.clear()
            self.get_paths.clear()
            self.get_headers.clear()
            self.payloads.clear()
            self.content_types.clear()
            self.headers.clear()
            self.status = 200
            self.response = provider_response(result_content())
            self.get_status = 200
            self.get_response = bing_page()
            self.set_cookie = "hd2ct-bing=fixture-cookie; Path=/; HttpOnly"
            self.delay = 0.0
            self.get_delay = 0.0
            self.response_by_target.clear()
            self.response_by_path.clear()
            self.response_by_payload_kind.clear()
            self.status_by_path.clear()
            self.delay_by_path.clear()
            self.response_headers_by_path.clear()
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
        cls.free_config = cls.temp_path / "free-service-test.json"
        cls.free_config_url = "http://127.0.0.1:1/translate"
        cls.free_test_key1 = "00000000-0000-4000-8000-000000000001"
        cls.free_config.write_text(
            json.dumps({
                "endpoint_url": cls.free_config_url,
                "key1": cls.free_test_key1,
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _, cls.free_config_digest = free_service_config.load_config(cls.free_config)
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--output",
                str(cls.dll),
                "--meta",
                str(cls.meta),
                "--free-config",
                str(cls.free_config),
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

            def do_GET(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                with state.lock:
                    state.get_paths.append(self.path)
                    state.get_headers.append(dict(self.headers.items()))
                    response_status = state.status_by_path.get(path, state.get_status)
                    response = state.response_by_path.get(path, state.get_response)
                    response_headers = dict(state.response_headers_by_path.get(path, {}))
                    delay = state.delay_by_path.get(path, state.get_delay)
                    request_started_marker = state.request_started_marker
                    cookie = state.set_cookie
                if request_started_marker is not None:
                    request_started_marker.write_text("started", encoding="ascii")
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(response_status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    if path == "/translator" and cookie:
                        self.send_header("Set-Cookie", cookie)
                    for name, value in response_headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    state.request_finished.set()

            def do_POST(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
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
                    response_status = state.status_by_path.get(path, state.status)
                    response = state.response_by_path.get(path, state.response)
                    has_response_for_path = path in state.response_by_path
                    response_by_target = dict(state.response_by_target)
                    response_by_payload_kind = dict(state.response_by_payload_kind)
                    response_headers = dict(state.response_headers_by_path.get(path, {}))
                    delay = state.delay_by_path.get(path, state.delay)
                    request_started_marker = state.request_started_marker
                response_key = None
                if isinstance(payload, dict):
                    response_key = payload.get("target") or payload.get("to")
                if (response_key is None and isinstance(payload, dict) and
                        isinstance(payload.get("messages"), list)):
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
                if not has_response_for_path and response_key in response_by_target:
                    response = response_by_target[response_key]
                if not has_response_for_path and isinstance(payload, dict):
                    payload_kind = (
                        "anthropic" if "system" in payload and "max_tokens" in payload
                        else "chat" if "response_format" in payload
                        else None
                    )
                    if payload_kind in response_by_payload_kind:
                        response = response_by_payload_kind[payload_kind]
                elif not has_response_for_path and isinstance(payload, list):
                    if "bing" in response_by_payload_kind:
                        response = response_by_payload_kind["bing"]
                if request_started_marker is not None:
                    request_started_marker.write_text("started", encoding="ascii")
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(response_status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    for name, value in response_headers.items():
                        self.send_header(name, value)
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
        ignore_self_value: str | None = None,
        free_service_url: str | None = None,
        bing_base_url: str | None = None,
        interface_type: int | str | None = None,
        user_registry_overrides: dict[str, str] | None = None,
        machine_registry: dict[str, str] | None = None,
        process_region: str | None = None,
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
        if user_registry_overrides:
            config.update(user_registry_overrides)
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-menu-values-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            registry = {
                "user": config,
                "machine": machine_registry or {},
                "__target_values_path": str(values_path),
                "__target_primary": self._runtime_values_text(
                    target_language_index, enabled_value, timeout_index,
                    ignore_self_value, interface_type,
                ),
            }
            if process_region is not None:
                registry["__process_region"] = process_region
            return self.run_environment_child(
                registry, actions=actions, timeout=timeout,
                free_service_url=free_service_url,
                bing_base_url=bing_base_url,
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
    def environment_test_dll(
        cls, free_service_url: str | None = None,
        bing_base_url: str | None = None,
    ) -> Path:
        url_suffix = hashlib.sha256(
            ((free_service_url or "") + "\0" + (bing_base_url or "")).encode("utf-8")
        ).hexdigest()[:12]
        dll = cls.temp_path / f"hd2ct_http_environment_test_{url_suffix}.dll"
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
        if free_service_url is not None:
            command.append(f'-DHD2CT_FREE_SERVICE_URL="{free_service_url}"')
        if bing_base_url is not None:
            command.append(f'-DHD2CT_BING_TEST_BASE_URL="{bing_base_url}"')
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
        free_service_url: str | None = None,
        bing_base_url: str | None = None,
    ) -> dict:
        command = [
            sys.executable,
            "-c",
            ENVIRONMENT_CHILD,
            str(self.environment_test_dll(free_service_url, bing_base_url)),
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
                self.assertEqual(set(payload), {"model", "messages", "response_format"})
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
        ignore_self_value: str | None = None,
        interface_type: int | str | None = None,
    ) -> str:
        options = TARGET_LANGUAGE_CATALOGUE["menu_options"]
        values = [
            f"{TARGET_LANGUAGE_CATALOGUE['option_id']}\t{target_index}\n",
            f"{options['enabled']['option_id']}\t{enabled_value}\n",
            f"{options['timeout']['option_id']}\t{timeout_index}\n",
        ]
        if ignore_self_value is not None:
            values.append(
                f"{options['ignore_self']['option_id']}\t{ignore_self_value}\n"
            )
        if interface_type is not None:
            values.append(f"{INTERFACE_TYPE_OPTION_ID}\t{interface_type}\n")
        return "".join(values)

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
        self.assertEqual(menu_options["ignore_self"]["option_id"],
                         "hd2chattranslate.ignore_self")
        self.assertIs(menu_options["ignore_self"]["default"], True)
        self.assertEqual(
            len({TARGET_LANGUAGE_CATALOGUE["option_id"],
                 menu_options["enabled"]["option_id"],
                 menu_options["ignore_self"]["option_id"],
                 menu_options["timeout"]["option_id"]}),
            4,
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
        free_service_url = self.url + "/translate"
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
            {"op": "submit", "token": "unknown-service-self",
             "body_hex": "73616665", "flags": 1},
            {"op": "wait", "token": "unknown-service-self"},
            {"op": "submit", "token": "unknown-service", "body_hex": "73616665"},
            {"op": "wait", "token": "unknown-service"},
        ]
        result = self.run_environment_child(
            {"user": {
                "HD2CT_API_URL": "http://127.0.0.1:1/unknown-service",
                "HD2CT_MODEL": "",
            }},
            actions=unknown_actions,
        )
        self.assertEqual(result["status"], 5)
        self.assertEqual(result["enabled"], 0)
        self.assertEqual([item.get("accepted") for item in result["actions"]],
                         [1, None, 1, None])
        self.assertEqual(result["actions"][1]["result"], "SKIP\n")
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
                    "ignore_self": 1,
                })

        settings = self.run_menu_child(
            {}, None, actions=[{"op": "read_settings"}],
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 1,
            "timeout_seconds": 20,
            "menu_enabled": 1,
            "ignore_self": 1,
        })
        settings = self.run_menu_child(
            {}, self._runtime_values_text(
                target_index=99, enabled_value="True", timeout_index=4,
                ignore_self_value="True",
            ),
            actions=[{"op": "read_settings"}],
        )
        self.assertEqual(settings["actions"][0], {
            "target_language": 1,
            "timeout_seconds": 20,
            "menu_enabled": 1,
            "ignore_self": 1,
        })

        settings = self.run_menu_child(
            {}, self._runtime_values_text(ignore_self_value="false"),
            actions=[{"op": "read_settings"}],
        )
        self.assertEqual(settings["actions"][0]["ignore_self"], 0)

        primary_with_other_mod = "othermod.some_option\t2\n"
        backup_settings = self._runtime_values_text(
            target_index=3, enabled_value="false", timeout_index=3,
            ignore_self_value="false",
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
            "ignore_self": 1,
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
            "ignore_self": 0,
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
                if missing_name == "HD2CT_API_URL":
                    self.state.clear()
                    self.state.response = free_success_response("free fallback")
                    result = self.run_environment_child(
                        {"user": user, "machine": machine},
                        expected_config={
                            "HD2CT_API_URL": free_service_url,
                            "HD2CT_MODEL": "",
                            "HD2CT_API_KEY": "",
                            "HD2CT_APP_ID": "",
                        },
                        actions=[
                            {"op": "submit", "token": "missing-url-free", "body_hex": "6869",
                             "player_name": "Test Player"},
                            {"op": "wait", "token": "missing-url-free"},
                        ],
                        free_service_url=free_service_url,
                    )
                    self.assertEqual(result["status"], 0)
                    self.assertEqual(result["enabled"], 1)
                    self.assertEqual(result["selected_config_matches"], 1)
                    self.assertEqual(result["actions"][1]["result"], "OK\nfree fallback")
                    self.assertEqual(self.state.paths, ["/translate"])
                else:
                    result = self.run_environment_child({"user": user, "machine": machine})
                if missing_name == "HD2CT_MODEL":
                    self.assertEqual(result["status"], 5)
                    self.assertEqual(result["enabled"], 0)
                    self.assertEqual(result["actions"][1]["result"], "ERR\nUNSUPPORTED_SERVICE")
                elif missing_name == "HD2CT_API_KEY":
                    self.assertEqual(result["status"], 1)
                    self.assertEqual(result["enabled"], 0)
                    self.assertEqual(result["submit"], 1)
                    self.assertEqual(result["actions"][1]["result"], "ERR\nMISSING_CONFIG")

        self.state.clear()
        self.state.response = free_success_response("empty-url free")
        result = self.run_environment_child(
            {
                "user": {
                    "HD2CT_API_URL": "",
                    "HD2CT_MODEL": "invalid\nlegacy-model",
                    "HD2CT_API_KEY": "invalid\nlegacy-key",
                    "HD2CT_APP_ID": "invalid\nlegacy-app-id",
                },
                "machine": {
                    "HD2CT_API_URL": self.url + "/Google/machine-fallback",
                    "HD2CT_MODEL": "machine-model",
                    "HD2CT_API_KEY": "machine-key",
                },
            },
            expected_config={
                "HD2CT_API_URL": free_service_url,
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "",
                "HD2CT_APP_ID": "",
            },
            actions=[
                {"op": "submit", "token": "empty-url-free", "body_hex": "6869",
                 "player_name": "Test Player"},
                {"op": "wait", "token": "empty-url-free"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["selected_config_matches"], 1)
        self.assertEqual(result["actions"][1]["result"], "OK\nempty-url free")
        self.assertEqual(self.state.paths, ["/translate"])

        self.state.clear()
        whitespace_url = self.run_environment_child(
            {"user": {
                "HD2CT_API_URL": " \t ",
                "HD2CT_MODEL": "fake-model",
                "HD2CT_API_KEY": "fake-key",
            }}
        )
        self.assertEqual(whitespace_url["status"], 2)
        self.assertEqual(whitespace_url["enabled"], 0)
        self.assertEqual(whitespace_url["actions"][1]["result"], "ERR\nINVALID_CONFIG")
        self.assertEqual(self.state.paths, [])

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

        free_service_url = self.url + "/translate"
        free_source = "集合，保持这个位置。"
        free_config = {
            "HD2CT_API_URL": "",
            "HD2CT_MODEL": "invalid\nlegacy-model",
            "HD2CT_API_KEY": "invalid\nlegacy-key",
            "HD2CT_APP_ID": "invalid\nlegacy-app-id",
        }
        self.state.clear()
        self.state.response = free_success_response(free_source)
        free_player_name = "  e\u0301 Игрок  "
        overwritten_player_name = "  E\u0301 Игрок  "
        free_result = self.run_environment_child(
            {"user": free_config, "__registry_delay_ms": 200},
            {
                "HD2CT_API_URL": free_service_url,
                "HD2CT_MODEL": "",
                "HD2CT_API_KEY": "",
                "HD2CT_APP_ID": "",
            },
            [
                {"op": "submit", "token": "free-same", "body_hex": free_source.encode("utf-8").hex(),
                 "player_name": free_player_name,
                 "player_name_after_submit": overwritten_player_name},
                {"op": "wait", "token": "free-same"},
                {"op": "submit", "token": "free-same-cache", "body_hex": free_source.encode("utf-8").hex(),
                 "player_name": free_player_name},
                {"op": "wait", "token": "free-same-cache"},
            ],
            timeout=15.0,
            free_service_url=free_service_url,
        )
        self.assertEqual(free_result["status"], 0)
        self.assertEqual(free_result["enabled"], 1)
        self.assertEqual(free_result["selected_config_matches"], 1)
        self.assertEqual(free_result["actions"][1]["result"], "SKIP\n")
        self.assertEqual(free_result["actions"][3]["result"], "SKIP\n")
        self.assertEqual(free_result["cache_count"], 1)
        self.assertEqual(self.state.paths, ["/translate"])
        self.assertEqual(self.state.content_types[0], "application/json")
        self.assertEqual(
            set(self.state.payloads[0]),
            {"key1", "key2", "text", "target_language"},
        )
        self.assertEqual(self.state.payloads[0]["key1"], self.free_test_key1)
        self.assertEqual(
            self.state.payloads[0]["key2"],
            hashlib.sha256(free_player_name.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(len(self.state.payloads[0]["key2"]), 64)
        self.assertEqual(self.state.payloads[0]["key2"], self.state.payloads[0]["key2"].lower())
        self.assertEqual(self.state.payloads[0]["text"], free_source)
        self.assertEqual(
            self.state.payloads[0]["target_language"],
            TARGET_LANGUAGE_CATALOGUE["languages"][0]["youdao"],
        )

        self.state.clear()
        unknown_player = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-player-unknown", "body_hex": "6869"},
                {"op": "wait", "token": "free-player-unknown"},
                {"op": "submit", "token": "free-empty-player", "body_hex": "6869",
                 "player_name": ""},
                {"op": "wait", "token": "free-empty-player"},
                {"op": "submit", "token": "free-long-player", "body_hex": "6869",
                 "player_name": "x" * 129},
                {"op": "submit", "token": "free-invalid-utf8-player", "body_hex": "6869",
                 "player_name_hex": "c0af"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(unknown_player["actions"][1]["result"], "ERR\nFREE_PLAYER_UNKNOWN")
        self.assertEqual(unknown_player["actions"][3]["result"], "ERR\nFREE_PLAYER_UNKNOWN")
        self.assertEqual(unknown_player["actions"][4]["accepted"], 0)
        self.assertEqual(unknown_player["actions"][5]["accepted"], 0)
        self.assertEqual(self.state.paths, [])
        self.assertEqual(unknown_player["rate_count"], 0)

        self.state.clear()
        self_state_skip = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-self-no-player", "flags": 1,
                 "body_hex": "6869"},
                {"op": "wait", "token": "free-self-no-player"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(self_state_skip["actions"][1]["result"], "SKIP\n")
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-free-disabled-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            values_path = Path(temporary_directory) / "ModOptionsMenu.values"
            disabled_skip = self.run_environment_child(
                {"user": free_config,
                 "__target_values_path": str(values_path),
                 "__target_primary": self._runtime_values_text(
                     target_index=1, enabled_value="false", timeout_index=2,
                 )},
                actions=[
                    {"op": "submit", "token": "free-disabled-no-player", "body_hex": "6869"},
                    {"op": "wait", "token": "free-disabled-no-player"},
                ],
                free_service_url=free_service_url,
            )
        self.assertEqual(disabled_skip["actions"][1]["result"], "SKIP\n")
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        self.state.response = free_failure_response("玩家身份哈希无效")
        free_error = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-auth-error", "body_hex": "6869",
                 "player_name": "Test Player"},
                {"op": "wait", "token": "free-auth-error"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(free_error["actions"][1]["result"], "ERR\nFREE_PLAYER_INVALID")

        self.state.clear()
        self.state.response = free_failure_response("remote secret reason")
        free_unknown_error = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-unknown-error", "body_hex": "6869",
                 "player_name": "Test Player"},
                {"op": "wait", "token": "free-unknown-error"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(
            free_unknown_error["actions"][1]["result"], "ERR\nSERVICE_ERROR",
        )
        self.assertNotIn("remote secret reason", json.dumps(free_unknown_error))

        for reason, expected_code in (
            ("免费版的余额已用尽，请自行配置提供商", "FREE_QUOTA_EXCEEDED"),
            ("服务器错误，请联系作者", "FREE_SERVER_ERROR"),
            ("请升级hd2-chat-translate以继续使用免费版服务", "FREE_UPGRADE_REQUIRED"),
            ("DAILY_LIMIT_REACHED", "FREE_DAILY_LIMIT"),
            ("今日免费次数已用尽，请明天再试", "FREE_DAILY_LIMIT"),
        ):
            self.state.clear()
            self.state.response = free_failure_response(reason)
            free_reason_error = self.run_environment_child(
                {"user": free_config},
                actions=[
                    {"op": "submit", "token": "free-new-error", "body_hex": "6869",
                     "player_name": "Test Player"},
                    {"op": "wait", "token": "free-new-error"},
                ],
                free_service_url=free_service_url,
            )
            self.assertEqual(
                free_reason_error["actions"][1]["result"], "ERR\n" + expected_code,
            )

        self.state.clear()
        self.state.response = b'{"success":true,"data":{"text":"bad","reason":"unexpected"}}'
        free_malformed = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-malformed", "body_hex": "6869",
                 "player_name": "Test Player"},
                {"op": "wait", "token": "free-malformed"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(free_malformed["actions"][1]["result"], "ERR\nFREE_SERVER_ERROR")

        self.state.clear()
        self.state.status = 503
        self.state.response = b"private server error body"
        free_http_error = self.run_environment_child(
            {"user": free_config},
            actions=[
                {"op": "submit", "token": "free-http-503", "body_hex": "6869",
                 "player_name": "Test Player"},
                {"op": "wait", "token": "free-http-503"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(free_http_error["actions"][1]["result"], "ERR\nFREE_SERVER_ERROR")
        self.assertNotIn("private server error body", json.dumps(free_http_error))

    def _exercise_official_protocol_requests(self) -> None:
        fixture = ctypes.CDLL(str(self.environment_test_dll()))
        fixture.fixture_SelectAdapter.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        fixture.fixture_SelectAdapter.restype = ctypes.c_uint32
        auto_routes = (
            (b"", b"fake-model", 6),
            (b"http://127.0.0.1/anthropic/custom", b"fake-model", 8),
            (b"http://127.0.0.1/custom/messages/", b"fake-model", 8),
            (b"http://127.0.0.1/openai/compat", b"claude-leftover", 1),
            (b"http://127.0.0.1/bing/custom", b"", 7),
            (b"http://127.0.0.1/google/bing", b"", 2),
            (b"http://127.0.0.1/baidu", b"", 3),
            (b"http://127.0.0.1/youdao", b"", 4),
            (b"http://127.0.0.1/custom-service", b"", 5),
        )
        for url, model, expected in auto_routes:
            with self.subTest(auto_route=(url, model)):
                self.assertEqual(fixture.fixture_SelectAdapter(url, model), expected)
        fixture.fixture_SelectAdapter = None
        fixture_handle = fixture._handle
        del fixture
        gc.collect()
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        self.assertEqual(free_library(fixture_handle), 1)

        normalization_cases = (
            (7, self.url, self.url + "/translate"),
            (7, "https://eastus.cognitiveservices.azure.com",
             "https://eastus.cognitiveservices.azure.com/translator/text/v3.0/translate"),
            (7, "https://eastasia.cognitiveservices.azure.cn",
             "https://eastasia.cognitiveservices.azure.cn/translator/text/v3.0/translate"),
            (7, "https://api.translator.azure.cn",
             "https://api.translator.azure.cn/translate"),
            (8, self.url, self.url + "/v1/messages"),
            (8, self.url + "/v1", self.url + "/v1/messages"),
            (8, self.url + "/v1/", self.url + "/v1/messages"),
            (8, self.url + "/custom/messages", self.url + "/custom/messages"),
            (7, self.url + "/custom/bing", self.url + "/custom/bing"),
        )
        normalization_actions = [
            {"op": "normalize", "adapter_id": adapter_id, "url": url}
            for adapter_id, url, _ in normalization_cases
        ]
        normalization_actions.extend([
            {"op": "normalize", "adapter_id": 7, "url": self.url + "?unsafe=query"},
            {"op": "normalize", "adapter_id": 8, "url": self.url + "#unsafe"},
        ])
        normalized = self.run_environment_child({}, actions=normalization_actions)
        for index, (_, _, expected) in enumerate(normalization_cases):
            self.assertEqual(normalized["actions"][index], {
                "accepted": 1, "url": expected,
            })
        self.assertEqual(normalized["actions"][-2]["accepted"], 0)
        self.assertEqual(normalized["actions"][-1]["accepted"], 0)

        for interface_type, expected_url in (
            (6, "https://api.translator.azure.cn/translate"),
            (7, "https://api.cognitive.microsofttranslator.com/translate"),
        ):
            with self.subTest(bing_default_interface=interface_type):
                self.state.clear()
                prepared = self.run_child(
                    "",
                    actions=[{"op": "prepare_config",
                              "interface_type": interface_type}],
                    interface_type=interface_type,
                    model="",
                )
                self.assertEqual(prepared["actions"][0], {
                    "status": 0,
                    "url": expected_url,
                    "region": "",
                    "adapter_id": 7,
                })
                self.assertEqual(self.state.paths, [])

        self.state.clear()
        source = "Need an extraction route."
        anthropic_json = result_content("正在前往撤离点", is_target_language=False)
        self.state.response = anthropic_response([
            {"type": "text", "text": anthropic_json[:13]},
            {"type": "text", "text": anthropic_json[13:]},
        ])
        automatic_anthropic = self.run_child(
            self.url + "/messages",
            [
                {"op": "submit", "token": "anthropic-auto",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "anthropic-auto"},
            ],
            model="fake-model",
        )
        self.assertEqual(automatic_anthropic["actions"][1]["result"],
                         "OK\n正在前往撤离点")
        self.assertEqual(self.state.paths, ["/messages"])
        payload = self.state.payloads[0]
        self.assertEqual(set(payload), {"model", "max_tokens", "system", "messages"})
        self.assertEqual(payload["model"], "fake-model")
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["messages"], [{"role": "user", "content": source}])
        self.assertIn("目标语言为简体中文", payload["system"])
        self.assertNotIn("response_format", payload)
        self.assertNotIn("tools", payload)
        headers = {name.lower(): value for name, value in self.state.headers[0].items()}
        self.assertEqual(headers.get("x-api-key"), "fake-api-key")
        self.assertEqual(headers.get("anthropic-version"), ANTHROPIC_VERSION)
        self.assertNotIn("authorization", headers)

        self.state.clear()
        self.state.response = anthropic_response([
            {"type": "text", "text": result_content("繁體翻譯")},
        ])
        traditional = self.run_child(
            self.url + "/anthropic-custom",
            [
                {"op": "submit", "token": "anthropic-traditional",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "anthropic-traditional"},
            ],
            target_language_index=2,
            model="stale-chat-model",
            interface_type=3,
        )
        self.assertEqual(traditional["actions"][1]["result"], "OK\n繁體翻譯")
        self.assertEqual(self.state.paths, ["/anthropic-custom"])
        self.assertIn("繁體中文", self.state.payloads[0]["system"])
        self.assertNotIn("Charger=牛", self.state.payloads[0]["system"])
        self.assertNotIn("lol=哈哈", self.state.payloads[0]["system"])

        self.state.clear()
        source = "Move to extraction."
        self.state.response_by_payload_kind = {
            "chat": provider_response(result_content("Chat translation")),
            "anthropic": anthropic_response([
                {"type": "text", "text": result_content("Anthropic translation")},
            ]),
            "bing": bing_response("Bing translation"),
        }
        switched = self.run_child(
            self.url + "/shared-custom-endpoint",
            [
                {"op": "submit", "token": "interface-chat-one",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "interface-chat-one"},
                {"op": "target_file", "kind": "primary",
                 "contents": self._runtime_values_text(interface_type=3)},
                {"op": "submit", "token": "interface-anthropic",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "interface-anthropic"},
                {"op": "target_file", "kind": "primary",
                 "contents": self._runtime_values_text(interface_type=7)},
                {"op": "submit", "token": "interface-bing",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "interface-bing"},
                {"op": "target_file", "kind": "primary",
                 "contents": self._runtime_values_text(interface_type=2)},
                {"op": "submit", "token": "interface-chat-cache",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "interface-chat-cache"},
            ],
            model="stale-claude-model",
            interface_type=2,
            user_registry_overrides={
                "HD2CT_APP_ID": "invalid app id",
                "HD2CT_REGION": "user-east-2",
            },
            machine_registry={"HD2CT_REGION": "machine-west"},
            process_region="process-fake-region",
        )
        self.assertEqual(switched["actions"][1]["result"], "OK\nChat translation")
        self.assertEqual(switched["actions"][4]["result"], "OK\nAnthropic translation")
        self.assertEqual(switched["actions"][7]["result"], "OK\nBing translation")
        self.assertEqual(switched["actions"][10]["result"], "OK\nChat translation")
        self.assertEqual(self.state.paths, [
            "/shared-custom-endpoint",
            "/shared-custom-endpoint",
            "/shared-custom-endpoint?api-version=3.0&to=zh-Hans",
        ])
        self.assertEqual(len(self.state.payloads), 3)
        self.assertEqual(self.state.payloads[2], [{"Text": source}])
        self.assertEqual(self.state.payloads[1]["system"],
                         self.state.payloads[0]["messages"][0]["content"])
        bing_headers = {
            name.lower(): value for name, value in self.state.headers[2].items()
        }
        self.assertEqual(bing_headers.get("ocp-apim-subscription-key"), "fake-api-key")
        self.assertEqual(bing_headers.get("ocp-apim-subscription-region"), "user-east-2")
        self.assertNotIn("authorization", bing_headers)
        self.assertEqual(parse_qs(urlsplit(self.state.paths[2]).query), {
            "api-version": ["3.0"], "to": ["zh-Hans"],
        })
        self.assertEqual(switched["cache_count"], 3)

        self.state.clear()
        self.state.response = bing_response("自动识别回退")
        invalid_menu_fallback = self.run_child(
            self.url + "/bing-invalid-menu",
            [
                {"op": "submit", "token": "invalid-menu-auto",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "invalid-menu-auto"},
            ],
            model="",
            interface_type=9,
        )
        self.assertEqual(invalid_menu_fallback["actions"][1]["result"],
                         "OK\n自动识别回退")
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/bing-invalid-menu")

        self.state.clear()
        self.state.response = bing_response("显式恢复")
        recovered = self.run_child(
            self.url + "/unsupported-bootstrap",
            [
                {"op": "submit", "token": "explicit-recovers-bootstrap",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "explicit-recovers-bootstrap"},
            ],
            model="",
            interface_type=7,
        )
        self.assertEqual(recovered["actions"][1]["result"], "OK\n显式恢复")
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/unsupported-bootstrap")

        self.state.clear()
        self.state.response = google_response("Google translation", "en")
        irrelevant_values = self.run_child(
            self.url + "/google-explicit",
            [
                {"op": "submit", "token": "google-ignores-ai-and-region",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "google-ignores-ai-and-region"},
            ],
            model="stale-claude-model",
            interface_type=8,
            user_registry_overrides={
                "HD2CT_APP_ID": "invalid app id",
                "HD2CT_REGION": "invalid_region!",
            },
            process_region="process-fake-region",
        )
        self.assertEqual(irrelevant_values["actions"][1]["result"],
                         "OK\nGoogle translation")
        google_headers = {
            name.lower(): value for name, value in self.state.headers[0].items()
        }
        self.assertNotIn("ocp-apim-subscription-region", google_headers)

        self.state.clear()
        self.state.response = bing_response("空 region 合法")
        empty_region = self.run_child(
            self.url + "/bing-empty-region",
            [
                {"op": "submit", "token": "bing-empty-region",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "bing-empty-region"},
            ],
            interface_type=7,
            user_registry_overrides={"HD2CT_REGION": ""},
            machine_registry={"HD2CT_REGION": "machine-region"},
            process_region="process-fake-region",
        )
        self.assertEqual(empty_region["actions"][1]["result"], "OK\n空 region 合法")
        empty_headers = {
            name.lower(): value for name, value in self.state.headers[0].items()
        }
        self.assertNotIn("ocp-apim-subscription-region", empty_headers)

        self.state.clear()
        invalid_region = self.run_child(
            self.url + "/bing-invalid-region",
            [
                {"op": "submit", "token": "bing-invalid-region",
                 "body_hex": source.encode("utf-8").hex()},
                {"op": "wait", "token": "bing-invalid-region"},
            ],
            interface_type=7,
            user_registry_overrides={"HD2CT_REGION": "invalid_region!"},
            machine_registry={"HD2CT_REGION": "machine-region"},
            process_region="process-region",
        )
        self.assertEqual(invalid_region["actions"][1]["result"], "ERR\nINVALID_CONFIG")
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        self.state.response = free_success_response("FREE translation")
        free_selected = self.run_child(
            self.url + "/unsupported-free-bootstrap",
            [
                {"op": "submit", "token": "explicit-free",
                 "body_hex": source.encode("utf-8").hex(),
                 "player_name": "Fixture Player"},
                {"op": "wait", "token": "explicit-free"},
            ],
            model="",
            interface_type=1,
            free_service_url=self.url + "/translate",
        )
        self.assertEqual(free_selected["actions"][1]["result"], "OK\nFREE translation")
        self.assertEqual(self.state.paths, ["/translate"])
        self.assertNotIn("/unsupported-free-bootstrap", " ".join(self.state.paths))

        for name, enabled_value, flags in (
            ("disabled-before-config", "false", 0),
            ("self-before-config", "true", 1),
        ):
            with self.subTest(explicit_skip=name):
                self.state.clear()
                actions = [
                    {"op": "submit", "token": name, "flags": flags,
                     "body_hex": source.encode("utf-8").hex()},
                    {"op": "wait", "token": name},
                ]
                skipped = self.run_child(
                    self.url + "/messages",
                    actions,
                    model="",
                    enabled_value=enabled_value,
                    interface_type=3,
                )
                self.assertEqual(skipped["actions"][1]["result"], "SKIP\n")
                self.assertEqual(self.state.paths, [])

    def _exercise_official_response_parsers(self) -> None:
        fixture = ctypes.CDLL(str(self.environment_test_dll()))
        function_argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p, ctypes.c_uint32,
        ]
        for function_name in (
            "fixture_ParseAnthropicResponse",
            "fixture_ParseOfficialBingResponse",
        ):
            function = getattr(fixture, function_name)
            function.argtypes = function_argtypes
            function.restype = ctypes.c_int

        def parse_response(
            function_name: str, response: bytes, target_language: int = 1,
        ) -> tuple[bool, str, str]:
            body = ctypes.create_string_buffer(response, len(response) + 1)
            translation = ctypes.create_string_buffer(16385)
            written = ctypes.c_uint32()
            failure_code = ctypes.create_string_buffer(64)
            accepted = getattr(fixture, function_name)(
                ctypes.cast(body, ctypes.c_void_p), len(response), target_language,
                ctypes.cast(translation, ctypes.c_void_p), ctypes.byref(written),
                ctypes.cast(failure_code, ctypes.c_void_p), len(failure_code),
            )
            return (
                bool(accepted),
                translation.raw[:written.value].decode("utf-8"),
                failure_code.value.decode("ascii"),
            )

        inner = result_content("Anthropic parser translation")
        complete = anthropic_response([
            {"type": "text", "text": inner[:11]},
            {"type": "text", "text": inner[11:]},
        ])
        accepted, translation, failure = parse_response(
            "fixture_ParseAnthropicResponse", complete,
        )
        self.assertTrue(accepted)
        self.assertEqual(translation, "Anthropic parser translation")
        self.assertEqual(failure, "BAD_RESPONSE")
        stop_sequence = anthropic_response(
            [{"type": "text", "text": inner}], stop_reason="stop_sequence",
        )
        self.assertTrue(parse_response(
            "fixture_ParseAnthropicResponse", stop_sequence,
        )[0])
        already_target = anthropic_response([
            {"type": "text", "text": result_content("ignored", True)},
        ])
        accepted, translation, _ = parse_response(
            "fixture_ParseAnthropicResponse", already_target,
        )
        self.assertTrue(accepted)
        self.assertEqual(translation, "Hold this position.")

        anthropic_invalid = (
            ("malformed-json", b"not JSON", "BAD_RESPONSE"),
            ("wrong-role", anthropic_response(
                [{"type": "text", "text": inner}], role="user",
            ), "BAD_RESPONSE"),
            ("max-tokens", anthropic_response(
                [{"type": "text", "text": inner}], stop_reason="max_tokens",
            ), "BAD_RESPONSE"),
            ("unknown-stop", anthropic_response(
                [{"type": "text", "text": inner}], stop_reason="pending",
            ), "BAD_RESPONSE"),
            ("stop-details-refusal", anthropic_response(
                [{"type": "text", "text": inner}],
                stop_details={"type": "refusal"},
            ), "BAD_RESPONSE"),
            ("tool-use", anthropic_response([
                {"type": "tool_use", "id": "fixture", "name": "x"},
            ], stop_reason="tool_use"), "BAD_RESPONSE"),
            ("empty-content", anthropic_response([]), "BAD_RESPONSE"),
            ("inner-extra-field", anthropic_response([
                {"type": "text", "text": '{"is_target_language":false,'
                 '"translation":"bad","extra":1}'},
            ]), "BAD_RESPONSE"),
            ("inner-duplicate-field", anthropic_response([
                {"type": "text", "text": '{"is_target_language":false,'
                 '"translation":"one","translation":"two"}'},
            ]), "BAD_RESPONSE"),
            ("inner-nul", anthropic_response([
                {"type": "text", "text": '{"is_target_language":false,'
                 '"translation":"\\u0000"}'},
            ]), "BAD_RESPONSE"),
            ("error-object", json.dumps({
                "type": "error", "error": {"message": "private anthropic error"},
            }, separators=(",", ":")).encode("utf-8"), "SERVICE_ERROR"),
            ("invalid-utf8", b'{"type":"message","role":"assistant",'
             b'"stop_reason":"end_turn","content":[{"type":"text",'
             b'"text":"\xff"}]}', "BAD_RESPONSE"),
        )
        for name, response, expected_code in anthropic_invalid:
            with self.subTest(anthropic_response=name):
                accepted, translation, code = parse_response(
                    "fixture_ParseAnthropicResponse", response,
                )
                self.assertFalse(accepted)
                self.assertEqual(translation, "")
                self.assertEqual(code, expected_code)
                self.assertNotIn("private anthropic error", code)

        bing_valid = bing_response("Bing parser translation", "zh-Hans")
        accepted, translation, failure = parse_response(
            "fixture_ParseOfficialBingResponse", bing_valid,
        )
        self.assertTrue(accepted)
        self.assertEqual(translation, "Bing parser translation")
        self.assertEqual(failure, "BAD_RESPONSE")
        bing_invalid = (
            ("malformed-json", b"not JSON", "BAD_RESPONSE"),
            ("wrong-target", bing_response("wrong target", "en"), "BAD_RESPONSE"),
            ("multiple-responses", b'[{"translations":[{"text":"a",'
             b'"to":"zh-Hans"}]},{"translations":[{"text":"b",'
             b'"to":"zh-Hans"}]}]', "BAD_RESPONSE"),
            ("multiple-translations", b'[{"translations":[{"text":"a",'
             b'"to":"zh-Hans"},{"text":"b","to":"zh-Hans"}]}]',
             "BAD_RESPONSE"),
            ("missing-to", b'[{"translations":[{"text":"missing target"}]}]',
             "BAD_RESPONSE"),
            ("duplicate-text", b'[{"translations":[{"text":"one",'
             b'"text":"two","to":"zh-Hans"}]}]', "BAD_RESPONSE"),
            ("escaped-nul", b'[{"translations":[{"text":"\\u0000",'
             b'"to":"zh-Hans"}]}]', "BAD_RESPONSE"),
            ("error-object", b'{"error":{"message":"private Bing error"}}',
             "SERVICE_ERROR"),
            ("invalid-utf8", b'[{"translations":[{"text":"\xff",'
             b'"to":"zh-Hans"}]}]', "BAD_RESPONSE"),
        )
        for name, response, expected_code in bing_invalid:
            with self.subTest(official_bing_response=name):
                accepted, translation, code = parse_response(
                    "fixture_ParseOfficialBingResponse", response,
                )
                self.assertFalse(accepted)
                self.assertEqual(translation, "")
                self.assertEqual(code, expected_code)
                self.assertNotIn("private Bing error", code)

        fixture.fixture_ParseAnthropicResponse = None
        fixture.fixture_ParseOfficialBingResponse = None
        fixture_handle = fixture._handle
        del parse_response
        del fixture
        gc.collect()
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        self.assertEqual(free_library(fixture_handle), 1)

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
                self.assertEqual(
                    set(self.state.payloads[0]),
                    {"model", "messages", "response_format"},
                )
                self.assertEqual(self.state.payloads[0]["response_format"], AI_RESPONSE_FORMAT)

        fixture = ctypes.CDLL(str(self.environment_test_dll()))
        fixture.fixture_SelectAdapter.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        fixture.fixture_SelectAdapter.restype = ctypes.c_uint32
        self.assertEqual(fixture.fixture_SelectAdapter(b"", b"fake-model"), 6)
        self.assertEqual(fixture.fixture_SelectAdapter(b" ", b"fake-model"), 1)
        self.assertEqual(fixture.fixture_SelectAdapter(b"custom-url", b"fake-model"), 1)

        fixture.fixture_ParseFreeResponse.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_uint32,
        ]
        fixture.fixture_ParseFreeResponse.restype = ctypes.c_int

        def parse_free_fixture(response: bytes) -> tuple[bool, str, str]:
            body = ctypes.create_string_buffer(response, len(response) + 1)
            translation = ctypes.create_string_buffer(16385)
            written = ctypes.c_uint32()
            failure_code = ctypes.create_string_buffer(64)
            accepted = fixture.fixture_ParseFreeResponse(
                body, len(response), translation, ctypes.byref(written),
                failure_code, len(failure_code),
            )
            return (
                bool(accepted),
                translation.raw[:written.value].decode("utf-8"),
                failure_code.value.decode("ascii"),
            )

        accepted, translation, failure_code = parse_free_fixture(
            free_success_response("fixture translation")
        )
        self.assertTrue(accepted)
        self.assertEqual(translation, "fixture translation")
        self.assertEqual(failure_code, "FREE_SERVER_ERROR")
        for reason, expected_code in (
            ("玩家身份哈希无效", "FREE_PLAYER_INVALID"),
            ("DAILY_LIMIT_REACHED", "FREE_DAILY_LIMIT"),
            ("今日免费次数已用尽，请明天再试", "FREE_DAILY_LIMIT"),
            ("翻译配置不完整", "MISSING_CONFIG"),
            ("请求太频繁，请稍后再试", "RATE_LIMITED"),
            ("翻译额度不足", "QUOTA_EXCEEDED"),
            ("免费版的余额已用尽，请自行配置提供商", "FREE_QUOTA_EXCEEDED"),
            ("服务器错误，请联系作者", "FREE_SERVER_ERROR"),
            ("请升级hd2-chat-translate以继续使用免费版服务", "FREE_UPGRADE_REQUIRED"),
            ("请求的文本过长", "FREE_REQUEST_TEXT_TOO_LONG"),
            ("不支持此语言", "UNSUPPORTED_LANGUAGE"),
            ("请求参数有误", "REQUEST_INVALID"),
            ("请求超时", "TIMEOUT"),
            ("网络连接失败", "NETWORK"),
            ("返回内容无效", "BAD_RESPONSE"),
            ("返回内容过长", "RESPONSE_TOO_LARGE"),
            ("翻译服务异常", "SERVICE_ERROR"),
            ("无权使用此翻译服务", "ACCESS_DENIED"),
        ):
            with self.subTest(free_reason=reason):
                accepted, translation, failure_code = parse_free_fixture(
                    free_failure_response(reason)
                )
                self.assertFalse(accepted)
                self.assertEqual(translation, "")
                self.assertEqual(failure_code, expected_code)
        accepted, _, failure_code = parse_free_fixture(
            free_failure_response("remote secret and arbitrary reason")
        )
        self.assertFalse(accepted)
        self.assertEqual(failure_code, "SERVICE_ERROR")
        for malformed_free_response in (
            b'{"success":"true","data":{"text":"x","reason":""}}',
            b'{"success":true,"data":{"text":"x"}}',
            b'{"success":true,"data":{"text":"x","reason":"bad"}}',
            b'{"success":true,"data":{"text":"","reason":""}}',
            '{"success":false,"data":{"text":"remote secret","reason":"翻译服务异常"}}'.encode("utf-8"),
            b'{"success":true,"success":false,"data":{"text":"x","reason":""}}',
        ):
            with self.subTest(malformed_free_response=malformed_free_response):
                accepted, _, failure_code = parse_free_fixture(malformed_free_response)
                self.assertFalse(accepted)
                self.assertEqual(failure_code, "FREE_SERVER_ERROR")

        fixture.fixture_ParseBingPage.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ]
        fixture.fixture_ParseBingPage.restype = ctypes.c_int
        page = bing_page()
        ig = ctypes.create_string_buffer(129)
        iid = ctypes.create_string_buffer(129)
        key = ctypes.create_string_buffer(14)
        token = ctypes.create_string_buffer(513)
        parsed_page = fixture.fixture_ParseBingPage(
            page, len(page), ig, len(ig), iid, len(iid), key, len(key),
            token, len(token),
        )
        self.assertEqual(parsed_page, 1)
        self.assertEqual(ig.value, b"0123456789ABCDEF")
        self.assertEqual(iid.value, b"translator.5022")
        self.assertEqual(key.value, b"1790000000000")
        self.assertEqual(token.value, b"fixture-token")
        for invalid_page in (
            b"<html>no translator data</html>",
            page.replace(b"var params_AbusePreventionHelper", b"params_AbusePreventionHelper"),
            page.split(b"<div data-iid=")[0] + b"</script>",
            page + b'<script>var _G={x:"x",IG:"FEDCBA9876543210"};</script>',
            page + b'<script>var params_AbusePreventionHelper = '
            b'[1790000000001,"second-token",3600000];</script>',
        ):
            with self.subTest(invalid_bing_page=invalid_page):
                self.assertEqual(
                    fixture.fixture_ParseBingPage(
                        invalid_page, len(invalid_page), ig, len(ig), iid, len(iid),
                        key, len(key), token, len(token),
                    ),
                    0,
                )

        fixture.fixture_ParseBingResponse.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_char_p),
        ]
        fixture.fixture_ParseBingResponse.restype = ctypes.c_int

        def parse_bing_response(response: bytes, target: str) -> tuple[bool, str, str]:
            translation = ctypes.create_string_buffer(16385)
            written = ctypes.c_uint32(0)
            failure_code = ctypes.c_char_p()
            accepted = fixture.fixture_ParseBingResponse(
                response, len(response), target.encode("ascii"), translation,
                ctypes.byref(written), ctypes.byref(failure_code),
            )
            code = failure_code.value.decode("ascii") if failure_code.value else ""
            return bool(accepted), translation.raw[:written.value].decode("utf-8"), code

        accepted, parsed_translation, _ = parse_bing_response(
            bing_response("fixture translation"), "zh-Hans",
        )
        self.assertTrue(accepted)
        self.assertEqual(parsed_translation, "fixture translation")
        for invalid_response, target in (
            (b"not JSON", "zh-Hans"),
            (bing_response("wrong target", "en"), "zh-Hans"),
            (b'[{"translations":[]}]', "zh-Hans"),
        ):
            with self.subTest(invalid_bing_response=invalid_response):
                accepted, _, code = parse_bing_response(invalid_response, target)
                self.assertFalse(accepted)
                self.assertEqual(code, "BAD_RESPONSE")

        fixture.fixture_BuildAiRequest.argtypes = [
            ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_wchar_p, ctypes.c_uint32,
        ]
        fixture.fixture_BuildAiRequest.restype = ctypes.c_int

        def build_fixture(url: str, target_language: int = 1) -> tuple[dict, str]:
            body = ctypes.create_string_buffer(32769)
            request_path = ctypes.create_unicode_buffer(128)
            self.assertEqual(
                fixture.fixture_BuildAiRequest(
                    url.encode("utf-8"), target_language, body, len(body),
                    request_path, len(request_path),
                ),
                1,
            )
            return json.loads(body.value.decode("utf-8")), request_path.value

        compatible_urls = (
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/v1/chat/completions",
            "https://api.deepseek.com/beta/chat/completions",
            "https://api.deepseek.com/beta",
            "https://api.deepseek.com/custom/openai/compat",
            "https://api.deepseek.com.evil.test/chat/completions",
            "https://third-party.example/openai/v1/chat/completions",
        )
        for target_language in (1, 2):
            expected, expected_path = build_fixture(compatible_urls[0], target_language)
            self.assertEqual(expected_path, "")
            self.assertEqual(set(expected), {"model", "messages", "response_format"})
            self.assertEqual(expected["response_format"], AI_RESPONSE_FORMAT)
            self.assertEqual(
                [message["role"] for message in expected["messages"]],
                ["system", "user"],
            )
            self.assertEqual(expected["messages"][1]["content"], "Hold this position.")
            for url in compatible_urls:
                with self.subTest(url=url, target_language=target_language):
                    payload, request_path = build_fixture(url, target_language)
                    self.assertEqual(request_path, "")
                    self.assertEqual(payload, expected)
        fixture.fixture_BuildAiRequest = None
        fixture.fixture_ParseFreeResponse = None
        fixture.fixture_ParseBingPage = None
        fixture.fixture_ParseBingResponse = None
        fixture.fixture_SelectAdapter = None
        del build_fixture
        fixture_handle = fixture._handle
        del fixture
        gc.collect()
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        self.assertEqual(free_library(fixture_handle), 1)

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

        free_service_url = self.url + "/translate"
        free_normalized = self.run_environment_child(
            {},
            actions=[
                {"op": "normalize", "adapter_id": 6, "url": free_service_url},
                {"op": "normalize", "adapter_id": 6,
                 "url": free_service_url + "?redirect=http://example.test"},
                {"op": "normalize", "adapter_id": 6,
                 "url": self.url + "/other"},
                {"op": "normalize", "adapter_id": 1,
                 "url": "http://free-service.invalid/translate"},
            ],
            free_service_url=free_service_url,
        )
        self.assertEqual(free_normalized["actions"][0], {
            "accepted": 1, "url": free_service_url,
        })
        self.assertEqual(free_normalized["actions"][1]["accepted"], 0)
        self.assertEqual(free_normalized["actions"][2]["accepted"], 0)
        self.assertEqual(free_normalized["actions"][3]["accepted"], 0)

        configured_free = self.run_environment_child(
            {},
            actions=[{
                "op": "normalize", "adapter_id": 6, "url": self.free_config_url,
            }],
        )
        self.assertEqual(configured_free["actions"][0], {
            "accepted": 1, "url": self.free_config_url,
        })

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
        self._exercise_official_protocol_requests()

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
        self.assertEqual(set(payload), {"model", "messages", "response_format"})
        self.assertEqual(payload["response_format"], AI_RESPONSE_FORMAT)
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
        source_shared = "Hold this position together."
        self.state.response = provider_response(
            result_content("Hold position together translated", is_target_language=False))
        self_filtered = self.run_child(
            actions=[
                {"op": "submit", "token": "other-sender",
                 "body_hex": source_shared.encode("utf-8").hex()},
                {"op": "wait", "token": "other-sender"},
                {"op": "submit", "token": "self-sender", "flags": 1,
                 "body_hex": source_shared.encode("utf-8").hex()},
                {"op": "wait", "token": "self-sender"},
                {"op": "submit", "token": "other-sender-cache",
                 "body_hex": source_shared.encode("utf-8").hex()},
                {"op": "wait", "token": "other-sender-cache"},
                {"op": "submit", "token": "unknown-flags", "flags": 2,
                 "body_hex": source_shared.encode("utf-8").hex()},
            ],
        )
        self.assertEqual(self_filtered["actions"][1]["result"],
                         "OK\nHold position together translated")
        self.assertEqual(self_filtered["actions"][3]["result"], "SKIP\n")
        self.assertEqual(self_filtered["actions"][5]["result"],
                         "OK\nHold position together translated")
        self.assertEqual(self_filtered["actions"][6]["accepted"], 0)
        self.assertEqual(len(self.state.paths), 1)
        self.assertEqual(self_filtered["cache_count"], 1)
        self.assertEqual(self_filtered["rate_count"], 1)

        self.state.clear()
        self.state.response = provider_response(
            result_content("Self messages enabled", is_target_language=False))
        allowed_self = self.run_child(
            actions=[
                {"op": "submit", "token": "self-messages-enabled", "flags": 1,
                 "body_hex": "73656c66206d657373616765"},
                {"op": "wait", "token": "self-messages-enabled"},
            ],
            ignore_self_value="false",
        )
        self.assertEqual(allowed_self["actions"][1]["result"],
                         "OK\nSelf messages enabled")
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

        free_service_url = self.url + "/translate"
        for target_index, target in ((1, "zh-Hans"), (2, "zh-Hant"), (3, "en")):
            with self.subTest(bing_target=target):
                self.state.clear()
                self.state.response_by_target = {
                    target: bing_response("bing-" + target, target),
                }
                source = "Bing source " + target
                actions = [
                    {"op": "submit", "token": "bing-" + target,
                     "body_hex": source.encode("utf-8").hex(),
                     **({"player_name": "private Bing player"} if target_index == 2 else {})},
                    {"op": "wait", "token": "bing-" + target},
                ]
                result = self.run_child(
                    url="", actions=actions, model="",
                    target_language_index=target_index,
                    free_service_url=free_service_url,
                    bing_base_url=self.url,
                )
                self.assertEqual(result["actions"][1]["result"], "OK\nbing-" + target)
                self.assertEqual(self.state.get_paths, ["/translator"])
                self.assertEqual(len(self.state.paths), 1)
                path = self.state.paths[0]
                self.assertEqual(urlsplit(path).path, "/ttranslatev3")
                query = parse_qs(urlsplit(path).query, keep_blank_values=True)
                self.assertEqual(set(query), {"isVertical", "IG", "IID"})
                self.assertEqual(query["isVertical"], ["1"])
                self.assertEqual(query["IG"], ["0123456789ABCDEF"])
                self.assertEqual(query["IID"], ["translator.5022.1"])
                payload = self.state.payloads[0]
                self.assertEqual(set(payload), {"fromLang", "text", "to", "key", "token"})
                self.assertEqual(payload["fromLang"], "auto-detect")
                self.assertEqual(payload["text"], source)
                self.assertEqual(payload["to"], target)
                self.assertEqual(payload["key"], "1790000000000")
                self.assertEqual(payload["token"], "fixture-token")
                self.assertNotIn("key1", payload)
                self.assertNotIn("key2", payload)
                self.assertNotIn("private Bing player", json.dumps(payload))
                headers = {name.lower(): value for name, value in self.state.headers[0].items()}
                self.assertIn("hd2ct-bing=fixture-cookie", headers.get("cookie", ""))
                self.assertEqual(len(self.state.get_headers), 1)
                if target_index == 2:
                    self.assertNotIn(
                        hashlib.sha256(b"private Bing player").hexdigest(),
                        json.dumps(payload),
                    )

        self.state.clear()
        same_bing_source = "Hold this position."
        self.state.response_by_target = {
            "zh-Hans": bing_response(same_bing_source, "zh-Hans"),
        }
        same_bing = self.run_child(
            url="", model="", free_service_url=free_service_url,
            bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "bing-same-one",
                 "body_hex": same_bing_source.encode("utf-8").hex()},
                {"op": "wait", "token": "bing-same-one"},
                {"op": "submit", "token": "bing-same-two",
                 "body_hex": same_bing_source.encode("utf-8").hex()},
                {"op": "wait", "token": "bing-same-two"},
            ],
        )
        self.assertEqual(same_bing["actions"][1]["result"], "SKIP\n")
        self.assertEqual(same_bing["actions"][3]["result"], "SKIP\n")
        self.assertEqual(len(self.state.get_paths), 1)
        self.assertEqual(len(self.state.paths), 1)

        self.state.clear()
        self.state.response_by_path["/ttranslatev3"] = b"Bing error body"
        unknown_player = self.run_child(
            url="", model="", free_service_url=free_service_url,
            bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "bing-no-name-failure", "body_hex": "6869"},
                {"op": "wait", "token": "bing-no-name-failure"},
            ],
        )
        self.assertEqual(unknown_player["actions"][1]["result"], "ERR\nFREE_PLAYER_UNKNOWN")
        self.assertEqual(len(self.state.get_paths), 1)
        self.assertEqual(len(self.state.paths), 1)
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/ttranslatev3")

        self.state.clear()
        disabled = self.run_child(
            url="", model="", enabled_value="false",
            free_service_url=free_service_url, bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "bing-disabled", "body_hex": "6869"},
                {"op": "wait", "token": "bing-disabled"},
            ],
        )
        self.assertEqual(disabled["actions"][1]["result"], "SKIP\n")
        self.assertEqual(self.state.get_paths, [])
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        self_filtered = self.run_child(
            url="", model="", free_service_url=free_service_url,
            bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "bing-self-filtered", "flags": 1,
                 "body_hex": "6869"},
                {"op": "wait", "token": "bing-self-filtered"},
            ],
        )
        self.assertEqual(self_filtered["actions"][1]["result"], "SKIP\n")
        self.assertEqual(self.state.get_paths, [])
        self.assertEqual(self.state.paths, [])

        self.state.clear()
        rate_limited = self.run_child(
            url="", model="", free_service_url=free_service_url,
            bing_base_url=self.url,
            actions=[
                {"op": "fill_rate_limit"},
                {"op": "submit", "token": "bing-rate-limited", "body_hex": "6869"},
                {"op": "wait", "token": "bing-rate-limited"},
            ],
        )
        self.assertEqual(rate_limited["actions"][2]["result"], "ERR\nRATE_LIMITED")
        self.assertEqual(rate_limited["rate_count"], 30)
        self.assertFalse(any(urlsplit(path).path == "/translate" for path in self.state.paths))

    def test_http401_is_redacted_and_invalid_model_result_is_classified(self) -> None:
        self.assertEqual(self.meta_json["abi_version"], 4)
        self.assertEqual(
            self.meta_json["target_languages_sha256"],
            target_languages.catalogue_sha256(TARGET_LANGUAGE_CATALOGUE),
        )
        self.assertEqual(
            self.meta_json["free_service_config_sha256"],
            self.free_config_digest,
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

        fixture = ctypes.CDLL(str(self.environment_test_dll()))
        fixture.fixture_ParseAiResponse.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ]
        fixture.fixture_ParseAiResponse.restype = ctypes.c_int

        def parse_fixture(url: str, response: dict) -> tuple[bool, str]:
            raw = json.dumps(response, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            provider_body = ctypes.create_string_buffer(raw, len(raw) + 1)
            translation = ctypes.create_string_buffer(16385)
            written = ctypes.c_uint32()
            accepted = fixture.fixture_ParseAiResponse(
                url.encode("utf-8"), provider_body, len(raw),
                translation, ctypes.byref(written),
            )
            return bool(accepted), translation.raw[:written.value].decode("utf-8")

        compatible_response_urls = (
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/custom/openai/compat",
            "https://third-party.example/openai/v1/chat/completions",
        )
        generic_raw = json.loads(provider_response(
            result_content("generic response", is_target_language=False)
        ).decode("utf-8"))
        for url in compatible_response_urls:
            with self.subTest(valid_response_url=url):
                accepted, translation = parse_fixture(url, generic_raw)
                self.assertTrue(accepted)
                self.assertEqual(translation, "generic response")

        preserved_raw = json.loads(provider_response(
            result_content("忽略模型译文", is_target_language=True)
        ).decode("utf-8"))
        for url in compatible_response_urls:
            with self.subTest(skip_response_url=url):
                accepted, translation = parse_fixture(url, preserved_raw)
                self.assertTrue(accepted)
                self.assertEqual(translation, "Hold this position.")

        tool_only_response = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "type": "function",
                        "function": {
                            "name": "hd2ct_translation",
                            "arguments": result_content("ignored"),
                        },
                    }],
                },
            }],
        }
        invalid_contents = (
            '{"is_target_language":false}',
            '{"is_target_language":"false","translation":"bad"}',
            '{"is_target_language":false,"translation":7}',
            '{"is_target_language":false,"translation":"one",'
            '"translation":"two"}',
            '{"is_target_language":false,"translation":"\\u0000"}',
            '{"is_target_language":false,"translation":"ok","extra":1}',
            '{"is_target_language":false,"translation":"   "}',
            'not a JSON object',
            '{"is_target_language":false,"translation":"ok"} trailing',
        )
        for url in compatible_response_urls:
            with self.subTest(tool_only_url=url):
                accepted, _ = parse_fixture(url, tool_only_response)
                self.assertFalse(accepted)
            for content in invalid_contents:
                malformed_raw = json.loads(provider_response(content).decode("utf-8"))
                with self.subTest(invalid_content_url=url, content=content):
                    accepted, _ = parse_fixture(url, malformed_raw)
                    self.assertFalse(accepted)
        truncated = json.loads(provider_response(
            result_content("truncated"), finish_reason="length"
        ).decode("utf-8"))
        for url in compatible_response_urls:
            with self.subTest(truncated_response_url=url):
                accepted, _ = parse_fixture(url, truncated)
                self.assertFalse(accepted)
        fixture.fixture_ParseAiResponse = None
        del parse_fixture
        fixture_handle = fixture._handle
        del fixture
        gc.collect()
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        self.assertEqual(free_library(fixture_handle), 1)

        values, digest = free_service_config.load_config(self.free_config)
        self.assertEqual(values["endpoint_url"], self.free_config_url)
        self.assertEqual(values["key1"], self.free_test_key1)
        self.assertEqual(digest, hashlib.sha256(self.free_config.read_bytes()).hexdigest())

        invalid_values = (
            {"endpoint_url": self.free_config_url, "key1": self.free_test_key1,
             "extra": "value"},
            {"endpoint_url": "", "key1": self.free_test_key1},
            {"endpoint_url": self.free_config_url + "?redirect=example.invalid",
             "key1": self.free_test_key1},
            {"endpoint_url": "https://user@example.invalid/translate",
             "key1": self.free_test_key1},
            {"endpoint_url": "https://example.invalid/translate#fragment",
             "key1": self.free_test_key1},
            {"endpoint_url": "https://example.invalid/bad path",
             "key1": self.free_test_key1},
            {"endpoint_url": self.free_config_url, "key1": "invalid"},
            {"endpoint_url": "http://127.0.0.1/" + "x" * 2040,
             "key1": self.free_test_key1},
        )
        with tempfile.TemporaryDirectory(
            prefix="hd2ct-free-config-", dir=str(NATIVE_TEST_ROOT)
        ) as temporary_directory:
            config_path = Path(temporary_directory) / "free-service-test.json"
            for value in invalid_values:
                with self.subTest(invalid_config=value):
                    config_path.write_text(
                        json.dumps(value, separators=(",", ":")),
                        encoding="utf-8",
                        newline="\n",
                    )
                    with self.assertRaises(ValueError):
                        free_service_config.load_config(config_path)

            config_path.write_text(
                '{"endpoint_url":"http://127.0.0.1/translate",'
                '"endpoint_url":"http://127.0.0.1/translate",'
                f'"key1":"{self.free_test_key1}"}}',
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ValueError, "重复字段"):
                free_service_config.load_config(config_path)

            missing_path = Path(temporary_directory) / "missing.json"
            with self.assertRaisesRegex(ValueError, "free-service.example.json"):
                free_service_config.load_config(missing_path)

        free_service_url = self.url + "/translate"
        bing_failure_modes = (
            "missing-page-parameters",
            "http-200-error-body",
            "wrong-target",
            "malformed-json",
            "http-error",
            "unexpected-redirect",
        )
        for failure_mode in bing_failure_modes:
            with self.subTest(bing_failure=failure_mode):
                self.state.clear()
                self.state.response = free_success_response("legacy fallback")
                if failure_mode == "missing-page-parameters":
                    self.state.get_response = b"<html>translator page unavailable</html>"
                elif failure_mode == "http-200-error-body":
                    self.state.response_by_path["/ttranslatev3"] = (
                        b'{"error":"bing private error body"}'
                    )
                elif failure_mode == "wrong-target":
                    self.state.response_by_path["/ttranslatev3"] = bing_response(
                        "ignored wrong target", "en",
                    )
                elif failure_mode == "malformed-json":
                    self.state.response_by_path["/ttranslatev3"] = b"not JSON"
                elif failure_mode == "http-error":
                    self.state.get_status = 503
                    self.state.get_response = b"private Bing HTTP error body"
                else:
                    self.state.get_status = 302
                    self.state.response_headers_by_path["/translator"] = {
                        "Location": self.url + "/redirect-target",
                    }
                result = self.run_child(
                    url="", model="", free_service_url=free_service_url,
                    bing_base_url=self.url,
                    actions=[
                        {"op": "submit", "token": "bing-fallback-" + failure_mode,
                         "body_hex": "4869", "player_name": "Fallback Player"},
                        {"op": "wait", "token": "bing-fallback-" + failure_mode},
                    ],
                )
                self.assertEqual(result["actions"][1]["result"], "OK\nlegacy fallback")
                self.assertEqual(self.state.get_paths, ["/translator"])
                self.assertNotIn("/redirect-target", " ".join(self.state.get_paths))
                legacy_index = next(
                    index for index, path in enumerate(self.state.paths)
                    if urlsplit(path).path == "/translate"
                )
                legacy_payload = self.state.payloads[legacy_index]
                self.assertEqual(
                    set(legacy_payload), {"key1", "key2", "text", "target_language"},
                )
                self.assertEqual(legacy_payload["key2"], hashlib.sha256(
                    b"Fallback Player"
                ).hexdigest())
                self.assertEqual(legacy_payload["text"], "Hi")
                self.assertEqual(
                    legacy_payload["target_language"],
                    TARGET_LANGUAGE_CATALOGUE["languages"][0]["youdao"],
                )
                legacy_headers = {
                    name.lower(): value
                    for name, value in self.state.headers[legacy_index].items()
                }
                self.assertNotIn("cookie", legacy_headers)
                self.assertNotIn("token", legacy_payload)
                self.assertNotIn("fixture-token", json.dumps(legacy_payload))
                self.assertNotIn("private Bing HTTP error body", json.dumps(result))
                self.assertNotIn("bing private error body", json.dumps(result))

        fixture = ctypes.CDLL(str(self.environment_test_dll(free_service_url, self.url)))
        fixture.fixture_BingReset.argtypes = [ctypes.c_uint32]
        fixture.fixture_BingReset.restype = ctypes.c_int
        fixture.fixture_BingExpire.argtypes = [ctypes.c_uint32]
        fixture.fixture_BingExpire.restype = ctypes.c_int
        fixture.fixture_BingTranslate.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, ctypes.c_uint32,
        ]
        fixture.fixture_BingTranslate.restype = ctypes.c_int

        def direct_bing_translation(state_index: int, source: str) -> tuple[int, str, int, str]:
            translation = ctypes.create_string_buffer(16385)
            written = ctypes.c_uint32()
            attempted = ctypes.c_int()
            failure = ctypes.create_string_buffer(64)
            result = fixture.fixture_BingTranslate(
                state_index, 1, source.encode("utf-8"), 10000,
                translation, len(translation), ctypes.byref(written),
                ctypes.byref(attempted), failure, len(failure),
            )
            return (
                result,
                translation.raw[:written.value].decode("utf-8"),
                attempted.value,
                failure.value.decode("ascii"),
            )

        self.state.clear()
        self.state.response_by_target = {
            "zh-Hans": bing_response("state translation", "zh-Hans"),
        }
        self.assertEqual(fixture.fixture_BingReset(0), 1)
        self.assertEqual(fixture.fixture_BingReset(1), 1)
        first_state = direct_bing_translation(0, "state source one")
        second_request_same_state = direct_bing_translation(0, "state source two")
        other_state = direct_bing_translation(1, "other state source")
        self.assertEqual(first_state[:3], (1, "state translation", 1))
        self.assertEqual(second_request_same_state[:3], (1, "state translation", 1))
        self.assertEqual(other_state[:3], (1, "state translation", 1))
        self.assertEqual(self.state.get_paths, ["/translator", "/translator"])
        self.assertEqual(
            [parse_qs(urlsplit(path).query)["IID"][0] for path in self.state.paths],
            ["translator.5022.1", "translator.5022.2", "translator.5022.1"],
        )

        self.assertEqual(fixture.fixture_BingExpire(0), 1)
        expired_state = direct_bing_translation(0, "expired page source")
        self.assertEqual(expired_state[:3], (1, "state translation", 1))
        self.assertEqual(len(self.state.get_paths), 3)
        self.assertEqual(parse_qs(urlsplit(self.state.paths[-1]).query)["IID"],
                         ["translator.5022.1"])

        self.state.response_by_path["/ttranslatev3"] = b"malformed Bing response"
        failed_state = direct_bing_translation(0, "cooldown source")
        self.assertEqual(failed_state[0], 0)
        self.assertEqual(failed_state[2:], (1, "BAD_RESPONSE"))
        request_count_after_failure = len(self.state.paths)
        cooled_state = direct_bing_translation(0, "cooldown retry")
        self.assertEqual(cooled_state[0], 0)
        self.assertEqual(cooled_state[2], 0)
        self.assertEqual(len(self.state.paths), request_count_after_failure)
        self.state.response_by_path.clear()
        healthy_other_state = direct_bing_translation(1, "isolated state retry")
        self.assertEqual(healthy_other_state[:3], (1, "state translation", 1))
        self.assertEqual(len(self.state.get_paths), 3)
        self.assertEqual(parse_qs(urlsplit(self.state.paths[-1]).query)["IID"],
                         ["translator.5022.2"])
        self.assertEqual(fixture.fixture_BingReset(0), 1)
        self.assertEqual(fixture.fixture_BingReset(1), 1)
        fixture.fixture_BingTranslate = None
        fixture.fixture_BingReset = None
        fixture.fixture_BingExpire = None
        fixture_handle = fixture._handle
        del fixture
        gc.collect()
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        self.assertEqual(free_library(fixture_handle), 1)

        for interface_type, route_name in ((3, "anthropic"), (7, "bing")):
            with self.subTest(official_http_401=route_name):
                self.state.clear()
                self.state.status = 401
                self.state.response = f"private {route_name} HTTP body".encode("utf-8")
                unauthorized = self.run_child(
                    self.url + "/shared-401",
                    [
                        {"op": "submit", "token": "official-401-" + route_name,
                         "body_hex": "4869"},
                        {"op": "wait", "token": "official-401-" + route_name},
                    ],
                    interface_type=interface_type,
                )
                self.assertEqual(unauthorized["actions"][1]["result"], "ERR\nHTTP_401")
                self.assertNotIn(f"private {route_name} HTTP body",
                                 json.dumps(unauthorized))

        for interface_type, route_name, error_body in (
            (3, "anthropic", b'{"type":"error","error":{"message":"private anthropic body"}}'),
            (7, "bing", b'{"error":{"message":"private Bing body"}}'),
        ):
            with self.subTest(official_error_body=route_name):
                self.state.clear()
                self.state.response = error_body
                failed = self.run_child(
                    self.url + "/shared-provider-error",
                    [
                        {"op": "submit", "token": "official-error-" + route_name,
                         "body_hex": "4869"},
                        {"op": "wait", "token": "official-error-" + route_name},
                    ],
                    interface_type=interface_type,
                )
                self.assertEqual(failed["actions"][1]["result"],
                                 "ERR\nSERVICE_ERROR")
                self.assertNotIn(error_body.decode("utf-8"), json.dumps(failed))

        self._exercise_official_response_parsers()

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

        free_service_url = self.url + "/translate"
        self.state.clear()
        self.state.response = free_success_response("legacy after slow Bing")
        self.state.delay_by_path["/ttranslatev3"] = 6.5
        slow_bing = self.run_child(
            url="", model="", timeout_index=1,
            free_service_url=free_service_url, bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "slow-bing-fallback", "body_hex": "6869",
                 "player_name": "Slow Bing Player"},
                {"op": "wait", "token": "slow-bing-fallback", "timeout": 12.0},
            ],
            timeout=15.0,
        )
        self.assertEqual(slow_bing["timeout"], 10)
        self.assertEqual(slow_bing["actions"][1]["result"], "OK\nlegacy after slow Bing")
        self.assertLess(slow_bing["actions"][1]["elapsed_ms"], 10000.0)
        self.assertEqual(len(self.state.paths), 2)
        self.assertEqual(urlsplit(self.state.paths[0]).path, "/ttranslatev3")
        self.assertEqual(urlsplit(self.state.paths[1]).path, "/translate")
        fallback_headers = {
            name.lower(): value
            for name, value in self.state.headers[1].items()
        }
        self.assertNotIn("cookie", fallback_headers)
        self.assertNotIn("token", self.state.payloads[1])

        self.state.clear()
        marker_path = self.temp_path / "bing-cancel-request-started"
        marker_path.unlink(missing_ok=True)
        self.state.get_delay = 0.5
        self.state.request_started_marker = marker_path
        cancelled_bing = self.run_child(
            url="", model="", free_service_url=free_service_url,
            bing_base_url=self.url,
            actions=[
                {"op": "submit", "token": "cancel-bing", "body_hex": "6869"},
                {"op": "wait_file", "path": str(marker_path)},
                {"op": "cancel", "token": "cancel-bing"},
                {"op": "sleep", "seconds": 0.7},
                {"op": "poll", "token": "cancel-bing"},
            ],
            timeout=10.0,
        )
        self.assertTrue(cancelled_bing["actions"][1]["found"])
        self.assertEqual(cancelled_bing["actions"][2]["cancelled"], 1)
        self.assertIsNone(cancelled_bing["actions"][4]["result"])
        self.assertEqual(self.state.get_paths, ["/translator"])
        self.assertEqual(self.state.paths, [])

if __name__ == "__main__":
    unittest.main()
