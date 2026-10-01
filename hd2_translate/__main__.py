"""命令行入口。"""

from __future__ import annotations

import argparse
import time

from .config import ConfigError, load_config
from .service import CompanionService, ServiceAlreadyRunningError
from .translator import validate_config


def main() -> int:
    parser = argparse.ArgumentParser(description="绝地潜兵聊天翻译伴随服务")
    parser.add_argument("--headless", action="store_true", help="仅运行文件邮箱服务")
    args = parser.parse_args()
    if not args.headless:
        from .gui import run_gui

        run_gui()
        return 0
    config = load_config()
    try:
        validate_config(config)
    except ConfigError:
        print("配置无效，请启动图形界面检查设置。")
        return 2
    service = CompanionService(config)
    try:
        service.start()
    except ServiceAlreadyRunningError:
        print("该邮箱已有另一个翻译服务运行。")
        return 2
    print("后台翻译服务已启动。按 Ctrl+C 停止。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()
        print("后台翻译服务已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
