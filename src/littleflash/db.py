"""SQLite 存储层：用户、学习偏好、学习进度与站点设置。

数据库默认位于 ``<项目根>/data/littleflash.db``，可用环境变量
``LITTLEFLASH_DB`` 覆盖。所有写入都在事务里完成，连接按需创建并启用
WAL，方便 gunicorn 多线程并发读。
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"
ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_SUPERADMIN)

ANONYMOUS_OWNER = "anonymous"

# 默认账号：240 + 1~8 的一位数 + 01~55 的两位数，共 440 个，密码与用户名相同。
DEFAULT_ACCOUNT_PREFIX = "240"
DEFAULT_ACCOUNT_GROUPS = 8
DEFAULT_ACCOUNT_RANGE = 55

# 去掉容易混淆的 0/O/1/l/I，方便管理员抄写随机密码。
PASSWORD_ALPHABET = "".join(
    ch
    for ch in string.ascii_letters + string.digits
    if ch not in "0O1lI"
)

# 440 个默认账号的初始密码等于用户名（公开的初始凭据），首次建库需要一次性
# 生成 440 条哈希；Werkzeug 默认的 scrypt 在低配机器上约 0.9 秒/条，会让首
# 次启动卡住数分钟，因此这里使用较便宜的 PBKDF2。用户改密后 set_password()
# 会自动改用 Werkzeug 的默认算法（scrypt）。
DEFAULT_ACCOUNT_HASH_METHOD = "pbkdf2:sha256:10000"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    display_name  TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS preferences (
    owner            TEXT PRIMARY KEY,
    wordbook_id      TEXT    NOT NULL DEFAULT '',
    unit_id          TEXT    NOT NULL DEFAULT 'all',
    front            TEXT    NOT NULL DEFAULT 'en',
    sort_order       TEXT    NOT NULL DEFAULT 'sequence',
    mode             TEXT    NOT NULL DEFAULT 'auto',
    interval_seconds REAL    NOT NULL DEFAULT 4.0,
    audio_enabled    INTEGER NOT NULL DEFAULT 1,
    audio_volume     REAL    NOT NULL DEFAULT 0.8,
    updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    wordbook_id   TEXT    NOT NULL DEFAULT '',
    unit_id       TEXT    NOT NULL DEFAULT 'all',
    word_index    INTEGER NOT NULL DEFAULT 0,
    word_en       TEXT    NOT NULL DEFAULT '',
    rounds        INTEGER NOT NULL DEFAULT 0,
    total_seconds INTEGER NOT NULL DEFAULT 0,
    last_study_at INTEGER,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS site_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SITE_SETTINGS: dict[str, str] = {
    "app.host": "127.0.0.1",
    "app.port": "4570",
    "app.debug": "false",
    "app.session_hours": "2",
    "wordbooks.directory": "src/wordbooks",
    "wordbooks.auto_reload": "true",
    "wordbooks.max_file_size_kb": "4096",
    "wordbooks.max_books": "100",
    "audio.enabled": "true",
    "audio.type": "2",
    "audio.timeout_seconds": "8",
    "audio.max_bytes": "3145728",
    "audio.cache_dir": ".cache/audio",
    "audio.cache_ttl_hours": "720",
    "security.trust_proxy_headers": "true",
    "security.cookie_secure": "false",
    "security.login_max_attempts": "5",
    "security.login_window_seconds": "600",
    "security.login_lockout_seconds": "900",
}


def default_db_path(root: Path) -> Path:
    override = os.environ.get("LITTLEFLASH_DB")
    if override:
        return Path(override).expanduser().resolve()
    return (root / "data" / "littleflash.db").resolve()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """打开一个短连接；退出时自动提交或回滚。"""
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_password(length: int = 8) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def default_usernames() -> list[str]:
    names = []
    for group in range(1, DEFAULT_ACCOUNT_GROUPS + 1):
        for index in range(1, DEFAULT_ACCOUNT_RANGE + 1):
            names.append(f"{DEFAULT_ACCOUNT_PREFIX}{group}{index:02d}")
    return names


def _int_env(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def ensure_database(db_path: Path, *, root: Path) -> dict[str, Any]:
    """建表、写入默认站点设置并补齐初始账号。

    返回 ``{"created": bool, "accounts": {...}}``，首次创建时附带随机管理员密码，
    便于调用方输出到日志或初始化文件。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fresh = not db_path.exists()
    created_accounts: dict[str, str] = {}

    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _seed_site_settings(conn)
        _seed_preferences(conn)
        created_accounts = _seed_users(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('initialized_at', ?)",
            (str(int(time.time())),),
        )

    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass
    return {"created": fresh, "accounts": created_accounts, "root": root}


