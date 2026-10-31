"""Persistent study preferences.

These are intentionally separate from config.yml: they can be edited from the
password-protected settings page, while the YAML file remains the place for
server/deployment secrets.  Writes are atomic and file-locked so multiple
gunicorn workers cannot corrupt the JSON state.

0.1.4 起支持：
- ``formation_*``：卡片书（词性转换）是否显示、按什么维度分组、是否只看常规/特例；
- ``cache_interval_seconds`` / ``cache_limit``：后台发音预缓存的节奏与上限；
- 其余仍是 0.1.3 的 ``selections`` / ``seed`` / ``audio_prefetch``。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .wordbooks import (
    FORMATION_FILTERS,
    FORMATION_GROUPS,
    Unit,
    Wordbook,
    WordbookManager,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux is the supported deployment
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

MAX_STATE_BYTES = 64 * 1024

FRONTS = ("en", "zh")
ORDERS = ("sequence", "random")
MODES = ("auto", "manual")
MIN_INTERVAL = 1.0
MAX_INTERVAL = 30.0

MIN_FONT_SCALE = 0.8
MAX_FONT_SCALE = 1.8
DEFAULT_FONT_SCALE = 1.0

MIN_CACHE_INTERVAL = 0.2
MAX_CACHE_INTERVAL = 10.0
DEFAULT_CACHE_INTERVAL = 1.0
MIN_CACHE_LIMIT = 100
MAX_CACHE_LIMIT = 20000
DEFAULT_CACHE_LIMIT = 2000

MAX_SEED_LENGTH = 64
DEFAULT_SEED_LENGTH = 8
SEED_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_BOOKS = 50
MAX_UNITS_PER_BOOK = 200


class SettingsValidationError(ValueError):
    """A client-supplied preference value is invalid."""


def generate_seed(length: int = DEFAULT_SEED_LENGTH) -> str:
    """生成一串易读的随机种子（去掉 0/O/1/I 等混淆字符）。"""
    size = max(4, min(MAX_SEED_LENGTH, int(length)))
    return "".join(secrets.choice(SEED_ALPHABET) for _ in range(size))


def normalize_seed(value: Any, fallback: str | None = None) -> str:
    """清洗种子：只保留可打印字符，限制长度，空值回落到 fallback。"""
    if not isinstance(value, str):
        return fallback or generate_seed()
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    if not text:
        return fallback or generate_seed()
    return text[:MAX_SEED_LENGTH]


def normalize_selections(
    raw: Any,
    manager: WordbookManager,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """把任意来源的选择清洗成 ``[{"wordbook_id", "unit_ids"}]``。

    - 兼容旧的单本 ``wordbook_id`` / ``unit_id``；
    - 未知词书会被丢弃（strict=True 时抛错）；
    - 未知单元会被丢弃（strict=True 时抛错）；
    - 结果为空时回落到第一本词书的全部单元。
    """
    books = manager.books()
    if not books:
        return []
    by_id = {book.id: book for book in books}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    items = raw if isinstance(raw, list) else []
    if strict and isinstance(raw, list) and not raw:
        raise SettingsValidationError("请至少选择一本词书")

    if isinstance(raw, dict):
        nested = raw.get("selections")
        if isinstance(nested, list) and nested:
            items = nested
        else:
            # 向后兼容：旧版单本词书 + 单元
            legacy_book = raw.get("wordbook_id")
            legacy_unit = raw.get("unit_id")
            if isinstance(legacy_book, str):
                items = [
                    {
                        "wordbook_id": legacy_book,
                        "unit_ids": ["all"]
                        if legacy_unit in (None, "all")
                        else [legacy_unit],
                    }
                ]

    for item in items:
        if len(result) >= MAX_BOOKS:
            break
        if not isinstance(item, dict):
            if strict:
                raise SettingsValidationError("词书选择格式无效")
            continue
        book_id = item.get("wordbook_id")
        if not isinstance(book_id, str) or book_id not in by_id:
            if strict:
                raise SettingsValidationError("所选词书不存在")
            continue
        if book_id in seen:
            continue
        book = by_id[book_id]
        unit_ids = _normalize_unit_ids(item.get("unit_ids"), book, strict=strict)
        if not unit_ids:
            if strict:
                raise SettingsValidationError("请至少选择一个单元")
            continue
        seen.add(book_id)
        result.append({"wordbook_id": book_id, "unit_ids": unit_ids})

    if not result:
        first = books[0]
        result = [{"wordbook_id": first.id, "unit_ids": ["all"]}]
    return result


def _normalize_unit_ids(raw: Any, book: Wordbook, *, strict: bool) -> list[str]:
    if raw is None:
        return ["all"]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        if strict:
            raise SettingsValidationError("单元选择格式无效")
        return ["all"]

    ids: list[str] = []
    for value in raw[:MAX_UNITS_PER_BOOK]:
        if not isinstance(value, str):
            if strict:
                raise SettingsValidationError("单元参数无效")
            continue
        if value == "all":
            return ["all"]
        if book.get_unit(value) is None:
            if strict:
                raise SettingsValidationError("所选单元不属于该词书")
            continue
        if value not in ids:
            ids.append(value)
    return ids or ["all"]


def selection_units(
    manager: WordbookManager,
    selections: list[dict[str, Any]],
) -> list[tuple[Wordbook, Unit]]:
    """展开选择，返回去重后的 (词书, 单元) 列表。"""
    pairs: list[tuple[Wordbook, Unit]] = []
    seen: set[tuple[str, str]] = set()
    for selection in selections:
        book = manager.get(str(selection.get("wordbook_id", "")))
        if book is None:
            continue
        unit_ids = selection.get("unit_ids") or ["all"]
        if "all" in unit_ids:
            units: Iterable[Unit] = book.units
        else:
            units = (unit for unit in (book.get_unit(str(uid)) for uid in unit_ids) if unit)
        for unit in units:
            key = (book.id, unit.id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((book, unit))
    return pairs


def iter_selected_words(
    manager: WordbookManager,
    selections: list[dict[str, Any]],
) -> Iterator[tuple[Wordbook, Unit, Any]]:
    for book, unit in selection_units(manager, selections):
        for word in unit.words:
            yield book, unit, word


def selected_word_count(
    manager: WordbookManager,
    selections: list[dict[str, Any]],
) -> int:
    return sum(1 for _ in iter_selected_words(manager, selections))


def default_preferences(
    manager: WordbookManager,
    seed: str | None = None,
) -> dict[str, Any]:
    book = manager.first_book()
    selections = [{"wordbook_id": book.id, "unit_ids": ["all"]}] if book else []
    return {
        "selections": selections,
        "wordbook_id": book.id if book else "",
        "unit_id": "all",
        "front": "en",
        "order": "sequence",
        "seed": normalize_seed(seed),
        "mode": "auto",
        "interval_seconds": 4.0,
        "audio_enabled": True,
        "audio_volume": 0.8,
        "audio_prefetch": True,
        "font_scale": DEFAULT_FONT_SCALE,
        "formation_enabled": True,
        "formation_group": "deck",
        "formation_filter": "all",
        "cache_interval_seconds": DEFAULT_CACHE_INTERVAL,
        "cache_limit": DEFAULT_CACHE_LIMIT,
    }


def public_preferences(
    raw: Any,
    manager: WordbookManager,
    fallback_seed: str | None = None,
) -> dict[str, Any]:
    """Always return a complete, safe set of preferences."""
    defaults = default_preferences(manager, seed=fallback_seed)
    if not isinstance(raw, dict):
        return defaults

    selections = normalize_selections(raw, manager)

    front = raw.get("front")
    if front not in FRONTS:
        front = defaults["front"]

    order = raw.get("order")
    if order == "shuffle":  # 0.1.2 及更早版本的旧值
        order = "random"
    if order not in ORDERS:
        order = defaults["order"]

    mode = raw.get("mode")
    if mode not in MODES:
        mode = defaults["mode"]

    seed = normalize_seed(raw.get("seed"), defaults["seed"])

    interval = _safe_float(raw.get("interval_seconds"), defaults["interval_seconds"])
    interval = min(MAX_INTERVAL, max(MIN_INTERVAL, round(interval * 2) / 2))

    audio_enabled = raw.get("audio_enabled")
    if not isinstance(audio_enabled, bool):
        audio_enabled = defaults["audio_enabled"]

    audio_prefetch = raw.get("audio_prefetch")
    if not isinstance(audio_prefetch, bool):
        audio_prefetch = defaults["audio_prefetch"]

    volume = _safe_float(raw.get("audio_volume"), defaults["audio_volume"])
    volume = min(1.0, max(0.0, round(volume, 2)))

    font_scale = _safe_float(raw.get("font_scale"), defaults["font_scale"])
    font_scale = round(min(MAX_FONT_SCALE, max(MIN_FONT_SCALE, font_scale)), 2)

    formation_enabled = raw.get("formation_enabled")
    if not isinstance(formation_enabled, bool):
        formation_enabled = defaults["formation_enabled"]

    formation_group = raw.get("formation_group")
    if formation_group not in FORMATION_GROUPS:
        formation_group = defaults["formation_group"]

    formation_filter = raw.get("formation_filter")
    if formation_filter not in FORMATION_FILTERS:
        formation_filter = defaults["formation_filter"]

    cache_interval = _safe_float(
        raw.get("cache_interval_seconds"), defaults["cache_interval_seconds"]
    )
    cache_interval = round(
        min(MAX_CACHE_INTERVAL, max(MIN_CACHE_INTERVAL, cache_interval)), 1
    )

    cache_limit = raw.get("cache_limit")
    if isinstance(cache_limit, bool) or not isinstance(cache_limit, int):
        cache_limit = defaults["cache_limit"]
    cache_limit = int(min(MAX_CACHE_LIMIT, max(MIN_CACHE_LIMIT, cache_limit)))

    first = selections[0] if selections else {"wordbook_id": "", "unit_ids": ["all"]}
    first_unit = "all" if "all" in first["unit_ids"] else first["unit_ids"][0]

    return {
        "selections": selections,
        "wordbook_id": first["wordbook_id"],
        "unit_id": first_unit,
        "front": front,
        "order": order,
        "seed": seed,
        "mode": mode,
        "interval_seconds": interval,
        "audio_enabled": audio_enabled,
        "audio_volume": volume,
        "audio_prefetch": audio_prefetch,
        "font_scale": font_scale,
        "formation_enabled": formation_enabled,
        "formation_group": formation_group,
        "formation_filter": formation_filter,
        "cache_interval_seconds": cache_interval,
        "cache_limit": cache_limit,
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

    merged = public_preferences(current, manager)

    if "selections" in patch:
        selections = normalize_selections(
            patch["selections"], manager, strict=True
        )
        if not selections:
            raise SettingsValidationError("请至少选择一本词书")
        merged["selections"] = selections
    elif "wordbook_id" in patch or "unit_id" in patch:
        # 兼容旧客户端：单本词书 + 单个单元
        book_id = patch.get("wordbook_id", merged["wordbook_id"])
        unit_id = patch.get("unit_id", merged["unit_id"])
        if not isinstance(book_id, str) or manager.get(book_id) is None:
            raise SettingsValidationError("所选词书不存在")
        temp = {
            "selections": [
                {"wordbook_id": book_id, "unit_ids": ["all"] if unit_id == "all" else [unit_id]}
            ]
        }
        merged["selections"] = normalize_selections(temp, manager, strict=True)

    for key, allowed in (("front", FRONTS), ("order", ORDERS), ("mode", MODES)):
        if key in patch:
            value = patch[key]
            if value == "shuffle" and key == "order":
                value = "random"
            if value not in allowed:
                raise SettingsValidationError(f"设置项 {key} 的值无效")
            merged[key] = value

    if "seed" in patch:
        value = normalize_seed(patch["seed"], "")
        merged["seed"] = value or generate_seed()

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

    if "audio_prefetch" in patch:
        if not isinstance(patch["audio_prefetch"], bool):
            raise SettingsValidationError("发音预缓存开关的值无效")
        merged["audio_prefetch"] = patch["audio_prefetch"]

    if "font_scale" in patch:
        value = patch["font_scale"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError("字号必须是数字")
        value = float(value)
        if not MIN_FONT_SCALE <= value <= MAX_FONT_SCALE:
            raise SettingsValidationError(
                f"字号必须在 {MIN_FONT_SCALE:g} 到 {MAX_FONT_SCALE:g} 之间"
            )
        merged["font_scale"] = round(value, 2)

    if "formation_enabled" in patch:
        if not isinstance(patch["formation_enabled"], bool):
            raise SettingsValidationError("词性转换显示开关的值无效")
        merged["formation_enabled"] = patch["formation_enabled"]

    if "formation_group" in patch:
        value = patch["formation_group"]
        if value not in FORMATION_GROUPS:
            raise SettingsValidationError("词性转换的分组方式无效")
        merged["formation_group"] = value

    if "formation_filter" in patch:
        value = patch["formation_filter"]
        if value not in FORMATION_FILTERS:
            raise SettingsValidationError("词性转换的内容筛选无效")
        merged["formation_filter"] = value

    if "cache_interval_seconds" in patch:
        value = patch["cache_interval_seconds"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError("缓存间隔必须是数字")
        value = float(value)
        if not MIN_CACHE_INTERVAL <= value <= MAX_CACHE_INTERVAL:
            raise SettingsValidationError(
                f"缓存间隔必须在 {MIN_CACHE_INTERVAL:g} 到 {MAX_CACHE_INTERVAL:g} 秒之间"
            )
        merged["cache_interval_seconds"] = round(value, 1)

    if "cache_limit" in patch:
        value = patch["cache_limit"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError("缓存上限必须是整数")
        if not MIN_CACHE_LIMIT <= value <= MAX_CACHE_LIMIT:
            raise SettingsValidationError(
                f"缓存上限必须在 {MIN_CACHE_LIMIT} 到 {MAX_CACHE_LIMIT} 个之间"
            )
        merged["cache_limit"] = value

    return public_preferences(merged, manager)


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()
        self._fallback_seed = generate_seed()

    def load(self, manager: WordbookManager) -> dict[str, Any]:
        with self._thread_lock:
            raw = self._read_json()
            return public_preferences(raw, manager, self._fallback_seed)

    def update(
        self,
        patch: Any,
        manager: WordbookManager,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock():
            current = public_preferences(
                self._read_json(), manager, self._fallback_seed
            )
            updated = validate_update(patch, current, manager)
            self._write_json(updated)
            return updated

    def replace(
        self,
        value: dict[str, Any],
        manager: WordbookManager,
    ) -> dict[str, Any]:
        with self._thread_lock, self._file_lock():
            normalized = validate_update(
                value,
                default_preferences(manager, self._fallback_seed),
                manager,
            )
            self._write_json(normalized)
            return normalized

    def reset(self, manager: WordbookManager) -> dict[str, Any]:
        defaults = default_preferences(manager, generate_seed())
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
    selections = preferences.get("selections")
    if not isinstance(selections, list):
        selections = normalize_selections(preferences, manager)
    for book, unit in selection_units(manager, selections):
        if unit.words:
            word = unit.words[0]
            sample = {
                "en": word.en,
                "zh": word.zh,
                "phonetic": word.phonetic,
                "bookTitle": book.title,
                "unitTitle": unit.title,
            }
            if word.is_card:
                sample["kind"] = "cards"
                sample["front"] = word.front
                sample["back"] = word.back
                sample["note"] = word.note
            return sample
    return None


def _safe_float(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)
