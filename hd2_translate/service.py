"""游戏文件邮箱轮询器。"""

from __future__ import annotations

import ctypes
import hashlib
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from .config import AppConfig, mailbox_path
from .translator import TranslationError, Translator


REQUEST_TTL_SECONDS = 60
RESPONSE_TTL_SECONDS = 300
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_SOURCE_BYTES = 8_192


class CompanionService:
    """从 .req 文件读取聊天并写入原子 .res 响应。"""

    def __init__(
        self,
        config: AppConfig,
        mailbox: Path | None = None,
        poll_interval: float = 0.15,
        worker_count: int = 2,
        queue_size: int = 32,
        translator: Translator | None = None,
    ) -> None:
        self.mailbox = mailbox or mailbox_path()
        self.poll_interval = max(0.02, poll_interval)
        self.worker_count = worker_count
        self._queue: queue.Queue[tuple[str, Path, float]] = queue.Queue(maxsize=queue_size)
        self.translator = translator or Translator(config)
        self._lock = threading.RLock()
        self._response_lock = threading.Lock()
        self._queued: set[str] = set()
        self._active: set[str] = set()
        self._stop_event: threading.Event | None = None
        self._poll_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._mailbox_lock: _MailboxLock | None = None
        self._restart_pending = False
        self._processed = 0
        self._failed = 0

    @property
    def running(self) -> bool:
        with self._lock:
            if self._restart_pending:
                return False
            return self._stop_event is not None and not self._stop_event.is_set()

    @property
    def status(self) -> str:
        with self._lock:
            if self._restart_pending:
                state = "等待上次请求结束"
            elif self._stop_event is None or self._stop_event.is_set():
                state = "已停止"
            else:
                state = "运行中"
            return f"{state} · 已处理 {self._processed} 条 · 失败 {self._failed} 条"

    def configure(self, config: AppConfig) -> None:
        self.translator.configure(config)

    def start(self) -> None:
        with self._lock:
            if self._stop_event is not None and not self._stop_event.is_set():
                return
            if self._restart_pending:
                return
            previous_threads = [thread for thread in [self._poll_thread, *self._workers] if thread and thread.is_alive()]
            if previous_threads:
                self._restart_pending = True
                threading.Thread(
                    target=self._restart_when_idle,
                    args=(previous_threads,),
                    name="hd2-translate-restart-waiter",
                    daemon=True,
                ).start()
                return
            self._start_locked()

    def _start_locked(self) -> None:
        self.mailbox.mkdir(parents=True, exist_ok=True)
        if self._mailbox_lock is None:
            self._mailbox_lock = _MailboxLock(self.mailbox)
        # 清除上次停止时留在内存队列中的引用；邮箱文件仍会重新扫描。
        self._drain_queue()
        self._queued.clear()
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._workers = [
            threading.Thread(
                target=self._worker,
                args=(stop_event,),
                name=f"hd2-translate-worker-{index + 1}",
                daemon=True,
            )
            for index in range(self.worker_count)
        ]
        self._poll_thread = threading.Thread(
            target=self._poller,
            args=(stop_event,),
            name="hd2-translate-poller",
            daemon=True,
        )
        for thread in self._workers:
            thread.start()
        self._poll_thread.start()

    def _restart_when_idle(self, previous_threads: list[threading.Thread]) -> None:
        for thread in previous_threads:
            while thread.is_alive():
                thread.join(timeout=0.2)
        with self._lock:
            if not self._restart_pending or self._stop_event is None or not self._stop_event.is_set():
                return
            self._restart_pending = False
            self._start_locked()

    def _release_when_idle(
        self,
        previous_threads: list[threading.Thread],
        stopped_event: threading.Event,
    ) -> None:
        for thread in previous_threads:
            while thread.is_alive():
                thread.join(timeout=0.2)
        with self._lock:
            if self._stop_event is stopped_event and not self._restart_pending:
                self._release_mailbox_lock()

    def _release_mailbox_lock(self) -> None:
        if self._mailbox_lock is not None:
            self._mailbox_lock.release()
            self._mailbox_lock = None

    def stop(self) -> None:
        """仅发出停止信号，不等待网络线程，避免卡住 GUI。"""
        with self._lock:
            self._restart_pending = False
            if self._stop_event is not None:
                stopped_event = self._stop_event
                stopped_event.set()
                previous_threads = [
                    thread
                    for thread in [self._poll_thread, *self._workers]
                    if thread is not None and thread.is_alive()
                ]
                if previous_threads:
                    threading.Thread(
                        target=self._release_when_idle,
                        args=(previous_threads, stopped_event),
                        name="hd2-translate-lock-release",
                        daemon=True,
                    ).start()
                else:
                    self._release_mailbox_lock()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                return

    def _poller(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._scan_mailbox(stop_event)
            except OSError:
                # 临时文件系统错误在后续轮询中恢复，不显示底层路径或内容。
                pass
            stop_event.wait(self.poll_interval)

    def _scan_mailbox(self, stop_event: threading.Event) -> None:
        now = time.time()
        for response in self.mailbox.glob("*.res"):
            if stop_event.is_set():
                return
            try:
                if now - response.stat().st_mtime > RESPONSE_TTL_SECONDS:
                    response.unlink(missing_ok=True)
            except OSError:
                pass
        for temporary in self.mailbox.glob("*.res.*.tmp"):
            try:
                if now - temporary.stat().st_mtime > RESPONSE_TTL_SECONDS:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._recover_processing_files()
        for request in self.mailbox.glob("*.req"):
            if stop_event.is_set():
                return
            token = request.stem
            if not _TOKEN_PATTERN.fullmatch(token):
                continue
            response = self.mailbox / f"{token}.res"
            if response.exists():
                request.unlink(missing_ok=True)
                continue
            try:
                deadline = request.stat().st_mtime + REQUEST_TTL_SECONDS
            except OSError:
                continue
            if now >= deadline:
                self._write_response(token, "ERR\n请求已过期")
                request.unlink(missing_ok=True)
                continue
            with self._lock:
                if token in self._queued or token in self._active:
                    continue
                self._queued.add(token)
            try:
                self._queue.put_nowait((token, request, deadline))
            except queue.Full:
                with self._lock:
                    self._queued.discard(token)

    def _recover_processing_files(self) -> None:
        for processing in self.mailbox.glob("*.processing"):
            token = processing.stem
            if not _TOKEN_PATTERN.fullmatch(token):
                continue
            with self._lock:
                if token in self._active:
                    continue
            self._write_response(token, "ERR\n上次处理未完成")
            processing.unlink(missing_ok=True)

    def _worker(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                token, request, deadline = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                if token in self._active:
                    self._queue.task_done()
                    continue
                self._queued.discard(token)
                self._active.add(token)
            try:
                if stop_event.is_set():
                    continue
                self._process_request(token, request, deadline, stop_event)
            except OSError:
                with self._lock:
                    self._failed += 1
            except Exception:
                # 隐藏异常正文，保证单个邮箱条目不会终止后台 worker。
                with self._lock:
                    self._failed += 1
            finally:
                with self._lock:
                    self._active.discard(token)
                self._queue.task_done()

    def _process_request(
        self,
        token: str,
        request: Path,
        deadline: float,
        stop_event: threading.Event,
    ) -> None:
        if not request.exists() or stop_event.is_set():
            return
        if (self.mailbox / f"{token}.res").exists():
            request.unlink(missing_ok=True)
            return
        if time.time() >= deadline:
            self._write_response(token, "ERR\n请求已过期")
            request.unlink(missing_ok=True)
            return
        processing = self.mailbox / f"{token}.processing"
        try:
            os.replace(request, processing)
        except OSError:
            return
        result: str
        try:
            with processing.open("rb") as stream:
                raw = stream.read(_MAX_SOURCE_BYTES + 1)
            if len(raw) > _MAX_SOURCE_BYTES:
                raise TranslationError("聊天内容超过长度限制")
            try:
                source = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise TranslationError("聊天内容不是有效的 UTF-8") from None
            translated = self.translator.translate(source, stop_event=stop_event, deadline=deadline)
            result = f"OK\n{translated}"
            with self._lock:
                self._processed += 1
        except TranslationError as exc:
            result = f"ERR\n{exc}"
            with self._lock:
                self._failed += 1
        except OSError:
            result = "ERR\n读取请求失败"
            with self._lock:
                self._failed += 1
        self._write_response(token, result)
        processing.unlink(missing_ok=True)

    def _write_response(self, token: str, content: str) -> None:
        response = self.mailbox / f"{token}.res"
        temp = self.mailbox / f"{token}.res.{uuid.uuid4().hex}.tmp"
        with self._response_lock:
            if response.exists():
                return
            try:
                temp.write_text(content, encoding="utf-8", newline="")
                os.replace(temp, response)
            finally:
                temp.unlink(missing_ok=True)


class ServiceAlreadyRunningError(RuntimeError):
    """同一个邮箱已由另一个翻译服务实例占用。"""


class _MailboxLock:
    """跨进程独占邮箱，避免多个服务实例处理同一请求。"""

    def __init__(self, mailbox: Path) -> None:
        identity = hashlib.sha256(os.path.normcase(str(mailbox.resolve())).encode("utf-8")).hexdigest()
        self._handle: int | None = None
        self._fd: int | None = None
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.CreateMutexW(None, False, f"Local\\HD2ChatTranslate-{identity}")
            if not handle:
                raise OSError("无法锁定翻译邮箱")
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                raise ServiceAlreadyRunningError("该邮箱已有另一个翻译服务运行")
            self._handle = handle
            self._kernel32 = kernel32
            return
        import fcntl

        lock_path = mailbox / ".service.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ServiceAlreadyRunningError("该邮箱已有另一个翻译服务运行") from None
        self._fd = fd
        self._fcntl = fcntl

    def release(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._fd is not None:
            self._fcntl.flock(self._fd, self._fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