def _seed_site_settings(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    rows = [
        (key, value, now) for key, value in DEFAULT_SITE_SETTINGS.items()
    ]
    secret = _existing_secret(conn)
    if secret is None:
        rows.append(("app.secret_key", secrets.token_hex(32), now))
    conn.executemany(
        "INSERT OR IGNORE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)",
        rows,
    )


def _existing_secret(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM site_settings WHERE key = 'app.secret_key'"
    ).fetchone()
    return row["value"] if row else None


def _seed_preferences(conn: sqlite3.Connection) -> None:
    """写入未登录访客的默认偏好（超级管理员可修改）。"""
    now = int(time.time())
    conn.execute(
        """
        INSERT OR IGNORE INTO preferences
            (owner, wordbook_id, unit_id, front, sort_order, mode,
             interval_seconds, audio_enabled, audio_volume, updated_at)
        VALUES (?, '', 'all', 'en', 'sequence', 'auto', 4.0, 1, 0.8, ?)
        """,
        (ANONYMOUS_OWNER, now),
    )


def _seed_users(conn: sqlite3.Connection) -> dict[str, str]:
    """补齐初始账号：440 个默认用户 + admin + telecomadmin。

    已存在的账号不会重复创建，管理员密码只在创建时生成一次。
    """
    from werkzeug.security import generate_password_hash

    created: dict[str, str] = {}
    now = int(time.time())

    existing = {row["username"] for row in conn.execute("SELECT username FROM users")}

    bulk = []
    total = _int_env("LITTLEFLASH_SEED_ACCOUNTS", len(default_usernames()))
    for username in default_usernames()[: max(0, total)]:
        if username in existing:
            continue
        bulk.append(
            (
                username,
                generate_password_hash(
                    username, method=DEFAULT_ACCOUNT_HASH_METHOD
                ),
                ROLE_USER,
                "",
                1,
                now,
                now,
            )
        )
    if bulk:
        conn.executemany(
            """
            INSERT OR IGNORE INTO users
                (username, password_hash, role, display_name, is_active,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            bulk,
        )

    admins_enabled = _int_env("LITTLEFLASH_SEED_ADMINS", 1) != 0
    if admins_enabled:
        for username, role in (
            ("admin", ROLE_ADMIN),
            ("telecomadmin", ROLE_SUPERADMIN),
        ):
            if username in existing:
                continue
            password = generate_password(
                _int_env("LITTLEFLASH_ADMIN_PASSWORD_LENGTH", 8)
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO users
                    (username, password_hash, role, display_name, is_active,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (username, generate_password_hash(password), role, "", now, now),
            )
            created[username] = password

    return created


def write_bootstrap_credentials(db_path: Path, accounts: dict[str, str]) -> Path | None:
    """把随机生成的管理员密码落盘，权限 600，方便首次登录。"""
    if not accounts:
        return None
    path = db_path.parent / "initial-accounts.txt"
    lines = [
        "# LittleFlash 初始化账号（首次创建数据库时生成，登录后请尽快修改）",
        "# 默认用户 440 个：240101-240155 ... 240801-240855，密码与用户名相同",
        "",
    ]
    for username, password in accounts.items():
        lines.append(f"{username}\t{password}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


__all__ = [
    "ANONYMOUS_OWNER",
    "DEFAULT_SITE_SETTINGS",
    "ROLE_ADMIN",
    "ROLE_SUPERADMIN",
    "ROLE_USER",
    "ROLES",
    "connect",
    "default_db_path",
    "default_usernames",
    "ensure_database",
    "generate_password",
    "write_bootstrap_credentials",
]
