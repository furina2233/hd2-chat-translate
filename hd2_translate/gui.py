"""tkinter 设置窗口与后台服务控制。"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .config import AppConfig, ConfigError, load_config, save_config
from .service import CompanionService, ServiceAlreadyRunningError
from .translator import TranslationError, Translator, normalize_endpoint, validate_config


class CompanionWindow:
    """提供服务商设置、连接测试与邮箱服务控制。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("HD2 聊天翻译")
        self.root.geometry("690x500")
        self.root.minsize(620, 450)
        self._load_notice = ""
        try:
            self.config = load_config()
            validate_config(self.config)
        except ConfigError:
            self.config = AppConfig()
            self._load_notice = "已忽略无效设置，请重新填写并保存。"
        self.translator = Translator(self.config)
        self.service = CompanionService(self.config, translator=self.translator)
        self._feedback_queue: queue.Queue[tuple[str, bool]] = queue.Queue()
        self._closed = False
        self._after_id: str | None = None
        self._build_widgets()
        self._refresh_status()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="大模型 API URL").grid(row=0, column=0, sticky="w", pady=6)
        self.url_var = tk.StringVar(value=self.config.url)
        ttk.Entry(outer, textvariable=self.url_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(outer, text="模型名称").grid(row=1, column=0, sticky="w", pady=6)
        self.model_var = tk.StringVar(value=self.config.model)
        ttk.Entry(outer, textvariable=self.model_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(outer, text="API 密钥").grid(row=2, column=0, sticky="w", pady=6)
        self.key_var = tk.StringVar()
        ttk.Entry(outer, textvariable=self.key_var, show="•").grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        if self.config.api_key:
            key_hint = "已有密钥已载入；留空保留当前密钥。"
        elif os.name == "nt":
            key_hint = "密钥使用当前 Windows 用户的 DPAPI 加密保存。"
        else:
            key_hint = "此系统不会保存密钥；关闭程序后需要重新输入。"
        ttk.Label(outer, text=key_hint).grid(row=3, column=1, columnspan=2, sticky="w")
        self.clear_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="清除已保存密钥", variable=self.clear_key_var).grid(row=4, column=1, sticky="w", pady=4)

        ttk.Label(outer, text="请求超时（秒）").grid(row=5, column=0, sticky="w", pady=6)
        self.timeout_var = tk.StringVar(value=str(self.config.timeout))
        ttk.Spinbox(outer, from_=1, to=120, increment=1, textvariable=self.timeout_var, width=12).grid(row=5, column=1, sticky="w", pady=6)

        self.enabled_var = tk.BooleanVar(value=self.config.enabled)
        ttk.Checkbutton(outer, text="启用翻译服务", variable=self.enabled_var).grid(row=6, column=1, sticky="w", pady=6)

        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 10))
        ttk.Button(buttons, text="保存设置", command=self._save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="开始", command=self._start).pack(side="left", padx=4)
        ttk.Button(buttons, text="停止", command=self.service.stop).pack(side="left", padx=4)
        self.test_button = ttk.Button(buttons, text="测试连接", command=self._test_connection)
        self.test_button.pack(side="left", padx=4)

        ttk.Separator(outer).grid(row=8, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(outer, text="服务状态").grid(row=9, column=0, sticky="nw", pady=4)
        self.status_var = tk.StringVar(value=self.service.status)
        ttk.Label(outer, textvariable=self.status_var, wraplength=500).grid(row=9, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(outer, text="操作反馈").grid(row=10, column=0, sticky="nw", pady=4)
        self.feedback_var = tk.StringVar(value=self._load_notice or "设置已载入。")
        ttk.Label(outer, textvariable=self.feedback_var, wraplength=500).grid(row=10, column=1, columnspan=2, sticky="w", pady=4)
        note = (
            "URL 支持基础地址、/v1 或完整接口；基础地址会自动补全。"
            "测试连接只发送固定示例，不启用聊天翻译。安装游戏插件并启用后，聊天正文会发送到所选服务，"
            "中文也由模型检测；密钥仅保存在本机。"
        )
        ttk.Label(outer, text=note, wraplength=610, justify="left").grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )

    def _read_form(self) -> AppConfig:
        api_key = self.config.api_key
        if self.clear_key_var.get():
            api_key = ""
        elif self.key_var.get():
            api_key = self.key_var.get()
        try:
            timeout = float(self.timeout_var.get())
        except ValueError:
            raise ConfigError("超时必须是 1 到 120 之间的数字") from None
        url = self.url_var.get().strip()
        if url:
            url = normalize_endpoint(url)
        config = AppConfig(
            url=url,
            model=self.model_var.get().strip(),
            api_key=api_key,
            timeout=timeout,
            enabled=self.enabled_var.get(),
        )
        validate_config(config)
        return config

    def _apply(self, persist: bool) -> bool:
        try:
            config = self._read_form()
            self.url_var.set(config.url)
            if persist:
                save_config(config)
            self.translator.configure(config)
            self.config = config
            self.key_var.set("")
            self.clear_key_var.set(False)
        except (ConfigError, OSError) as exc:
            messagebox.showerror("设置无效", str(exc), parent=self.root)
            return False
        if persist and os.name != "nt" and config.api_key:
            self.feedback_var.set("设置已保存；此系统不会将 API 密钥写入磁盘，关闭程序后需重新输入。")
        elif self._load_notice:
            self.feedback_var.set(self._load_notice)
            self._load_notice = ""
        else:
            self.feedback_var.set("设置已保存。")
        return True

    def _save(self) -> None:
        self._apply(persist=True)

    def _start(self) -> None:
        if not self._apply(persist=True):
            return
        try:
            self.service.start()
        except ServiceAlreadyRunningError:
            self.feedback_var.set("该邮箱已有另一个翻译服务运行，请先停止另一实例。")
        except OSError:
            self.feedback_var.set("无法创建邮箱目录，请检查本地目录权限。")

    def _test_connection(self) -> None:
        if str(self.test_button["state"]) == "disabled":
            return
        if not self._apply(persist=True):
            return
        self.test_button.configure(state="disabled")
        self.feedback_var.set("正在后台发送固定测试示例…")

        def run_test() -> None:
            try:
                result = self.translator.test_connection()
                message = f"连接成功：{result}"
            except TranslationError as exc:
                message = f"连接失败：{exc}"
            except Exception:
                message = "连接失败：翻译服务暂不可用。"
            self._feedback_queue.put((message, True))

        threading.Thread(target=run_test, name="hd2-translate-connection-test", daemon=True).start()

    def _refresh_status(self) -> None:
        if self._closed:
            return
        if self.service.running:
            self.status_var.set(self.service.status)
        else:
            self.status_var.set(self.service.status)
        while True:
            try:
                message, completed = self._feedback_queue.get_nowait()
            except queue.Empty:
                break
            self.feedback_var.set(message)
            if completed:
                self.test_button.configure(state="normal")
        self._after_id = self.root.after(250, self._refresh_status)

    def _close(self) -> None:
        self._closed = True
        self.service.stop()
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.root.destroy()


def run_gui() -> None:
    root = tk.Tk()
    CompanionWindow(root)
    root.mainloop()
