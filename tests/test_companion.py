"""翻译 HTTP 客户端、设置存储与游戏邮箱端到端测试。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hd2_translate.config import AppConfig, ConfigError, load_config, save_config
from hd2_translate.service import CompanionService, ServiceAlreadyRunningError
from hd2_translate.translator import RequestLimiter, TranslationError, Translator, validate_config, validate_endpoint


def _provider_response(answer: dict[str, Any], finish_reason: str = "stop") -> bytes:
    content = json.dumps(answer, ensure_ascii=False)
    body = {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ]
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _make_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            state["count"] += 1
            state["authorization"] = self.headers.get("Authorization")
            state["payload"] = json.loads(raw.decode("utf-8"))
            if self.path == "/target":
                state["target_count"] += 1
            if state.get("redirect"):
                self.send_response(302)
                self.send_header("Location", state["redirect"])
                self.end_headers()
                return
            delay = float(state.get("delay", 0))
            if delay:
                time.sleep(delay)
            self.send_response(int(state.get("status", 200)))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(state.get("body", b"{}"))
            except OSError:
                pass

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


class CompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state: dict[str, Any] = {
            "count": 0,
            "target_count": 0,
            "status": 200,
            "delay": 0,
            "body": _provider_response({"is_chinese": False, "translation": "默认译文"}),
        }
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
            "model": "test-model",
            "api_key": "test-api-key-private",
            "timeout": 1.0,
            "enabled": True,
        }
        values.update(changes)
        return AppConfig(**values)

    def _wait_for_file(self, path: Path, timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.02)
        return path.exists()

    def _wait_service_threads(self, service: CompanionService, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            threads = [thread for thread in [service._poll_thread, *service._workers] if thread]
            if all(not thread.is_alive() for thread in threads) and service._mailbox_lock is None:
                return
            time.sleep(0.02)
        self.fail("后台服务线程未及时退出")

    def test_config_validation_restricts_endpoint_and_required_values(self) -> None:
        validate_config(AppConfig())
        validate_endpoint("https://api.example.test/v1/chat/completions")
        validate_endpoint("http://localhost:1234/v1/chat/completions")
        for url in (
            "http://example.test/v1/chat/completions",
            "https://user:pass@example.test/v1/chat/completions",
            "https://api.example.test/v1/chat/completions?key=secret",
            "https://api.example.test/v1/chat/completions#fragment",
            "https://api.example.test/v1/chat/completions?",
            "https://api.example.test/v1/chat/completions#",
        ):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate_endpoint(url)
        with self.assertRaises(ConfigError):
            validate_config(self._config(enabled=False, timeout=121))
        with self.assertRaises(ConfigError):
            validate_config(self._config(api_key="bad\nkey"))
        with self.assertRaises(ConfigError):
            validate_config(AppConfig(enabled=True))

    def test_chinese_input_is_sent_for_detection_and_preserved_exactly(self) -> None:
        self.state["body"] = _provider_response({"is_chinese": True, "translation": "模型改写"})
        source = "  中文队友消息：坐标 A1。  "
        translator = Translator(self._config())
        self.assertEqual(translator.translate(source), source)
        self.assertEqual(self.state["count"], 1)
        request = self.state["payload"]
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertIn("绝地潜兵2", request["messages"][0]["content"])
        self.assertEqual(request["messages"][1], {"role": "user", "content": source})
        self.assertEqual(request["response_format"], {"type": "json_object"})

    def test_foreign_input_returns_model_translation(self) -> None:
        self.state["body"] = _provider_response({"is_chinese": False, "translation": "请在 A1 集合，等待增援。"})
        translator = Translator(self._config())
        self.assertEqual(translator.translate("Regroup at A1 and wait for reinforcements."), "请在 A1 集合，等待增援。")

    def test_connection_test_uses_fixed_sample_and_always_checks_endpoint(self) -> None:
        translator = Translator(self._config())
        self.assertEqual(translator.test_connection(), "默认译文")
        self.assertEqual(translator.test_connection(), "默认译文")
        self.assertEqual(self.state["count"], 2)
        sent = self.state["payload"]["messages"][1]["content"]
        self.assertEqual(sent, "Hello, Helldiver. Please regroup at the extraction point.")

    def test_rate_limiter_rejects_excess_without_waiting(self) -> None:
        translator = Translator(self._config(), limiter=RequestLimiter(limit=1, period=60))
        translator.translate("first request")
        with self.assertRaisesRegex(TranslationError, "频率已达上限"):
            translator.translate("second request")
        self.assertEqual(self.state["count"], 1)

    def test_bad_json_and_truncated_completion_are_rejected(self) -> None:
        self.state["body"] = _provider_response({"is_chinese": False, "translation": ""})
        self.state["body"] = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}
        ).encode("utf-8")
        with self.assertRaisesRegex(TranslationError, "响应格式无效"):
            Translator(self._config()).translate("Hello")
        self.state["body"] = _provider_response(
            {"is_chinese": False, "translation": "部分内容"}, finish_reason="length"
        )
        with self.assertRaisesRegex(TranslationError, "响应格式无效"):
            Translator(self._config()).translate("Hello")

    def test_timeout_is_reported_without_provider_details(self) -> None:
        self.state["delay"] = 1.2
        translator = Translator(self._config(timeout=1.0))
        with self.assertRaisesRegex(TranslationError, "暂不可用"):
            translator.translate("Hello")

    def test_key_is_never_exposed_in_errors_or_saved_as_plaintext(self) -> None:
        secret = "test-api-key-private"
        self.state["status"] = 401
        self.state["body"] = f"provider echoed {secret}".encode("utf-8")
        config = self._config(api_key=secret)
        with self.assertRaises(TranslationError) as caught:
            Translator(config).translate("Hello")
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(config))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(config, path)
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, saved)
            loaded = load_config(path)
            if os.name == "nt":
                self.assertEqual(loaded.api_key, secret)
            else:
                self.assertEqual(loaded.api_key, "")

    def test_redirect_is_not_followed_with_authorization_header(self) -> None:
        self.state["redirect"] = f"http://127.0.0.1:{self.server.server_port}/target"
        translator = Translator(self._config())
        with self.assertRaises(TranslationError) as caught:
            translator.translate("Hello")
        self.assertNotIn("test-api-key-private", str(caught.exception))
        self.assertEqual(self.state["target_count"], 0)

    def test_cache_is_cleared_when_config_changes_and_disabled_cache_cannot_bypass(self) -> None:
        translator = Translator(self._config())
        self.assertEqual(translator.translate("Hello"), "默认译文")
        self.state["body"] = _provider_response({"is_chinese": False, "translation": "新译文"})
        translator.configure(replace(self._config(), model="second-model"))
        self.assertEqual(translator.translate("Hello"), "新译文")
        self.assertEqual(self.state["count"], 2)
        translator.configure(replace(translator.config, enabled=False))
        with self.assertRaisesRegex(TranslationError, "翻译服务未启用"):
            translator.translate("Hello")

    def test_old_generation_result_is_not_cached(self) -> None:
        self.state["delay"] = 0.2
        translator = Translator(self._config())
        errors: list[Exception] = []

        def translate() -> None:
            try:
                translator.translate("Hello")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=translate)
        thread.start()
        deadline = time.monotonic() + 2
        while self.state["count"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        translator.configure(replace(self._config(), model="changed-model"))
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertIn("配置已更改", str(errors[0]))
        self.assertNotIn("Hello", translator._cache)

    def test_mailbox_end_to_end_and_expired_request_cleanup(self) -> None:
        self.state["body"] = _provider_response({"is_chinese": False, "translation": "加入战斗！"})
        with tempfile.TemporaryDirectory() as directory:
            mailbox = Path(directory)
            service = CompanionService(self._config(), mailbox=mailbox, poll_interval=0.02)
            service.start()
            try:
                (mailbox / "message_001.req").write_text("Join the fight!", encoding="utf-8")
                response = mailbox / "message_001.res"
                self.assertTrue(self._wait_for_file(response))
                self.assertEqual(response.read_text(encoding="utf-8"), "OK\n加入战斗！")
                self.assertFalse((mailbox / "message_001.req").exists())
                self.assertFalse((mailbox / "message_001.processing").exists())
                self.assertEqual(self.state["count"], 1)

                old_request = mailbox / "expired.req"
                old_request.write_text("Do not send this", encoding="utf-8")
                expired_at = time.time() - 61
                os.utime(old_request, (expired_at, expired_at))
                old_response = mailbox / "expired.res"
                self.assertTrue(self._wait_for_file(old_response))
                self.assertEqual(old_response.read_text(encoding="utf-8"), "ERR\n请求已过期")
                self.assertEqual(self.state["count"], 1)
            finally:
                service.stop()
                self._wait_service_threads(service)

    def test_missing_key_preserves_original_and_orphaned_request_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mailbox = Path(directory)
            service = CompanionService(AppConfig(), mailbox=mailbox, poll_interval=0.02)
            service.start()
            try:
                (mailbox / "no_key.req").write_text("Original chat", encoding="utf-8")
                response = mailbox / "no_key.res"
                self.assertTrue(self._wait_for_file(response))
                self.assertEqual(response.read_text(encoding="utf-8"), "ERR\n翻译服务未启用")
                self.assertEqual(self.state["count"], 0)

                (mailbox / "crashed.processing").write_text("Already submitted", encoding="utf-8")
                recovered = mailbox / "crashed.res"
                self.assertTrue(self._wait_for_file(recovered))
                self.assertEqual(recovered.read_text(encoding="utf-8"), "ERR\n上次处理未完成")
                self.assertFalse((mailbox / "crashed.processing").exists())
                self.assertEqual(self.state["count"], 0)
            finally:
                service.stop()
                self._wait_service_threads(service)

    def test_old_responses_are_cleaned_after_five_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mailbox = Path(directory)
            old_response = mailbox / "old.res"
            old_response.write_text("OK\nold", encoding="utf-8")
            expired_at = time.time() - 301
            os.utime(old_response, (expired_at, expired_at))
            service = CompanionService(AppConfig(), mailbox=mailbox, poll_interval=0.02)
            service.start()
            try:
                deadline = time.monotonic() + 2
                while old_response.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(old_response.exists())
            finally:
                service.stop()
                self._wait_service_threads(service)

    def test_restart_does_not_duplicate_inflight_request_or_worker(self) -> None:
        self.state["delay"] = 0.45
        with tempfile.TemporaryDirectory() as directory:
            mailbox = Path(directory)
            service = CompanionService(self._config(), mailbox=mailbox, poll_interval=0.02)
            service.start()
            try:
                (mailbox / "single.req").write_text("Wait here", encoding="utf-8")
                deadline = time.monotonic() + 2
                while self.state["count"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.state["count"], 1)
                service.stop()
                service.start()
                self.assertIn("等待上次请求结束", service.status)
                response = mailbox / "single.res"
                self.assertTrue(self._wait_for_file(response))
                deadline = time.monotonic() + 3
                while not service.running and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(service.running)
                time.sleep(0.15)
                self.assertEqual(self.state["count"], 1)
                self.assertFalse((mailbox / "single.processing").exists())
            finally:
                service.stop()
                self._wait_service_threads(service)

    def test_same_mailbox_rejects_second_instance_until_stopped_request_finishes(self) -> None:
        self.state["delay"] = 0.45
        with tempfile.TemporaryDirectory() as directory:
            mailbox = Path(directory) / "shared"
            other_mailbox = Path(directory) / "other"
            first = CompanionService(self._config(), mailbox=mailbox, poll_interval=0.02)
            second = CompanionService(self._config(), mailbox=mailbox, poll_interval=0.02)
            independent = CompanionService(self._config(), mailbox=other_mailbox, poll_interval=0.02)
            first.start()
            try:
                with self.assertRaises(ServiceAlreadyRunningError):
                    second.start()
                independent.start()
                self.assertTrue(independent.running)
                independent.stop()
                self._wait_service_threads(independent)

                (mailbox / "locked.req").write_text("Hold this request", encoding="utf-8")
                deadline = time.monotonic() + 2
                while self.state["count"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.state["count"], 1)
                first.stop()
                with self.assertRaises(ServiceAlreadyRunningError):
                    second.start()
                response = mailbox / "locked.res"
                self.assertTrue(self._wait_for_file(response))
                self._wait_service_threads(first)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        second.start()
                        break
                    except ServiceAlreadyRunningError:
                        time.sleep(0.02)
                self.assertTrue(second.running)
                self.assertEqual(self.state["count"], 1)
            finally:
                first.stop()
                second.stop()
                self._wait_service_threads(first)
                self._wait_service_threads(second)


if __name__ == "__main__":
    unittest.main()
