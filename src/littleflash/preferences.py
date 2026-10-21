"""Persistent study preferences.

These are intentionally separate from config.yml: they can be edited from the
password-protected settings page, while the YAML file remains the place for
server/deployment secrets.  Writes are atomic and file-locked so multiple
gunicorn workers cannot corrupt the JSON state.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .wordbooks import WordbookManager

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux is the supported deployment
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

MAX_STATE_BYTES = 64 * 1024

FRONTS = ("en", "zh")
ORDERS = ("sequence", "shuffle")
MODES = ("auto", "manual")
MIN_INTERVAL = 1.0
MAX_INTERVAL = 30.0


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


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()

    def load(self, manager: WordbookManager) -> dict[str, Any]:
        with self._thread_lock:
            raw = self._read_json()
            return public_preferences(raw, manager)

    def update(
        self,
        patch: Any,
        manager: WordbookManager,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock():
            current = public_preferences(self._read_json(), manager)
            updated = validate_update(patch, current, manager)
            self._write_json(updated)
            return updated

    def replace(
        self,
        value: dict[str, Any],
        manager: WordbookManager,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock():
            normalized = validate_update(value, default_preferences(manager), manager)
            self._write_json(normalized)
            return normalized

    def reset(self, manager: WordbookManager) -> dict[str, Any]:
        defaults = default_preferences(manager)
        if not manager.books():
            return defaults
        with self._thread_lock, self._file_lock():
            self._write_json(defaults)
        return defaults

    # ------------------------------------------------------------- internals
    def _read_json(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > MAX_STATE_BYTES:
                logger.warning("设置文件异常过大，已使用默认值：%s", self.path)
                return {}
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError) as exc:
            logger.warning("读取设置文件失败，已使用默认值：%s", exc)
            return {}

    def _write_json(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise SettingsValidationError(f"保存设置失败：{exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()


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
