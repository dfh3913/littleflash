"""Configuration loading for LittleFlash.

The application is driven by a single `config.yml`.  Runtime study
preferences (wordbook, timer, ...) deliberately live in a separate JSON file
so that this file can stay small, reviewable and, most importantly, contain
the server-only settings password.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when config.yml is missing or contains an invalid value."""


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
    prefetch: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_path: Path
    root_dir: Path
    host: str
    port: int
    debug: bool
    secret_key: str = field(repr=False)
    session_lifetime_seconds: int
    session_lifetime_hours: float
    wordbooks_dir: Path
    wordbooks_auto_reload: bool
    wordbook_max_file_bytes: int
    wordbook_max_books: int
    settings_password: str | None = field(repr=False)
    settings_password_hash: str | None = field(repr=False)
    preferences_path: Path
    audio: AudioConfig
    security: SecurityConfig


def default_config_path() -> Path:
    env_path = os.environ.get("LITTLEFLASH_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()

    cwd_path = Path.cwd() / "config.yml"
    if cwd_path.exists():
        return cwd_path.resolve()

    # src/littleflash/config.py -> project root
    project_path = Path(__file__).resolve().parents[2] / "config.yml"
    if project_path.exists():
        return project_path

    return cwd_path


def load_config(config_path: str | os.PathLike[str] | None = None) -> AppConfig:
    path = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else default_config_path()
    )
    if not path.is_file():
        raise ConfigError(
            f"找不到配置文件 {path}。请从项目根目录启动，或设置 LITTLEFLASH_CONFIG。"
        )
    try:
        if path.stat().st_size > 1_048_576:
            raise ConfigError("config.yml 过大（上限 1 MiB）")
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"读取 config.yml 失败：{exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yml 顶层必须是映射（mapping）")

    root = path.parent
    app_section = _section(raw, "app")
    wordbooks_section = _section(raw, "wordbooks")
    settings_section = _section(raw, "settings")
    audio_section = _section(raw, "audio")
    security_section = _section(raw, "security")

    host = os.environ.get("LITTLEFLASH_HOST") or _text(
        app_section, "host", "127.0.0.1"
    )
    env_port = _env_int("LITTLEFLASH_PORT")
    port = (
        env_port
        if env_port is not None
        else _integer(app_section, "port", 8000, minimum=1, maximum=65535)
    )
    if not 1 <= port <= 65535:
        raise ConfigError("端口必须在 1 到 65535 之间")
    debug = (
        _env_bool("LITTLEFLASH_DEBUG")
        if os.environ.get("LITTLEFLASH_DEBUG") is not None
        else _boolean(app_section, "debug", False)
    )
    session_hours = _number(
        app_section, "session_hours", 2.0, minimum=0.05, maximum=720.0
    )

    secret_key = _resolve_secret_key(root, app_section.get("secret_key"))

    wordbooks_dir = _resolve_path(
        root, _text(wordbooks_section, "directory", "src/wordbooks")
    )
    auto_reload = _boolean(wordbooks_section, "auto_reload", True)
    max_file_size_kb = _integer(
        wordbooks_section, "max_file_size_kb", 4096, minimum=16, maximum=65536
    )
    max_books = _integer(
        wordbooks_section, "max_books", 100, minimum=1, maximum=1000
    )

    password, password_hash = _resolve_password(settings_section)
    preferences_path = _resolve_path(
        root,
        _text(settings_section, "state_file", "data/preferences.json"),
    )

    audio_enabled = _boolean(audio_section, "enabled", True)
    voice_type = _integer(audio_section, "type", 2, minimum=1, maximum=2)
    timeout_seconds = _number(
        audio_section, "timeout_seconds", 8.0, minimum=1.0, maximum=30.0
    )
    max_bytes = _integer(
        audio_section,
        "max_bytes",
        3 * 1024 * 1024,
        minimum=64 * 1024,
        maximum=20 * 1024 * 1024,
    )
    cache_dir = _resolve_path(
        root, _text(audio_section, "cache_dir", ".cache/audio")
    )
    cache_ttl_hours = _integer(
        audio_section, "cache_ttl_hours", 720, minimum=1, maximum=87600
    )
    audio_prefetch = (
        _env_bool("LITTLEFLASH_AUDIO_PREFETCH")
        if os.environ.get("LITTLEFLASH_AUDIO_PREFETCH") is not None
        else _boolean(audio_section, "prefetch", True)
    )

    security = SecurityConfig(
        trust_proxy_headers=_env_bool("LITTLEFLASH_TRUST_PROXY")
        if os.environ.get("LITTLEFLASH_TRUST_PROXY") is not None
        else _boolean(security_section, "trust_proxy_headers", False),
        cookie_secure=_env_bool("LITTLEFLASH_COOKIE_SECURE")
        if os.environ.get("LITTLEFLASH_COOKIE_SECURE") is not None
        else _boolean(security_section, "cookie_secure", False),
        login_max_attempts=_integer(
            security_section, "login_max_attempts", 5, minimum=1, maximum=100
        ),
        login_window_seconds=_integer(
            security_section,
            "login_window_seconds",
            600,
            minimum=30,
            maximum=86400,
        ),
        login_lockout_seconds=_integer(
            security_section,
            "login_lockout_seconds",
            900,
            minimum=30,
            maximum=86400,
        ),
    )

    return AppConfig(
        config_path=path,
        root_dir=root,
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
        settings_password=password,
        settings_password_hash=password_hash,
        preferences_path=preferences_path,
        audio=AudioConfig(
            enabled=audio_enabled,
            voice_type=voice_type,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_hours * 3600,
            prefetch=audio_prefetch,
        ),
        security=security,
    )


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"config.yml 中的 {key} 必须是映射（mapping）")
    return value


