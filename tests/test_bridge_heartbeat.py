"""服务到游戏桥接心跳的临时邮箱测试。"""

from __future__ import annotations

import re
import tempfile
import time
import unittest
from pathlib import Path

from hd2_translate.config import AppConfig
from hd2_translate.service import CompanionService, ServiceAlreadyRunningError


class _FakeTranslator:
    """仅提供启用状态，不会执行任何网络请求。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def configure(self, config: AppConfig) -> None:
        self.config = config

    def translate(self, source: str, **kwargs: object) -> str:
        raise AssertionError("心跳测试不应调用翻译")


class BridgeHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.mailbox = Path(self.temp.name) / "mailbox"
        self.services: list[CompanionService] = []

    def tearDown(self) -> None:
        for service in self.services:
            service.stop()
            self._wait_stopped(service)
        self.temp.cleanup()

    def _service(self, enabled: bool, poll_interval: float = 0.02) -> CompanionService:
        config = AppConfig(enabled=enabled)
        service = CompanionService(
            config,
            mailbox=self.mailbox,
            poll_interval=poll_interval,
            translator=_FakeTranslator(config),  # type: ignore[arg-type]
        )
        self.services.append(service)
        return service

    def _wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def _wait_stopped(self, service: CompanionService, timeout: float = 3.0) -> None:
        def idle() -> bool:
            threads = [thread for thread in [service._poll_thread, *service._workers] if thread]
            with service._lock:
                return all(not thread.is_alive() for thread in threads) and service._mailbox_lock is None

        self.assertTrue(self._wait_for(idle, timeout), "服务线程或邮箱锁未及时释放")

    def test_enabled_service_publishes_ascii_uptime(self) -> None:
        service = self._service(enabled=True)
        service.start()
        flag = self.mailbox / "bridge.flag"

        self.assertTrue(self._wait_for(flag.exists))
        raw = flag.read_bytes()
        self.assertLessEqual(len(raw), 64)
        match = re.fullmatch(rb"HD2CT1 ([0-9]+)\n", raw)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(int(match.group(1)), 0)

    def test_disabled_service_does_not_publish_flag(self) -> None:
        service = self._service(enabled=False)
        service.start()
        time.sleep(0.08)
        self.assertFalse((self.mailbox / "bridge.flag").exists())

    def test_disable_and_enable_configuration_updates_flag(self) -> None:
        service = self._service(enabled=True)
        service.start()
        flag = self.mailbox / "bridge.flag"
        self.assertTrue(self._wait_for(flag.exists))

        service.configure(AppConfig(enabled=False))
        self.assertFalse(flag.exists(), "禁用配置应同步移除本实例心跳")

        service.configure(AppConfig(enabled=True))
        self.assertTrue(self._wait_for(flag.exists))

        # GUI 可直接配置共享 Translator；轮询器也须观察到这种状态变化。
        service.translator.configure(AppConfig(enabled=False))
        self.assertTrue(self._wait_for(lambda: not flag.exists()))

    def test_stop_immediately_removes_owned_flag(self) -> None:
        service = self._service(enabled=True)
        service.start()
        flag = self.mailbox / "bridge.flag"
        self.assertTrue(self._wait_for(flag.exists))

        service.stop()
        self.assertFalse(flag.exists(), "stop 返回时本实例心跳应已移除")

    def test_unstarted_or_lock_conflicted_service_preserves_existing_flag(self) -> None:
        self.mailbox.mkdir(parents=True)
        flag = self.mailbox / "bridge.flag"
        sentinel = b"external-owner\n"
        flag.write_bytes(sentinel)

        unstarted = self._service(enabled=True)
        unstarted.stop()
        self.assertEqual(flag.read_bytes(), sentinel)

        holder = self._service(enabled=False)
        holder.start()
        blocked = self._service(enabled=True)
        with self.assertRaises(ServiceAlreadyRunningError):
            blocked.start()
        self.assertEqual(flag.read_bytes(), sentinel)
        blocked.stop()
        self.assertEqual(flag.read_bytes(), sentinel)

    def test_old_generation_cleanup_cannot_remove_new_generation_flag(self) -> None:
        service = self._service(enabled=True)
        service.start()
        flag = self.mailbox / "bridge.flag"
        self.assertTrue(self._wait_for(flag.exists))
        old_event = service._stop_event
        assert old_event is not None

        service.stop()
        self.assertFalse(flag.exists())
        self._wait_stopped(service)

        service.start()
        self.assertTrue(self._wait_for(flag.exists))
        new_event = service._stop_event
        assert new_event is not None
        self.assertIsNot(new_event, old_event)

        with service._lock:
            before = flag.read_bytes()
            service._remove_bridge_flag_locked(old_event)
            self.assertEqual(flag.read_bytes(), before)
            self.assertIs(service._bridge_flag_event, new_event)


if __name__ == "__main__":
    unittest.main()
