#!/usr/bin/env python3
"""闪词 LittleFlash · 生产启动入口。

应用代码部署在 /home/admin/littleflash，本文件作为唯一启动入口：

    /home/admin/littleflash/.venv/bin/python /home/admin/LittleFlash/LittleFlash.py

配置（监听地址、端口、音频、会话）已迁移到 SQLite 的 site_settings 表，
数据库默认位于 <LITTLEFLASH_HOME>/data/littleflash.db；首次启动会自动
建库、写入默认配置并生成初始账号（见 data/initial-accounts.txt）。

默认用 gunicorn 监听 127.0.0.1:4570（1 个进程 + 4 线程），再由 nginx
反代到 https://6090723.xyz/littleflash。可用环境变量
LITTLEFLASH_HOME / LITTLEFLASH_DB / LITTLEFLASH_HOST / LITTLEFLASH_PORT 覆盖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_HOME = Path(
    os.environ.get("LITTLEFLASH_HOME", "/home/admin/littleflash")
).expanduser().resolve()
SRC_DIR = APP_HOME / "src"


def prepare_runtime() -> None:
    """把部署目录加入模块搜索路径，并固定运行目录。"""
    if not SRC_DIR.is_dir():
        raise SystemExit(f"找不到应用代码目录：{SRC_DIR}")
    sys.path.insert(0, str(SRC_DIR))
    os.chdir(APP_HOME)
    os.environ.setdefault("LITTLEFLASH_HOME", str(APP_HOME))
    # gunicorn 以本文件目录之外的 cwd 启动时，也能找到 littleflash 包。
    existing = os.environ.get("PYTHONPATH", "")
    if str(SRC_DIR) not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{SRC_DIR}{os.pathsep}{existing}" if existing else str(SRC_DIR)
        )


def main() -> None:
    prepare_runtime()

    from littleflash.app import create_app
    from littleflash.config import ConfigError, load_config

    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"配置错误：{exc}") from exc

    app = create_app(config)

    host = os.environ.get("LITTLEFLASH_HOST") or config.host
    port = int(os.environ.get("LITTLEFLASH_PORT") or config.port)

    try:
        from gunicorn.app.base import BaseApplication
    except ImportError:  # 没有 gunicorn 时退回内置服务器，仅建议本机调试使用
        print(
            f"未安装 gunicorn，使用 Flask 内置服务器：http://{host}:{port}",
            file=sys.stderr,
            flush=True,
        )
        app.run(host=host, port=port, debug=False, threaded=True)
        return

    class LittleFlashServer(BaseApplication):
        def __init__(self, application, options: dict) -> None:
            self.options = options
            self.application = application
            super().__init__()

        def load_config(self) -> None:
            for key, value in self.options.items():
                if key in self.cfg.settings and value is not None:
                    self.cfg.set(key, value)

        def load(self):
            return self.application

    options = {
        "bind": f"{host}:{port}",
        # 登录限流是进程内状态，保持单 worker 才能准确计数。
        "workers": 1,
        "worker_class": "gthread",
        "threads": 4,
        "timeout": 30,
        "graceful_timeout": 10,
        "keepalive": 5,
        "accesslog": "-",
        "errorlog": "-",
        "loglevel": "info",
        "proc_name": "littleflash",
    }

    print(
        f"闪词已启动：http://{host}:{port}（gunicorn · 1 worker / 4 threads）",
        flush=True,
    )
    print(f"数据库：    {config.db_path}", flush=True)
    print(f"用户中心：  http://{host}:{port}/account", flush=True)
    LittleFlashServer(app, options).run()


if __name__ == "__main__":
    main()
