"""学习偏好：校验规则 + SQLite 持久化。

偏好按 ``owner`` 分片保存：
- ``anonymous``：未登录访客看到/使用的站点默认值（超级管理员可改）；
- ``user:<id>``：登录用户自己的设置。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .db import ANONYMOUS_OWNER, connect
from .wordbooks import WordbookManager

FRONTS = ("en", "zh")
ORDERS = ("sequence", "shuffle")
MODES = ("auto", "manual")
MIN_INTERVAL = 1.0
MAX_INTERVAL = 30.0

# 偏好字典键 -> 数据表列名（order 是 SQL 关键字，列名用 sort_order）
_COLUMNS = {
    "wordbook_id": "wordbook_id",
    "unit_id": "unit_id",
    "front": "front",
    "order": "sort_order",
    "mode": "mode",
    "interval_seconds": "interval_seconds",
    "audio_enabled": "audio_enabled",
    "audio_volume": "audio_volume",
}


class SettingsValidationError(ValueError):
    """A client-supplied preference value is invalid."""


def default_preferences(manager: WordbookManager) -> dict[str, Any]:
    book = manager.first_book()
    return {
        "wordbook_id": book.id if book else "",
        "unit_id": "all",
        "front": "en",
        "order": "sequence",
        "mode": "auto",
        "interval_seconds": 4.0,
        "audio_enabled": True,
        "audio_volume": 0.8,
    }


def public_preferences(raw: Any, manager: WordbookManager) -> dict[str, Any]:
    """Always return a complete, safe set of preferences.

    Stored state is treated as untrusted (it may predate a wordbook deletion),
    so anomalies fall back to a sensible value instead of breaking the UI.
    """
    defaults = default_preferences(manager)
    if not isinstance(raw, dict):
        return defaults

    books = manager.books()
    by_id = {book.id: book for book in books}

    wordbook_id = raw.get("wordbook_id")
    if not isinstance(wordbook_id, str) or wordbook_id not in by_id:
        wordbook_id = defaults["wordbook_id"]
    book = by_id.get(wordbook_id)

    unit_id = raw.get("unit_id")
    if not isinstance(unit_id, str):
        unit_id = "all"
    if unit_id != "all" and (book is None or book.get_unit(unit_id) is None):
        unit_id = "all"

    front = raw.get("front")
    if front not in FRONTS:
        front = defaults["front"]
    order = raw.get("order")
    if order not in ORDERS:
        order = defaults["order"]
    mode = raw.get("mode")
    if mode not in MODES:
        mode = defaults["mode"]

    interval = _safe_float(raw.get("interval_seconds"), defaults["interval_seconds"])
    interval = min(MAX_INTERVAL, max(MIN_INTERVAL, round(interval * 2) / 2))

    audio_enabled = raw.get("audio_enabled")
    if not isinstance(audio_enabled, bool):
        audio_enabled = defaults["audio_enabled"]

    volume = _safe_float(raw.get("audio_volume"), defaults["audio_volume"])
    volume = min(1.0, max(0.0, round(volume, 2)))

    return {
        "wordbook_id": wordbook_id,
        "unit_id": unit_id,
        "front": front,
        "order": order,
        "mode": mode,
        "interval_seconds": interval,
        "audio_enabled": audio_enabled,
        "audio_volume": volume,
    }


def validate_update(
    patch: Any,
    current: dict[str, Any],
    manager: WordbookManager,
) -> dict[str, Any]:
    """Validate a partial settings update and return complete preferences."""
    if not isinstance(patch, dict):
        raise SettingsValidationError("请求内容必须是 JSON 对象")

    books = manager.books()
    if not books:
        raise SettingsValidationError("词书目录为空，暂时无法保存设置")
    by_id = {book.id: book for book in books}
    defaults = default_preferences(manager)
    merged = {**current}

    if "wordbook_id" in patch:
        value = patch["wordbook_id"]
        if not isinstance(value, str) or value not in by_id:
            raise SettingsValidationError("所选词书不存在")
        merged["wordbook_id"] = value

    if "unit_id" in patch:
        value = patch["unit_id"]
        if not isinstance(value, str):
            raise SettingsValidationError("单元参数无效")
        book = by_id.get(merged["wordbook_id"])
        if value != "all" and (book is None or book.get_unit(value) is None):
            raise SettingsValidationError("所选单元不属于当前词书")
        merged["unit_id"] = value

    book = by_id.get(merged["wordbook_id"], books[0])
    if merged.get("wordbook_id") not in by_id:
        merged["wordbook_id"] = book.id
    if merged.get("unit_id") != "all" and book.get_unit(merged["unit_id"]) is None:
        merged["unit_id"] = "all"

    for key, allowed in (("front", FRONTS), ("order", ORDERS), ("mode", MODES)):
        if key in patch:
            value = patch[key]
            if value not in allowed:
                raise SettingsValidationError(f"设置项 {key} 的值无效")
            merged[key] = value

    if "interval_seconds" in patch:
        value = patch["interval_seconds"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError("切换间隔必须是数字")
        value = float(value)
        if not MIN_INTERVAL <= value <= MAX_INTERVAL:
            raise SettingsValidationError(
                f"切换间隔必须在 {MIN_INTERVAL:g} 到 {MAX_INTERVAL:g} 秒之间"
            )
        merged["interval_seconds"] = round(value * 2) / 2

    if "audio_enabled" in patch:
        if not isinstance(patch["audio_enabled"], bool):
            raise SettingsValidationError("发音开关的值无效")
        merged["audio_enabled"] = patch["audio_enabled"]

    if "audio_volume" in patch:
        value = patch["audio_volume"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError("音量必须是数字")
        value = float(value)
        if not 0.0 <= value <= 1.0:
            raise SettingsValidationError("音量必须在 0 到 1 之间")
        merged["audio_volume"] = round(value, 2)

    # Fill in fields omitted by a partial client payload.
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return public_preferences(merged, manager)


def _row_to_preferences(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "wordbook_id": row["wordbook_id"],
        "unit_id": row["unit_id"],
        "front": row["front"],
        "order": row["sort_order"],
        "mode": row["mode"],
        "interval_seconds": float(row["interval_seconds"]),
        "audio_enabled": bool(row["audio_enabled"]),
        "audio_volume": float(row["audio_volume"]),
    }


class PreferenceStore:
    """按 owner 读写偏好；写入前统一走 ``validate_update``。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def load(self, owner: str, manager: WordbookManager) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM preferences WHERE owner = ?", (owner,)
            ).fetchone()
        raw = _row_to_preferences(row) if row else None
        return public_preferences(raw, manager)

    def update(
        self,
        owner: str,
        patch: Any,
        manager: WordbookManager,
    ) -> dict[str, Any]:
        current = self.load(owner, manager)
        updated = validate_update(patch, current, manager)
        self._write(owner, updated)
        return public_preferences(updated, manager)

    def replace(
        self,
        owner: str,
        value: dict[str, Any],
        manager: WordbookManager,
    ) -> dict[str, Any]:
        normalized = validate_update(value, default_preferences(manager), manager)
        self._write(owner, normalized)
        return normalized

    def reset(self, owner: str, manager: WordbookManager) -> dict[str, Any]:
        defaults = default_preferences(manager)
        self._write(owner, defaults)
        return defaults

    def _write(self, owner: str, value: dict[str, Any]) -> None:
        now = int(time.time())
        columns = ", ".join(_COLUMNS.values())
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS.values())
        params = [
            value.get("wordbook_id", ""),
            value.get("unit_id", "all"),
            value.get("front", "en"),
            value.get("order", "sequence"),
            value.get("mode", "auto"),
            float(value.get("interval_seconds", 4.0)),
            1 if value.get("audio_enabled", True) else 0,
            float(value.get("audio_volume", 0.8)),
        ]
        with connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO preferences (owner, {columns}, updated_at)
                VALUES (?, {placeholders}, ?)
                ON CONFLICT(owner) DO UPDATE SET
                    {updates},
                    updated_at = excluded.updated_at
                """,
                [owner, *params, now],
            )


def sample_word(
    preferences: dict[str, Any],
    manager: WordbookManager,
) -> dict[str, str] | None:
    book = manager.get(str(preferences.get("wordbook_id", "")))
    if book is None:
        book = manager.first_book()
    if book is None:
        return None
    unit = None
    unit_id = preferences.get("unit_id")
    if unit_id != "all":
        unit = book.get_unit(str(unit_id))
    if unit is None and unit_id != "all":
        return None
    units = (unit,) if unit is not None else book.units
    for candidate in units:
        if candidate.words:
            word = candidate.words[0]
            return {
                "en": word.en,
                "zh": word.zh,
                "phonetic": word.phonetic,
                "unitTitle": candidate.title,
            }
    return None


def _safe_float(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def owner_for_user(user_id: int) -> str:
    return f"user:{user_id}"


__all__ = [
    "ANONYMOUS_OWNER",
    "FRONTS",
    "MAX_INTERVAL",
    "MIN_INTERVAL",
    "MODES",
    "ORDERS",
    "PreferenceStore",
    "SettingsValidationError",
    "default_preferences",
    "owner_for_user",
    "public_preferences",
    "sample_word",
    "validate_update",
]
