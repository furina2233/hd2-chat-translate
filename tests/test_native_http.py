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

CHILD = r"""
import ctypes
import json
import sys
import time

dll_path, url = sys.argv[1], sys.argv[2]
actions = json.loads(sys.argv[3])
timeout_seconds = int(sys.argv[4]) if len(sys.argv) > 4 else 20
model = sys.argv[5] if len(sys.argv) > 5 else "fake-model"
lib = ctypes.CDLL(dll_path)
lib.HD2CT_ABIVersion.argtypes = []
lib.HD2CT_ABIVersion.restype = ctypes.c_uint32
lib.HD2CT_InitializeConfig.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32,
]
lib.HD2CT_InitializeConfig.restype = ctypes.c_uint32
lib.HD2CT_IsEnabled.argtypes = []
lib.HD2CT_IsEnabled.restype = ctypes.c_uint32
lib.HD2CT_LastStatus.argtypes = []
lib.HD2CT_LastStatus.restype = ctypes.c_uint32
lib.HD2CT_Submit.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
lib.HD2CT_Submit.restype = ctypes.c_uint32
lib.HD2CT_Poll.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
]
lib.HD2CT_Poll.restype = ctypes.c_uint32
lib.HD2CT_Cancel.argtypes = [ctypes.c_char_p]
lib.HD2CT_Cancel.restype = ctypes.c_uint32
lib.HD2CT_Disable.argtypes = []
lib.HD2CT_Disable.restype = None

test_started = time.perf_counter()
status = lib.HD2CT_InitializeConfig(
    url.encode("utf-8"), model.encode("utf-8"), b"fake-api-key", timeout_seconds,
)
result = {
    "abi": lib.HD2CT_ABIVersion(),
    "status": status,
    "enabled": lib.HD2CT_IsEnabled(),
    "last_status": lib.HD2CT_LastStatus(),
    "initialize_elapsed_ms": (time.perf_counter() - test_started) * 1000.0,
    "actions": [],
}

def submit(action):
    token = action["token"].encode("ascii")
    body = bytes.fromhex(action.get("body_hex", ""))
    backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
    return lib.HD2CT_Submit(token, ctypes.cast(backing, ctypes.c_void_p), len(body))

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

if status == 0:
    for action in actions:
        op = action["op"]
        if op == "submit":
            before = time.perf_counter()
            accepted = submit(action)
            result["actions"].append({
                "accepted": accepted,
                "elapsed_ms": (time.perf_counter() - before) * 1000.0,
            })
        elif op == "wait":
            poll_started = time.perf_counter()
            deadline = time.monotonic() + action.get("timeout", 8.0)
            value = None
            while time.monotonic() < deadline:
                value = poll(action["token"])
                if value is not None:
                    break
                time.sleep(0.01)
            result["actions"].append({
                "result": value,
                "first_result_elapsed_ms": (time.perf_counter() - poll_started) * 1000.0,
            })
        elif op == "poll":
            result["actions"].append({"result": poll(action["token"])})
        elif op == "cancel":
            result["actions"].append({
                "cancelled": lib.HD2CT_Cancel(action["token"].encode("ascii")),
            })
        elif op == "disable":
            lib.HD2CT_Disable()
            result["actions"].append({
                "enabled": lib.HD2CT_IsEnabled(),
                "status": lib.HD2CT_LastStatus(),
            })
        elif op == "sleep":
            time.sleep(action["seconds"])
            result["actions"].append({"slept": action["seconds"]})
        else:
            raise RuntimeError("unknown test action")

result["final_last_status"] = lib.HD2CT_LastStatus()
result["elapsed_before_print_ms"] = (time.perf_counter() - test_started) * 1000.0
print(json.dumps(result, ensure_ascii=True), flush=True)
"""