def _text(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"配置项 {key} 必须是非空字符串")
    return value.strip()


def _optional_text(section: dict[str, Any], key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"配置项 {key} 必须是字符串")
    value = value.strip()
    return value or None


def _boolean(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 {key} 必须是 true 或 false")
    return value


def _integer(
    section: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"配置项 {key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ConfigError(f"配置项 {key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _number(
    section: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置项 {key} 必须是数字")
    value = float(value)
    if not minimum <= value <= maximum:
        raise ConfigError(f"配置项 {key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数") from exc
    return parsed


def _env_bool(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"环境变量 {name} 必须是 true/false")


def _resolve_password(
    section: dict[str, Any],
) -> tuple[str | None, str | None]:
    env_password = os.environ.get("LITTLEFLASH_SETTINGS_PASSWORD")
    if env_password is not None:
        if not env_password:
            raise ConfigError("LITTLEFLASH_SETTINGS_PASSWORD 不能为空")
        return env_password, None

    password = _optional_text(section, "password")
    password_hash = _optional_text(section, "password_hash")

    if password_hash:
        # password_hash wins if a user leaves both behind while migrating.
        return None, password_hash
    if password:
        return password, None
    raise ConfigError(
        "未配置设置页密码。请在 config.yml 的 settings.password "
        "或 settings.password_hash 中设置一个强密码。"
    )


def _resolve_secret_key(root: Path, configured: Any) -> str:
    env_secret = os.environ.get("LITTLEFLASH_SECRET_KEY")
    if env_secret:
        if len(env_secret) < 16:
            raise ConfigError("LITTLEFLASH_SECRET_KEY 至少需要 16 个字符")
        return env_secret

    if configured is not None:
        if not isinstance(configured, str) or len(configured.strip()) < 16:
            raise ConfigError("app.secret_key 至少需要 16 个字符")
        return configured.strip()

    secret_path = root / "data" / ".secret_key"
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise ConfigError(f"无法创建 data 目录：{exc}") from exc

    # Multiple gunicorn workers may start at once; create the secret file
    # exactly once and let the other workers read it.
    for _ in range(50):
        if secret_path.exists():
            try:
                existing = secret_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigError(f"无法读取 {secret_path}：{exc}") from exc
            if len(existing) >= 32:
                try:
                    secret_path.chmod(0o600)
                except OSError:
                    pass
                return existing
            time.sleep(0.02)
            continue
        try:
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise ConfigError(f"无法创建密钥文件 {secret_path}：{exc}") from exc
        generated = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        return generated

    raise ConfigError(
        f"无法初始化会话密钥 {secret_path}，请删除损坏的文件后重试。"
    )
