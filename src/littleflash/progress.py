"""学习进度与学习时长。

前端每次切词或定时心跳上报当前词书/单元/序号，并附带本段时间增量；
服务端只做上限校验后累加，避免一次请求灌入夸张的时长。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import connect

MAX_SECONDS_PER_REPORT = 600


@dataclass(frozen=True, slots=True)
class Progress:
    user_id: int
    wordbook_id: str = ""
    unit_id: str = "all"
    word_index: int = 0
    word_en: str = ""
    rounds: int = 1
    total_seconds: int = 0
    last_study_at: int | None = None
    updated_at: int = field(default=0)

    def public(self, book_titles: dict[str, str] | None = None) -> dict[str, Any]:
        titles = book_titles or {}
        return {
            "wordbookId": self.wordbook_id,
            "wordbookTitle": titles.get(self.wordbook_id, ""),
            "unitId": self.unit_id,
            "wordIndex": self.word_index,
            "wordEn": self.word_en,
            "rounds": self.rounds,
            "totalSeconds": self.total_seconds,
            "lastStudyAt": self.last_study_at,
        }


def _row_to_progress(row: sqlite3.Row) -> Progress:
    return Progress(
        user_id=int(row["user_id"]),
        wordbook_id=row["wordbook_id"] or "",
        unit_id=row["unit_id"] or "all",
        word_index=int(row["word_index"] or 0),
        word_en=row["word_en"] or "",
        rounds=int(row["rounds"] or 1),
        total_seconds=int(row["total_seconds"] or 0),
        last_study_at=row["last_study_at"],
        updated_at=int(row["updated_at"] or 0),
    )


def get_progress(db_path: Path, user_id: int) -> Progress | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM progress WHERE user_id = ?", (user_id,)
        ).fetchone()
    return _row_to_progress(row) if row else None


def record_progress(
    db_path: Path,
    user_id: int,
    *,
    wordbook_id: str,
    unit_id: str,
    word_index: int,
    word_en: str = "",
    rounds: int | None = None,
    seconds_delta: int = 0,
) -> Progress:
    """写入一次进度心跳；``seconds_delta`` 会被限制在合理区间。"""
    now = int(time.time())
    delta = max(0, min(MAX_SECONDS_PER_REPORT, int(seconds_delta or 0)))
    index = max(0, int(word_index or 0))
    round_value = max(1, int(rounds or 1))

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO progress
                (user_id, wordbook_id, unit_id, word_index, word_en, rounds,
                 total_seconds, last_study_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                wordbook_id   = excluded.wordbook_id,
                unit_id       = excluded.unit_id,
                word_index    = excluded.word_index,
                word_en       = excluded.word_en,
                rounds        = excluded.rounds,
                total_seconds = progress.total_seconds + excluded.total_seconds,
                last_study_at = excluded.last_study_at,
                updated_at    = excluded.updated_at
            """,
            (
                user_id,
                wordbook_id.strip()[:128],
                (unit_id or "all").strip()[:128],
                index,
                word_en.strip()[:128],
                round_value,
                delta,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM progress WHERE user_id = ?", (user_id,)
        ).fetchone()
    return _row_to_progress(row)


def add_study_seconds(db_path: Path, user_id: int, seconds: int) -> int:
    """只累加时长，不改进度位置（用于仅统计时长的场景）。"""
    delta = max(0, min(MAX_SECONDS_PER_REPORT, int(seconds or 0)))
    if delta == 0:
        current = get_progress(db_path, user_id)
        return current.total_seconds if current else 0
    now = int(time.time())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO progress
                (user_id, wordbook_id, unit_id, word_index, word_en, rounds,
                 total_seconds, last_study_at, updated_at)
            VALUES (?, '', 'all', 0, '', 1, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                total_seconds = progress.total_seconds + excluded.total_seconds,
                last_study_at = excluded.last_study_at,
                updated_at    = excluded.updated_at
            """,
            (user_id, delta, now, now),
        )
        row = conn.execute(
            "SELECT total_seconds FROM progress WHERE user_id = ?", (user_id,)
        ).fetchone()
    return int(row["total_seconds"])


__all__ = [
    "MAX_SECONDS_PER_REPORT",
    "Progress",
    "add_study_seconds",
    "get_progress",
    "record_progress",
]