ENVIRONMENT_CHILD = r"""
import ctypes
import json
import os
import sys
import time

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
lib.fixture_SetRegistryValue.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p]
lib.fixture_SetRegistryValue.restype = ctypes.c_int
lib.fixture_ConfigMatches.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
]
lib.fixture_ConfigMatches.restype = ctypes.c_int
lib.fixture_TimeoutSeconds.argtypes = []
lib.fixture_TimeoutSeconds.restype = ctypes.c_uint32
lib.fixture_NormalizeUrl.argtypes = [
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
]
lib.fixture_NormalizeUrl.restype = ctypes.c_uint32
lib.fixture_CacheCount.argtypes = []
lib.fixture_CacheCount.restype = ctypes.c_uint32
lib.fixture_RateCount.argtypes = []
lib.fixture_RateCount.restype = ctypes.c_uint32
lib.HD2CT_InitializeEnvironment.argtypes = []
lib.HD2CT_InitializeEnvironment.restype = ctypes.c_uint32
lib.HD2CT_IsEnabled.argtypes = []
lib.HD2CT_IsEnabled.restype = ctypes.c_uint32
lib.HD2CT_Submit.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
lib.HD2CT_Submit.restype = ctypes.c_uint32
lib.HD2CT_Poll.argtypes = [
    ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
]
lib.HD2CT_Poll.restype = ctypes.c_uint32

lib.fixture_ClearRegistry()
for hive_name, hive in (("user", 0), ("machine", 1)):
    for name, value in json.loads(registry_json).get(hive_name, {}).items():
        if not lib.fixture_SetRegistryValue(hive, name, value):
            raise RuntimeError("fixture registry value was rejected")

status = lib.HD2CT_InitializeEnvironment()
enabled = lib.HD2CT_IsEnabled()
submit_result = None
if not enabled:
    submit_result = lib.HD2CT_Submit(b"missing-config", b"safe", 4)
action_results = []
if enabled:
    for action in actions:
        if action["op"] == "submit":
            body = bytes.fromhex(action.get("body_hex", ""))
            backing = ctypes.create_string_buffer(body if body else b"\0", max(1, len(body)))
            accepted = lib.HD2CT_Submit(
                action["token"].encode("ascii"),
                ctypes.cast(backing, ctypes.c_void_p), len(body),
            )
            action_results.append({"accepted": accepted})
        elif action["op"] in ("poll", "wait"):
            token = action["token"].encode("ascii")
            value = None
            deadline = time.monotonic() + action.get("timeout", 8.0)
            while True:
                buffer = ctypes.create_string_buffer(16388)
                written = ctypes.c_uint32(0)
                found = lib.HD2CT_Poll(
                    token, ctypes.cast(buffer, ctypes.c_void_p), len(buffer),
                    ctypes.byref(written),
                )
                if found:
                    value = bytes(buffer.raw[:written.value]).decode("utf-8")
                    break
                if action["op"] == "poll" or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            action_results.append({"result": value})
        elif action["op"] == "normalize":
            output = ctypes.create_string_buffer(2049)
            accepted = lib.fixture_NormalizeUrl(
                action["adapter_id"], action["url"].encode("utf-8"),
                ctypes.cast(output, ctypes.c_void_p), len(output),
            )
            action_results.append({
                "accepted": accepted,
                "url": output.value.decode("utf-8"),
            })
        else:
            raise RuntimeError("unknown environment fixture action")
result = {
    "status": status,
    "enabled": enabled,
    "submit": submit_result,
    "timeout": lib.fixture_TimeoutSeconds(),
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

static LSTATUS WINAPI fixture_RegGetValueW(
    HKEY root, LPCWSTR subkey, LPCWSTR name, DWORD flags, LPDWORD type,
    PVOID data, LPDWORD bytes)
{
    DWORD hive;
    DWORD index;
    DWORD required;
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

#define RegGetValueW fixture_RegGetValueW
#include "hd2ct_http.c"
#undef RegGetValueW

__declspec(dllexport) void __cdecl fixture_ClearRegistry(void)
{
    SecureZeroMemory(fixture_values, sizeof(fixture_values));
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

__declspec(dllexport) uint32_t __cdecl fixture_TimeoutSeconds(void)
{
    return g_timeout_seconds;
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


def result_content(translation: str = "你好，绝地潜兵。", is_chinese: bool = False) -> str:
    return json.dumps(
        {"is_chinese": is_chinese, "translation": translation},
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
        self.delay = 0.0
        self.headers: list[dict[str, str]] = []
        self.request_finished = threading.Event()

    def clear(self) -> None:
        with self.lock:
            self.paths.clear()
            self.payloads.clear()
            self.content_types.clear()
            self.headers.clear()
            self.status = 200
            self.delay = 0.0
        self.request_finished.clear()


@unittest.skipUnless(WINDOWS, "原生 DLL 仅在 Windows 上运行")
class NativeHttpWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="hd2ct-http-tests-")
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
                    delay = state.delay
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
        timeout_seconds: int = 20,
        model: str = "fake-model",
    ) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                CHILD,
                str(self.dll),
                url if url is not None else self.url + "/",
                json.dumps(actions or [], ensure_ascii=False),
                str(timeout_seconds),
                model,
            ],
            cwd=ROOT,
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

    @classmethod
    def environment_test_dll(cls) -> Path:
        dll = cls.temp_path / "hd2ct_http_environment_test.dll"
        if dll.is_file():
            return dll
        wrapper = cls.temp_path / "hd2ct_http_environment_test.c"
        wrapper.write_text(ENVIRONMENT_SHIM_C, encoding="utf-8", newline="\n")
        native_root = ROOT / "native"
        cjson_source = native_root / "vendor" / "cjson" / "cJSON.c"
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
            "-DCJSON_NESTING_LIMIT=32",
            "-DCJSON_HIDE_SYMBOLS",
            "-Wl,--exclude-all-symbols",
            "-I",
            str(native_root),
            str(wrapper),
            str(cjson_source),
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
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stdout)
        return json.loads(lines[0])

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
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual([item.get("accepted") for item in result["actions"]],
                         [1, None, 1, None])
        self.assertEqual(result["actions"][1]["result"], "ERR\nUNSUPPORTED_SERVICE")
        self.assertEqual(result["actions"][3]["result"], "ERR\nUNSUPPORTED_SERVICE")
        self.assertEqual(result["cache_count"], 0)
        self.assertEqual(result["rate_count"], 0)
        self.assertEqual(self.state.paths, [])

        disabled = self.run_environment_child({"user": {"HD2CT_ENABLED": "0"}})
        self.assertEqual(disabled["status"], 3)
        self.assertEqual(disabled["enabled"], 0)

        invalid_option = self.run_environment_child(
            {"user": {"HD2CT_TIMEOUT_SECONDS": "invalid"}}
        )
        self.assertEqual(invalid_option["status"], 2)
        self.assertEqual(invalid_option["enabled"], 0)

        for missing_name in user_config:
            user = dict(user_config)
            machine = dict(machine_config)
            del user[missing_name]
            del machine[missing_name]
            with self.subTest(missing_registry_value=missing_name):
                result = self.run_environment_child({"user": user, "machine": machine})
                if missing_name == "HD2CT_MODEL":
                    self.assertEqual(result["status"], 0)
                    self.assertEqual(result["enabled"], 1)
                else:
                    self.assertEqual(result["status"], 1)
                    self.assertEqual(result["enabled"], 0)
                    self.assertEqual(result["submit"], 0)

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
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["enabled"], 1)
        self.assertEqual(result["selected_config_matches"], 1)

        invalid_user = dict(user_config)
        invalid_user["HD2CT_API_URL"] = "ftp://127.0.0.1:1/user"
        result = self.run_environment_child(
            {"user": invalid_user, "machine": machine_config}
        )
        self.assertEqual(result["status"], 2)
        self.assertEqual(result["enabled"], 0)
        self.assertEqual(result["submit"], 0)

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
        self.assertEqual(result["actions"][1]["result"], "MT\n你好，\n绝地潜兵。")
        self.assertEqual(result["actions"][3]["result"], "MT\n你好，\n绝地潜兵。")
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
        self.assertEqual(preserved["actions"][1]["result"], "MT\n不覆盖中文")

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
        self.assertEqual(preserved["actions"][1]["result"], "MT\n" + same_chinese)

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
        self.assertEqual(result["actions"][1]["result"], "MT\n你好，绝地潜兵。")
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
        self.assertEqual(preserved["actions"][1]["result"], "MT\n不覆盖中文")

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
        self.assertEqual(preserved["actions"][1]["result"], "MT\n" + same_chinese)

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
        self.assertEqual(google["actions"][1]["result"], "MT\nHello & 🙂")
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
        self.assertEqual(preserved["actions"][1]["result"], "MT\nignored result")

        self.state.clear()
        self.state.response = google_response(chinese, "zh-Hans")
        same_chinese = self.run_child(
            self.url + "/google/custom",
            [
                {"op": "submit", "token": "google-chinese-same",
                 "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "google-chinese-same"},
            ],
            model="",
        )
        self.assertEqual(same_chinese["actions"][1]["result"], "MT\n" + chinese)

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
        self.assertEqual(result["actions"][1]["result"], "MT\n你好")

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

        missing_signed_app_id = self.run_child(
            self.url + "/Baidu/missing-app-id",
            model="",
        )
        self.assertEqual(missing_signed_app_id["status"], 1)
        self.assertEqual(missing_signed_app_id["enabled"], 0)
        self.assert_environment_initialization_uses_registry()

    def test_english_translation_preserves_chinese_and_model_translation(self) -> None:
        self.state.response = provider_response(result_content("前往撤离点", is_chinese=False))
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
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["reasoning_effort"], "none")
        prompt = payload["messages"][0]["content"]
        for rule in (
            "中文原样返回",
            "is_chinese",
            "translation",
            "Charger=牛",
        ):
            with self.subTest(prompt_rule=rule):
                self.assertIn(rule, prompt)

        self.state.clear()
        self.state.response = provider_response(result_content("不应覆盖中文原文", is_chinese=True))
        chinese = "我们需要在 A1 补给。"
        preserved = self.run_child(
            actions=[
                {"op": "submit", "token": "chinese", "body_hex": chinese.encode("utf-8").hex()},
                {"op": "wait", "token": "chinese"},
            ],
        )
        self.assertEqual(preserved["actions"][1]["result"], "OK\n" + chinese)
        self.assertEqual(self.state.payloads[0]["messages"][1]["content"], chinese)

        self.state.clear()
        self.state.response = provider_response(result_content("打得不错", is_chinese=False))
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
        self.state.status = 401
        self.state.response = b"secret-provider-error-body"
        unauthorized = self.run_child(
            actions=[
                {"op": "submit", "token": "http401", "body_hex": "4869"},
                {"op": "wait", "token": "http401"},
            ],
        )
        self.assertEqual(unauthorized["actions"][1]["result"], "ERR\nHTTP_401")
        self.assertEqual(unauthorized["final_last_status"], 401)
        self.assertNotIn("secret-provider-error-body", json.dumps(unauthorized))

        self.state.clear()
        self.state.status = 200
        self.state.response = provider_response('{"is_chinese":"yes","translation":"ignored"}')
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
        self.state.delay = 1.5
        timed_out = self.run_child(
            actions=[
                {"op": "submit", "token": "timeout-one", "body_hex": "6869"},
                {"op": "wait", "token": "timeout-one", "timeout": 3.0},
            ],
            timeout=5.0,
            timeout_seconds=1,
        )
        self.assertEqual(timed_out["actions"][1]["result"], "ERR\nTIMEOUT")

if __name__ == "__main__":
    unittest.main()
