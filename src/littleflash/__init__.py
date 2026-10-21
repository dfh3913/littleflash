"""LittleFlash 闪词."""

from __future__ import annotations

import argparse
import logging
import sys

__version__ = "1.0.0"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="littleflash",
        description="闪词 LittleFlash —— 本地优先的单词闪卡服务",
    )
    parser.add_argument("--config", help="config.yml 路径（默认自动查找）")
    parser.add_argument("--host", help="覆盖监听地址")
    parser.add_argument("--port", type=int, help="覆盖监听端口")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用 Flask 调试模式（请勿在生产环境使用）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .app import create_app
    from .config import ConfigError, load_config

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    host = args.host or config.host
    port = args.port or config.port
    debug = bool(args.debug or config.debug)
    app = create_app(str(config.config_path))
    print(f"闪词已启动：http://{host}:{port}")
    print(f"设置页：    http://{host}:{port}/settings")
    app.run(host=host, port=port, debug=debug)


__all__ = ["__version__", "main"]
