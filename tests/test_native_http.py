"""原生 HTTP worker 的本机回环验证，不读取真实配置或调用付费接口。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
    url.encode("utf-8"), b"fake-model", b"fake-api-key", timeout_seconds,
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


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.payloads: list[dict] = []
        self.status = 200
        self.response = provider_response(result_content())
        self.delay = 0.0
        self.headers: list[dict[str, str]] = []
        self.request_finished = threading.Event()

    def clear(self) -> None:
        with self.lock:
            self.paths.clear()
            self.payloads.clear()
            self.headers.clear()
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
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                with state.lock:
                    state.paths.append(self.path)
                    state.payloads.append(payload)
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

    def test_english_translation_preserves_chinese_and_gg_source(self) -> None:
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
        prompt = payload["messages"][0]["content"]
        for rule in (
            "整条消息去掉首尾空白后仅为 gg 或 ggs",
            "is_chinese",
            "translation",
            "Charger -> 牛（默认，口语可用牛牛）",
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
        raw_gg = "\tGgS \r\n"
        unchanged_gg = self.run_child(
            actions=[
                {"op": "submit", "token": "gg", "body_hex": raw_gg.encode("utf-8").hex()},
                {"op": "wait", "token": "gg"},
            ],
        )
        self.assertEqual(unchanged_gg["actions"][1]["result"], "OK\n" + raw_gg)
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

if __name__ == "__main__":
    unittest.main()
