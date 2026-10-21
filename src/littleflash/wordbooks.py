"""Dynamic wordbook discovery and validation.

Every `*.json` file below the configured wordbook directory is picked up
automatically.  Files are parsed defensively: a broken or oversized file is
ignored with a warning instead of taking the whole site down.  Results are
cached and revalidated when the directory's file signature changes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

MAX_WORDS_PER_BOOK = 50_000
MAX_UNITS_PER_BOOK = 500
REFRESH_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Word:
    en: str
    zh: str
    phonetic: str = ""

    def as_api(self) -> dict[str, str]:
        return {"en": self.en, "zh": self.zh, "phonetic": self.phonetic}


@dataclass(frozen=True, slots=True)
class Unit:
    id: str
    title: str
    words: tuple[Word, ...]

    @property
    def word_count(self) -> int:
        return len(self.words)

    def as_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "wordCount": self.word_count,
            "words": [word.as_api() for word in self.words],
        }


@dataclass(frozen=True, slots=True)
class Wordbook:
    id: str
    title: str
    stage: str
    edition: str
    source: str
    path: Path
    units: tuple[Unit, ...]

    @property
    def word_count(self) -> int:
        return sum(unit.word_count for unit in self.units)

    @property
    def unit_count(self) -> int:
        return len(self.units)

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "stage": self.stage,
            "edition": self.edition,
            "source": self.source,
            "unitCount": self.unit_count,
            "wordCount": self.word_count,
            "units": [
                {
                    "id": unit.id,
                    "title": unit.title,
                    "wordCount": unit.word_count,
                }
                for unit in self.units
            ],
        }

    def as_api(self) -> dict[str, Any]:
        payload = self.metadata()
        payload["units"] = [unit.as_api() for unit in self.units]
        return payload

    def get_unit(self, unit_id: str) -> Unit | None:
        for unit in self.units:
            if unit.id == unit_id:
                return unit
        return None


class WordbookManager:
    """Thread-safe, lazily refreshed collection of local wordbooks."""

    def __init__(
        self,
        directory: Path,
        *,
        auto_reload: bool = True,
        max_file_bytes: int = 4 * 1024 * 1024,
        max_books: int = 100,
    ) -> None:
        self.directory = directory
        self.auto_reload = auto_reload
        self.max_file_bytes = max_file_bytes
        self.max_books = max_books

        self._lock = threading.RLock()
        self._books: tuple[Wordbook, ...] = ()
        self._by_id: dict[str, Wordbook] = {}
        self._audio_words: dict[str, str] = {}
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._last_check = 0.0
        self._loaded = False
        self.warnings: tuple[str, ...] = ()

    # ------------------------------------------------------------------ API
    def books(self) -> tuple[Wordbook, ...]:
        with self._lock:
            self._refresh()
            return self._books

    def metadata_list(self) -> list[dict[str, Any]]:
        return [book.metadata() for book in self.books()]

    def get(self, book_id: str) -> Wordbook | None:
        if not isinstance(book_id, str):
            return None
        with self._lock:
            self._refresh()
            return self._by_id.get(book_id)

    def first_book(self) -> Wordbook | None:
        books = self.books()
        return books[0] if books else None

    def canonical_audio_word(self, candidate: str) -> str | None:
        if not isinstance(candidate, str):
            return None
        candidate = _clean_text(candidate, 120)
        if not candidate:
            return None
        with self._lock:
            self._refresh()
            return self._audio_words.get(candidate.casefold())

    def revision(self) -> str:
        with self._lock:
            self._refresh()
            return "|".join(
                f"{path}:{mtime}:{size}" for path, mtime, size in (self._signature or ())
            )

    def reload_now(self) -> None:
        """Force a synchronous rescan (used by tests and admin tooling)."""
        with self._lock:
            self._rebuild()

    # ------------------------------------------------------------- internals
    def _refresh(self) -> None:
        now = time.monotonic()
        if self._loaded:
            if not self.auto_reload:
                return
            if now - self._last_check < REFRESH_INTERVAL_SECONDS:
                return
        self._last_check = now

        signature = self._scan_signature()
        if self._loaded and signature == self._signature:
            return
        self._rebuild(signature=signature)

    def _scan_signature(self) -> tuple[tuple[str, int, int], ...]:
        if not self.directory.exists():
            return ()
        entries: list[tuple[str, int, int]] = []
        try:
            candidates = sorted(
                path
                for path in self.directory.rglob("*.json")
                if path.is_file()
                and not path.is_symlink()
                and not any(part.startswith(".") for part in path.relative_to(self.directory).parts)
            )
        except OSError as exc:
            logger.warning("扫描词书目录失败：%s", exc)
            return ()

        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_mtime_ns, stat.st_size))
            if len(entries) > self.max_books:
                break
        return tuple(entries)

    def _rebuild(
        self,
        *,
        signature: tuple[tuple[str, int, int], ...] | None = None,
    ) -> None:
        if signature is None:
            signature = self._scan_signature()

        warnings: list[str] = []
        if not self.directory.exists():
            warnings.append(f"词书目录不存在：{self.directory}")
        books: list[Wordbook] = []
        seen_ids: set[str] = set()

        for path_str, _mtime, size in signature[: self.max_books]:
            path = Path(path_str)
            if size > self.max_file_bytes:
                warning = (
                    f"忽略过大的词书 {path.name}"
                    f"（{size} B，上限 {self.max_file_bytes} B）"
                )
                logger.warning(warning)
                warnings.append(warning)
                continue
            try:
                book = _load_wordbook(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                warning = f"忽略无效词书 {path.name}：{exc}"
                logger.warning(warning)
                warnings.append(warning)
                continue
            unique_id = book.id
            suffix = 2
            while unique_id in seen_ids:
                unique_id = f"{book.id}-{suffix}"
                suffix += 1
            if unique_id != book.id:
                book = replace(book, id=unique_id)
            seen_ids.add(book.id)
            books.append(book)

        if len(signature) > self.max_books:
            warning = f"词书数量超过 {self.max_books}，其余文件已忽略"
            logger.warning(warning)
            warnings.append(warning)

        audio_words: dict[str, str] = {}
        for book in books:
            for unit in book.units:
                for word in unit.words:
                    audio_words.setdefault(word.en.casefold(), word.en)

        books.sort(key=lambda item: item.path.name.casefold())
        self._books = tuple(books)
        self._by_id = {book.id: book for book in books}
        self._audio_words = audio_words
        self._signature = signature
        self._loaded = True
        self.warnings = tuple(warnings)

    def all_words(self) -> Iterable[str]:
        with self._lock:
            self._refresh()
            return tuple(self._audio_words.values())


# ---------------------------------------------------------------- parsing
def _load_wordbook(path: Path) -> Wordbook:
    raw_bytes = path.read_bytes()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是 UTF-8 编码") from exc
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("顶层必须是 JSON 对象")

    file_id = _make_id(payload.get("id"), fallback=path.stem)
    if not file_id:
        raise ValueError("无法生成词书 id")

    title = _clean_text(payload.get("title"), 160) or path.stem
    stage = _clean_text(payload.get("stage"), 80)
    edition = _clean_text(payload.get("edition"), 80)
    source = _clean_text(payload.get("source"), 240)

    raw_units = payload.get("units")
    if raw_units is None:
        raw_words = payload.get("words")
        if isinstance(raw_words, list):
            raw_units = [{"id": "all", "title": title, "words": raw_words}]
    if not isinstance(raw_units, list) or not raw_units:
        raise ValueError("缺少非空的 units 数组")

    units: list[Unit] = []
    seen_unit_ids: set[str] = set()
    total_words = 0
    for index, raw_unit in enumerate(raw_units[:MAX_UNITS_PER_BOOK], start=1):
        if not isinstance(raw_unit, dict):
            continue
        raw_words = raw_unit.get("words")
        if not isinstance(raw_words, list):
            continue
        unit_title = _clean_text(raw_unit.get("title"), 160) or f"Unit {index}"
        unit_id = _make_id(
            raw_unit.get("id"), fallback=f"unit-{index}", allow_leading_digit=True
        ) or f"unit-{index}"
        base_unit_id = unit_id
        suffix = 2
        while unit_id in seen_unit_ids:
            unit_id = f"{base_unit_id}-{suffix}"
            suffix += 1

        words: list[Word] = []
        for raw_word in raw_words:
            if total_words >= MAX_WORDS_PER_BOOK:
                break
            if not isinstance(raw_word, dict):
                continue
            en = _clean_text(raw_word.get("en"), 120)
            zh = _clean_text(raw_word.get("zh"), 800)
            if not en or not zh:
                continue
            phonetic = _clean_text(raw_word.get("phonetic"), 160)
            words.append(Word(en=en, zh=zh, phonetic=phonetic))
            total_words += 1
        if words:
            seen_unit_ids.add(unit_id)
            units.append(Unit(id=unit_id, title=unit_title, words=tuple(words)))
        if total_words >= MAX_WORDS_PER_BOOK:
            logger.warning("词书 %s 超过 %s 个单词，后续内容被忽略", path.name, MAX_WORDS_PER_BOOK)
            break

    if not units:
        raise ValueError("没有可用单词")

    return Wordbook(
        id=file_id,
        title=title,
        stage=stage,
        edition=edition,
        source=source,
        path=path,
        units=tuple(units),
    )


def _clean_text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    value = _CONTROL_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return value[:max_length]


def _make_id(value: Any, *, fallback: str, allow_leading_digit: bool = True) -> str:
    for candidate in (value, fallback):
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()
        if not candidate:
            continue
        if not allow_leading_digit:
            candidate = candidate.lstrip("._-")
        if _ID_RE.fullmatch(candidate):
            return candidate
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("._-")
        if slug:
            return slug[:80]
    return ""
