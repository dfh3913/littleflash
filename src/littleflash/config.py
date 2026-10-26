"""运行配置：全部来自 SQLite（可用环境变量覆盖）。

早期版本用 ``config.yml`` 保存端口、音频与设置页密码；现在这些值统一放在
数据库的 ``site_settings`` 表里，首次启动自动写入默认值。环境变量优先级
最高，方便同一份代码在不同机器上跑不同端口。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import connect, default_db_path, ensure_database, write_bootstrap_credentials

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """配置无法初始化时抛出。"""


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    trust_proxy_headers: bool
    cookie_secure: bool
    login_max_attempts: int
    login_window_seconds: int
    login_lockout_seconds: int


@dataclass(frozen=True, slots=True)
class AudioConfig:
    enabled: bool
    voice_type: int
    timeout_seconds: float
    max_bytes: int
    cache_dir: Path
    cache_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class AppConfig:
    root_dir: Path
    db_path: Path
    host: str
    port: int
    debug: bool
    secret_key: str
    session_lifetime_seconds: int
    session_lifetime_hours: float
    wordbooks_dir: Path
    wordbooks_auto_reload: bool
    wordbook_max_file_bytes: int
    wordbook_max_books: int
    audio: AudioConfig
    security: SecurityConfig
    settings: dict[str, str]


def project_root() -> Path:
    override = os.environ.get("LITTLEFLASH_HOME")
    if override:
        return Path(override).expanduser().resolve()
    # src/littleflash/config.py -> 项目根目录
    return Path(__file__).resolve().parents[2]


def read_site_settings(db_path: Path) -> dict[str, str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM site_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def write_site_setting(db_path: Path, key: str, value: Any) -> None:
    import time

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (key, str(value), int(time.time())),
        )


def load_config() -> AppConfig:
    root = project_root()
    db_path = default_db_path(root)

    try:
        result = ensure_database(db_path, root=root)
    except Exception as exc:  # sqlite3.Error / OSError
        raise ConfigError(f"初始化数据库失败：{exc}") from exc

    if result["accounts"]:
        path = write_bootstrap_credentials(db_path, result["accounts"])
        for username, password in result["accounts"].items():
            logger.warning("已创建初始账号 %s，初始密码：%s", username, password)
        if path is not None:
            logger.warning("初始管理员密码已写入 %s（权限 600）", path)
    if result["created"]:
        logger.info("已创建新的数据库：%s", db_path)

    raw = read_site_settings(db_path)

    host = os.environ.get("LITTLEFLASH_HOST") or raw.get("app.host", "127.0.0.1")
    port = _env_int("LITTLEFLASH_PORT", _int(raw, "app.port", 4570, 1, 65535))
    if not 1 <= port <= 65535:
        raise ConfigError("端口必须在 1 到 65535 之间")
    debug = _env_bool("LITTLEFLASH_DEBUG", _bool(raw, "app.debug", False))
    session_hours = _env_float(
        "LITTLEFLASH_SESSION_HOURS",
        _float(raw, "app.session_hours", 2.0, 0.05, 720.0),
        0.05,
        720.0,
    )

    secret_key = os.environ.get("LITTLEFLASH_SECRET_KEY") or raw.get("app.secret_key", "")
    if len(secret_key) < 32:
        raise ConfigError("app.secret_key 缺失或过短，请删除数据库后重新初始化")

    wordbooks_dir = _resolve_path(
        root, os.environ.get("LITTLEFLASH_WORDBOOKS") or raw.get("wordbooks.directory", "src/wordbooks")
    )
    auto_reload = _bool(raw, "wordbooks.auto_reload", True)
    max_file_size_kb = _int(raw, "wordbooks.max_file_size_kb", 4096, 16, 65536)
    max_books = _int(raw, "wordbooks.max_books", 100, 1, 1000)

    audio = AudioConfig(
        enabled=_bool(raw, "audio.enabled", True),
        voice_type=_int(raw, "audio.type", 2, 1, 2),
        timeout_seconds=_float(raw, "audio.timeout_seconds", 8.0, 1.0, 30.0),
        max_bytes=_int(raw, "audio.max_bytes", 3 * 1024 * 1024, 65536, 20 * 1024 * 1024),
        cache_dir=_resolve_path(root, raw.get("audio.cache_dir", ".cache/audio")),
        cache_ttl_seconds=_int(raw, "audio.cache_ttl_hours", 720, 1, 87600) * 3600,
    )

    security = SecurityConfig(
        trust_proxy_headers=_env_bool(
            "LITTLEFLASH_TRUST_PROXY", _bool(raw, "security.trust_proxy_headers", True)
        ),
        cookie_secure=_env_bool(
            "LITTLEFLASH_COOKIE_SECURE", _bool(raw, "security.cookie_secure", False)
        ),
        login_max_attempts=_env_int(
            "LITTLEFLASH_LOGIN_MAX_ATTEMPTS",
            _int(raw, "security.login_max_attempts", 5, 1, 100),
        ),
        login_window_seconds=_int(raw, "security.login_window_seconds", 600, 30, 86400),
        login_lockout_seconds=_env_int(
            "LITTLEFLASH_LOGIN_LOCKOUT",
            _int(raw, "security.login_lockout_seconds", 900, 30, 86400),
        ),
    )

    return AppConfig(
        root_dir=root,
        db_path=db_path,
        host=host,
        port=port,
        debug=debug,
        secret_key=secret_key,
        session_lifetime_hours=session_hours,
        session_lifetime_seconds=int(session_hours * 3600),
        wordbooks_dir=wordbooks_dir,
        wordbooks_auto_reload=auto_reload,
        wordbook_max_file_bytes=max_file_size_kb * 1024,
        wordbook_max_books=max_books,
        audio=audio,
        security=security,
        settings=raw,
    )


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _bool(raw: dict[str, str], key: str, default: bool) -> bool:
    value = raw.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(raw: dict[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw.get(key, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _float(raw: dict[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(raw.get(key, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_int(name: str, fallback: int) -> int:
    value = os.environ.get(name)
    if not value:
        return fallback
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数") from exc


def _env_float(
    name: str, fallback: float, minimum: float, maximum: float
) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是数字") from exc
    return max(minimum, min(maximum, parsed))


def _env_bool(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"环境变量 {name} 必须是 true/false")


__all__ = [
    "AppConfig",
    "AudioConfig",
    "ConfigError",
    "SecurityConfig",
    "load_config",
    "project_root",
    "read_site_settings",
    "write_site_setting",
]
