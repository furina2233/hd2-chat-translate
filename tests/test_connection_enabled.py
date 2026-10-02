"""连接测试在正式聊天翻译未启用时的隔离验证。"""

from __future__ import annotations

import json
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from hd2_translate.config import AppConfig, ConfigError
from hd2_translate.translator import (
    RequestLimiter,
    TEST_SAMPLE,
    TranslationError,
    Translator,
    normalize_endpoint,
)


def _make_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            state["requests"].append(
                {
                    "authorization": self.headers.get("Authorization"),
                    "path": self.path,
                    "payload": payload,
                }
            )
            delay = float(state.get("delay", 0))
            if delay:
                time.sleep(delay)
            status = int(state.get("status", 200))
            if status == 200:
                answer = {
                    "is_chinese": False,
                    "translation": f"固定示例译文{len(state['requests'])}",
                }
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(answer, ensure_ascii=False)},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
            else:
                body = state.get("error_body", b"private provider error body")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


class ConnectionTestEnabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state: dict[str, Any] = {"requests": [], "delay": 0, "status": 200}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.state))
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def _config(self, **changes: Any) -> AppConfig:
        values: dict[str, Any] = {
            "url": self.url,
            "model": "local-test-model",
            "api_key": "fake-local-test-key",
            "timeout": 2.0,
            "enabled": False,
        }
        values.update(changes)
        return AppConfig(**values)

    def test_disabled_service_can_test_twice_without_changing_runtime_state(self) -> None:
        config = self._config()
        translator = Translator(config)
        cache_before = dict(translator._cache)
        failures_before = translator._failure_count
        backoff_before = translator._backoff_until

        self.assertEqual(translator.test_connection(), "固定示例译文1")
        self.assertEqual(translator.test_connection(), "固定示例译文2")

        self.assertEqual(len(self.state["requests"]), 2)
        self.assertEqual(
            [request["payload"]["messages"][1]["content"] for request in self.state["requests"]],
            [TEST_SAMPLE, TEST_SAMPLE],
        )
        self.assertTrue(
            all(request["payload"]["model"] == "local-test-model" for request in self.state["requests"])
        )
        self.assertTrue(
            all(request["authorization"] == "Bearer fake-local-test-key" for request in self.state["requests"])
        )
        self.assertEqual(translator.config, config)
        self.assertFalse(translator.config.enabled)
        self.assertEqual(translator._cache, cache_before)
        self.assertEqual(translator._failure_count, failures_before)
        self.assertEqual(translator._backoff_until, backoff_before)
        with self.assertRaisesRegex(TranslationError, "翻译服务未启用"):
            translator.translate("ordinary chat")
        self.assertEqual(len(self.state["requests"]), 2)

    def test_incomplete_configuration_has_clear_errors_without_http(self) -> None:
        cases = (
            ({"api_key": ""}, "请填写 API 密钥"),
            ({"url": ""}, "请填写完整 API URL"),
            ({"model": "  "}, "请填写模型名称"),
        )
        for changes, message in cases:
            with self.subTest(message=message):
                translator = Translator(self._config(**changes))
                with self.assertRaisesRegex(TranslationError, message):
                    translator.test_connection()
        self.assertEqual(self.state["requests"], [])

    def test_config_change_during_request_discards_test_result(self) -> None:
        self.state["delay"] = 0.2
        original = self._config()
        translator = Translator(original)
        results: list[str] = []
        errors: list[Exception] = []

        def test_connection() -> None:
            try:
                results.append(translator.test_connection())
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=test_connection)
        worker.start()
        deadline = time.monotonic() + 2
        while not self.state["requests"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.state["requests"], "连接测试没有向本机模拟服务发送请求")
        translator.configure(replace(original, model="updated-test-model"))
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "配置已更改")
        self.assertEqual(translator.config.model, "updated-test-model")
        self.assertEqual(translator._cache, {})

    def test_connection_test_uses_the_shared_rate_limiter(self) -> None:
        limiter = RequestLimiter(limit=1, period=60)
        enabled = replace(self._config(), enabled=True)
        translator = Translator(enabled, limiter=limiter)
        self.assertEqual(translator.translate("ordinary chat"), "固定示例译文1")
        translator.configure(replace(enabled, enabled=False))

        with self.assertRaisesRegex(TranslationError, "频率已达上限"):
            translator.test_connection()
        self.assertEqual(len(self.state["requests"]), 1)
        self.assertFalse(translator.config.enabled)

    def test_base_and_v1_urls_normalize_to_the_actual_local_request_path(self) -> None:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        cases = (
            ("", f"{origin}/chat/completions", "/chat/completions"),
            ("/", f"{origin}/chat/completions", "/chat/completions"),
            ("/v1", f"{origin}/v1/chat/completions", "/v1/chat/completions"),
            ("/v1/", f"{origin}/v1/chat/completions", "/v1/chat/completions"),
        )
        for suffix, expected_url, expected_path in cases:
            with self.subTest(suffix=suffix):
                translator = Translator(
                    self._config(url=origin + suffix),
                    limiter=RequestLimiter(limit=10),
                )
                self.assertEqual(translator.config.url, expected_url)
                translator.test_connection()
                self.assertEqual(self.state["requests"][-1]["path"], expected_path)

        configured = Translator(self._config(url=origin), limiter=RequestLimiter(limit=10))
        configured.configure(self._config(url=origin + "/v1/"))
        self.assertEqual(configured.config.url, f"{origin}/v1/chat/completions")

    def test_complete_custom_paths_are_preserved_and_invalid_urls_are_rejected(self) -> None:
        origin = f"http://127.0.0.1:{self.server.server_port}"
        for path in ("/v1/chat/completions", "/custom/chat/translate"):
            with self.subTest(path=path):
                endpoint = origin + path
                self.assertEqual(normalize_endpoint(endpoint), endpoint)
                translator = Translator(self._config(url=endpoint))
                self.assertEqual(translator.config.url, endpoint)

        invalid_urls = (
            "http://api.example.test/",
            "https://user:secret@example.test/",
            "https://api.example.test/?token=secret",
            "https://api.example.test/#fragment",
        )
        for endpoint in invalid_urls:
            with self.subTest(endpoint=endpoint), self.assertRaises(ConfigError):
                normalize_endpoint(endpoint)

    def test_http_status_hints_do_not_expose_key_or_provider_body(self) -> None:
        secret = "fake-local-test-key"
        private_body = f"private provider text containing {secret}".encode("utf-8")
        cases = (
            (401, "API 密钥或接口权限"),
            (404, "请求地址或模型名称"),
        )
        for status, hint in cases:
            with self.subTest(status=status):
                self.state["status"] = status
                self.state["error_body"] = private_body
                translator = Translator(self._config(), limiter=RequestLimiter(limit=5))
                with self.assertRaises(TranslationError) as caught:
                    translator.test_connection()
                message = str(caught.exception)
                self.assertIn("翻译服务暂不可用", message)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(hint, message)
                self.assertNotIn(secret, message)
                self.assertNotIn(private_body.decode("utf-8"), message)


if __name__ == "__main__":
    unittest.main()
