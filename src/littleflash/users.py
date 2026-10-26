"""用户账号：创建、校验、改密与列表查询。"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from .db import ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_USER, ROLES, connect

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128

# 列表里让超级管理员、管理员排在普通用户前面
_ROLE_ORDER = "CASE role WHEN 'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END"
_ROLE_ORDER_U = "CASE u.role WHEN 'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END"


class UserError(ValueError):
    """账号相关的可展示错误。"""


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    role: str
    display_name: str
    is_active: bool
    created_at: int
    last_login_at: int | None

    @property
    def is_admin(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_SUPERADMIN)

    @property
    def is_superadmin(self) -> bool:
        return self.role == ROLE_SUPERADMIN

    @property
    def role_label(self) -> str:
        return {
            ROLE_USER: "普通用户",
            ROLE_ADMIN: "管理员",
            ROLE_SUPERADMIN: "超级管理员",
        }.get(self.role, self.role)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "roleLabel": self.role_label,
            "displayName": self.display_name,
            "isActive": self.is_active,
            "lastLoginAt": self.last_login_at,
        }


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        display_name=row["display_name"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def validate_username(username: str) -> str:
    value = (username or "").strip()
    if not USERNAME_RE.match(value):
        raise UserError("用户名需为 3-32 位的字母、数字、下划线、点或连字符")
    return value


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise UserError("密码过长")
    return password


def get_user(db_path: Path, user_id: int) -> User | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def find_user(db_path: Path, username: str) -> User | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _row_to_user(row) if row else None


def authenticate(db_path: Path, username: str, password: str) -> User | None:
    """校验用户名与密码，成功时刷新登录时间。"""
    value = (username or "").strip()
    if not value or not password:
        return None
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (value,)
        ).fetchone()
        if row is None or not row["is_active"]:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        now = int(time.time())
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
        return _row_to_user(
            conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        )


def create_user(
    db_path: Path,
    username: str,
    password: str,
    *,
    role: str = ROLE_USER,
    display_name: str = "",
) -> User:
    name = validate_username(username)
    secret = validate_password(password)
    if role not in ROLES:
        raise UserError("角色无效")
    with connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (name,)
        ).fetchone()
        if exists:
            raise UserError("用户名已存在")
        now = int(time.time())
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, role, display_name, is_active,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (name, generate_password_hash(secret), role, display_name.strip(), now, now),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    # 新用户默认拥有一份自己的偏好，避免首访时为空
    ensure_user_preferences(db_path, int(row["id"]))
    return _row_to_user(row)


def ensure_user_preferences(db_path: Path, user_id: int) -> None:
    now = int(time.time())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO preferences
                (owner, wordbook_id, unit_id, front, sort_order, mode,
                 interval_seconds, audio_enabled, audio_volume, updated_at)
            SELECT ?, wordbook_id, unit_id, front, sort_order, mode,
                   interval_seconds, audio_enabled, audio_volume, ?
            FROM preferences WHERE owner = 'anonymous'
            """,
            (f"user:{user_id}", now),
        )


def set_password(db_path: Path, user_id: int, password: str) -> None:
    secret = validate_password(password)
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise UserError("用户不存在")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(secret), int(time.time()), user_id),
        )


def set_active(db_path: Path, user_id: int, active: bool) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, int(time.time()), user_id),
        )


def delete_user(db_path: Path, user_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM preferences WHERE owner = ?", (f"user:{user_id}",))


def count_by_role(db_path: Path, role: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = ?", (role,)
        ).fetchone()
    return int(row["n"])


def list_users(db_path: Path) -> list[User]:
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM users ORDER BY {_ROLE_ORDER}, username COLLATE NOCASE"
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def list_users_with_progress(
    db_path: Path, book_titles: dict[str, str]
) -> list[dict[str, Any]]:
    """管理员视图：用户 + 学习进度 + 累计时长。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT u.*,
                   p.wordbook_id, p.unit_id, p.word_index, p.word_en,
                   p.rounds, p.total_seconds, p.last_study_at
            FROM users u
            LEFT JOIN progress p ON p.user_id = u.id
            ORDER BY {_ROLE_ORDER_U}, u.username COLLATE NOCASE
            """
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        user = _row_to_user(row)
        payload = user.public()
        payload.update(
            {
                "wordbookId": row["wordbook_id"] or "",
                "wordbookTitle": book_titles.get(row["wordbook_id"] or "", ""),
                "unitId": row["unit_id"] or "all",
                "wordIndex": int(row["word_index"] or 0),
                "wordEn": row["word_en"] or "",
                "rounds": int(row["rounds"] or 0),
                "totalSeconds": int(row["total_seconds"] or 0),
                "lastStudyAt": row["last_study_at"],
            }
        )
        result.append(payload)
    return result


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "User",
    "UserError",
    "authenticate",
    "count_by_role",
    "create_user",
    "delete_user",
    "ensure_user_preferences",
    "find_user",
    "get_user",
    "list_users",
    "list_users_with_progress",
    "set_active",
    "set_password",
    "validate_password",
    "validate_username",
]
