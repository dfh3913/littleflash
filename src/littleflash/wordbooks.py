"""Dynamic wordbook discovery and validation.

Every `*.json` file below the configured wordbook directory is picked up
automatically.  Files are parsed defensively: a broken or oversized file is
ignored with a warning instead of taking the whole site down.  Results are
cached and revalidated when the directory's file signature changes.

0.1.4 起支持两种词书：

- ``kind: "words"``（默认）：普通词书，一个单词就是 ``en`` / ``zh`` 两面；
- ``kind: "cards"``：Anki 卡片书，每张卡自带 ``front`` / ``back``，
  导入时保留的嵌套牌组路径可按管理员设置重新分组（牌组层级 / 词性 / 后缀），
  也可以只显示常规或特例。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

MAX_WORDS_PER_BOOK = 50_000
MAX_UNITS_PER_BOOK = 500
REFRESH_INTERVAL_SECONDS = 1.0

BOOK_KIND_WORDS = "words"
BOOK_KIND_CARDS = "cards"

FORMATION_GROUPS = ("deck", "pos", "affix")
FORMATION_FILTERS = ("all", "regular", "special")
DEFAULT_FORMATION_VIEW: dict[str, Any] = {
    "enabled": True,
    "group": "deck",
    "filter": "all",
}

# 词性分组的展示顺序：先高频词性，再把其余类别按出现顺序接在后面。
POS_ORDER = ("名词", "形容词", "动词", "副词", "代词", "数词", "介词", "连词")


@dataclass(frozen=True, slots=True)
class Word:
    en: str
    zh: str
    phonetic: str = ""
    front: str = ""
    back: str = ""
    note: str = ""
    answer: str = ""
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def is_card(self) -> bool:
        return bool(self.front and self.back)

    @property
    def audio_answer(self) -> str:
        """背面要朗读的词：``taller / tallest`` 取第一个词。"""
        if not self.answer:
            return self.en
        first = re.split(r"[\s/,;]+", self.answer.strip(), maxsplit=1)
        return first[0] if first and first[0] else self.en

    def as_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "en": self.en,
            "zh": self.zh,
            "phonetic": self.phonetic,
        }
        if self.is_card:
            payload["kind"] = BOOK_KIND_CARDS
            payload["front"] = self.front
            payload["back"] = self.back
            payload["answer"] = self.audio_answer
            if self.note:
                payload["note"] = self.note
        return payload



@dataclass(frozen=True, slots=True)
class Unit:
    id: str
    title: str
    words: tuple[Word, ...]
    group: str = ""
    label: str = ""
    type: str = ""

    @property
    def word_count(self) -> int:
        return len(self.words)

    def metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "wordCount": self.word_count,
        }
        if self.group:
            payload["group"] = self.group
            payload["label"] = self.label or self.title
            payload["type"] = self.type
        return payload

    def as_api(self) -> dict[str, Any]:
        payload = self.metadata()
        payload["words"] = [word.as_api() for word in self.words]
        return payload


@dataclass(frozen=True, slots=True)
class Wordbook:
    id: str
    title: str
    stage: str
    edition: str
    source: str
    path: Path
    units: tuple[Unit, ...]
    kind: str = BOOK_KIND_WORDS

    @property
    def word_count(self) -> int:
        return sum(unit.word_count for unit in self.units)

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def is_card_book(self) -> bool:
        return self.kind == BOOK_KIND_CARDS

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "stage": self.stage,
            "edition": self.edition,
            "source": self.source,
            "kind": self.kind,
            "unitCount": self.unit_count,
            "wordCount": self.word_count,
            "units": [unit.metadata() for unit in self.units],
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
        formation_view: Mapping[str, Any] | None = None,
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
        self._formation_view = normalize_formation_view(formation_view)
        self.warnings: tuple[str, ...] = ()

    # ------------------------------------------------------------------ API
    def books(self) -> tuple[Wordbook, ...]:
        with self._lock:
            self._refresh()
            return self._books

    def formation_view(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._formation_view)

    def set_formation_view(self, view: Mapping[str, Any] | None) -> bool:
        """更新卡片书的展示方式；有变化时立即重建（返回是否发生变化）。"""
        normalized = normalize_formation_view(view)
        with self._lock:
            if normalized == self._formation_view:
                return False
            self._formation_view = normalized
            self._loaded = False
            self._rebuild()
            return True


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
            view = ",".join(
                f"{key}={self._formation_view[key]}" for key in sorted(self._formation_view)
            )
            return view + "#" + "|".join(
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
                book = _load_wordbook(path, formation_view=self._formation_view)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                warning = f"忽略无效词书 {path.name}：{exc}"
                logger.warning(warning)
                warnings.append(warning)
                continue
            if book.is_card_book and not self._formation_view.get("enabled", True):
                # 管理员关掉「词性转换」时整本书不参与选择与听写。
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
                    # 卡片书背面读的是变形后的词，也要放进发音白名单。
                    if word.is_card:
                        answer = word.audio_answer
                        if answer:
                            audio_words.setdefault(answer.casefold(), answer)

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
def normalize_formation_view(view: Mapping[str, Any] | None) -> dict[str, Any]:
    """把任意来源的展示设置清洗成 ``{enabled, group, filter}``。"""
    raw = view if isinstance(view, Mapping) else {}
    enabled = raw.get("enabled")
    group = raw.get("group")
    filter_mode = raw.get("filter")
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "group": group if group in FORMATION_GROUPS else DEFAULT_FORMATION_VIEW["group"],
        "filter": (
            filter_mode
            if filter_mode in FORMATION_FILTERS
            else DEFAULT_FORMATION_VIEW["filter"]
        ),
    }


def _load_wordbook(
    path: Path,
    *,
    formation_view: Mapping[str, Any] | None = None,
) -> Wordbook:
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

    kind = payload.get("kind")
    if kind == BOOK_KIND_CARDS or isinstance(payload.get("cards"), list):
        return _load_card_book(
            payload,
            path=path,
            book_id=file_id,
            title=title,
            stage=stage,
            edition=edition,
            source=source,
            view=normalize_formation_view(formation_view),
        )

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
            front = _clean_block(raw_word.get("front"), 600)
            back = _clean_block(raw_word.get("back"), 600)
            note = _clean_block(raw_word.get("note"), 300)
            words.append(
                Word(
                    en=en,
                    zh=zh,
                    phonetic=phonetic,
                    front=front,
                    back=back,
                    note=note,
                )
            )
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


def _load_card_book(
    payload: dict[str, Any],
    *,
    path: Path,
    book_id: str,
    title: str,
    stage: str,
    edition: str,
    source: str,
    view: dict[str, Any],
) -> Wordbook:
    """卡片书：每张卡带正/背面，按展示设置重新分组。"""
    raw_cards = payload.get("cards")
    cards: list[Word] = []
    if isinstance(raw_cards, list):
        for raw in raw_cards[:MAX_WORDS_PER_BOOK]:
            if not isinstance(raw, dict):
                continue
            front = _clean_block(raw.get("front"), 600)
            back = _clean_block(raw.get("back"), 600)
            if not front or not back:
                continue
            en = _clean_text(raw.get("en"), 120) or front.split("\n")[0].split(" ")[0]
            if not en:
                continue
            zh = _clean_block(raw.get("zh"), 800) or back
            meta = {
                "pos": _clean_text(raw.get("pos") or raw.get("category"), 40),
                "change": _clean_text(raw.get("change"), 60),
                "affix": _clean_text(raw.get("affix") or raw.get("suffix"), 60),
                "type": _clean_text(raw.get("type"), 20),
                "deck": _clean_text(raw.get("deck"), 240),
            }
            cards.append(
                Word(
                    en=en,
                    zh=zh,
                    front=front,
                    back=back,
                    note=_clean_block(raw.get("note"), 300),
                    answer=_clean_text(raw.get("answer"), 120),
                    meta=meta,
                )
            )
    elif isinstance(payload.get("units"), list):
        # 兼容把卡片塞进普通 units 的旧格式。
        for raw_unit in payload["units"][:MAX_UNITS_PER_BOOK]:
            if not isinstance(raw_unit, dict):
                continue
            deck = _clean_text(raw_unit.get("deck") or raw_unit.get("title"), 240)
            for raw in raw_unit.get("words") or raw_unit.get("cards") or []:
                if not isinstance(raw, dict):
                    continue
                front = _clean_block(raw.get("front"), 600)
                back = _clean_block(raw.get("back"), 600)
                if not front or not back:
                    continue
                en = _clean_text(raw.get("en"), 120) or front.split("\n")[0].split(" ")[0]
                if not en:
                    continue
                cards.append(
                    Word(
                        en=en,
                        zh=_clean_block(raw.get("zh"), 800) or back,
                        front=front,
                        back=back,
                        note=_clean_block(raw.get("note"), 300),
                        answer=_clean_text(raw.get("answer"), 120),
                        meta={
                            "pos": _clean_text(raw.get("pos"), 40),
                            "change": _clean_text(raw.get("change"), 60),
                            "affix": _clean_text(raw.get("affix") or raw.get("suffix"), 60),
                            "type": _clean_text(raw.get("type"), 20),
                            "deck": _clean_text(raw.get("deck"), 240) or deck,
                        },
                    )
                )

    cards = _filter_cards(cards, view["filter"])
    units = _group_cards(cards, view["group"])
    if not units:
        raise ValueError("没有可用卡片")

    return Wordbook(
        id=book_id,
        title=title,
        stage=stage,
        edition=edition,
        source=source,
        path=path,
        units=units,
        kind=BOOK_KIND_CARDS,
    )


def _filter_cards(cards: list[Word], filter_mode: str) -> list[Word]:
    if filter_mode == "regular":
        return [card for card in cards if card.meta.get("type") != "特例"]
    if filter_mode == "special":
        return [card for card in cards if card.meta.get("type") == "特例"]
    return cards


def _group_cards(cards: list[Word], group_mode: str) -> tuple[Unit, ...]:
    """按 牌组层级 / 词性 / 后缀 分组，组内保持原牌组顺序。"""
    if group_mode == "deck":
        return _deck_units(cards)

    buckets: dict[str, list[Word]] = {}
    for card in cards:
        buckets.setdefault(_card_group_title(card, group_mode), []).append(card)

    if group_mode == "pos":
        ordered = sorted(
            buckets.items(),
            key=lambda item: (
                POS_ORDER.index(item[0]) if item[0] in POS_ORDER else len(POS_ORDER),
                -len(item[1]),
            ),
        )
    elif group_mode == "affix":
        ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    else:
        ordered = list(buckets.items())

    prefix = group_mode if group_mode in {"pos", "affix"} else "unit"
    units: list[Unit] = []
    for index, (title, group_cards) in enumerate(ordered[:MAX_UNITS_PER_BOOK], start=1):
        units.append(
            Unit(
                id=f"{prefix}-{index:02d}",
                title=title,
                words=tuple(group_cards),
            )
        )
    return tuple(units)


def _deck_units(cards: list[Word]) -> tuple[Unit, ...]:
    """按牌组层级分组：大类 → 变换方向 →（常规 / 非常规）。

    规则（0.1.4）：

    - 一个大类下，如果所有变换方向都只有单一子牌组（只分常规或只分特例），
      这一层没有区分意义，整支归到上级，例如 ``代词变形::主格→反身::常规``
      会和其它代词小牌组一起归到「代词」；
    - 否则以「变换方向」为分组，例如 ``名词变形容词``、``形容词变副词``；
    - 同一方向里既有常规又有非常规（特例）时再拆成两组，
      例如「形容词变副词 · 常规」「形容词变副词 · 非常规」；
    - 分组只看全部卡片构成的牌组树，不受常规/特例筛选影响，切筛选时单元不会跳来跳去。
    """
    tree: dict[str, dict[str, set[str]]] = {}
    paths: list[list[str]] = []
    for card in cards:
        parts = [part for part in (card.meta.get("deck") or "").split("::") if part]
        paths.append(parts)
        if len(parts) >= 2:
            children = tree.setdefault(parts[0], {}).setdefault(parts[1], set())
            if len(parts) >= 3:
                children.add(parts[2])

    descend = {
        root: any(len(grandchildren) >= 2 for grandchildren in child_map.values())
        for root, child_map in tree.items()
    }

    buckets: dict[tuple[str, str, str], list[Word]] = {}
    for card, parts in zip(cards, paths):
        if not parts:
            key = ("未分组", "未分组", "")
        elif len(parts) == 1 or not descend.get(parts[0]):
            root = _root_deck_title(parts[0])
            key = (root, root, "")
        else:
            root = _root_deck_title(parts[0])
            label = parts[1]
            type_label = "非常规" if card.meta.get("type") == "特例" else "常规"
            key = (root, label, type_label)
        buckets.setdefault(key, []).append(card)

    # 同一方向只有一种变法时，标题里不重复写「常规」。
    variants: dict[tuple[str, str], set[str]] = {}
    for root, label, type_label in buckets:
        variants.setdefault((root, label), set()).add(type_label)

    ordered: dict[tuple[str, str, str], list[Word]] = {}
    for (root, label, type_label), group_cards in buckets.items():
        key = (root, label, "")
        if type_label and len(variants[(root, label)]) > 1:
            key = (root, label, type_label)
        ordered.setdefault(key, []).extend(group_cards)

    units: list[Unit] = []
    for index, ((root, label, type_label), group_cards) in enumerate(
        list(ordered.items())[:MAX_UNITS_PER_BOOK], start=1
    ):
        title = label if not type_label else f"{label} · {type_label}"
        units.append(
            Unit(
                id=f"deck-{index:02d}",
                title=title,
                words=tuple(group_cards),
                group=root,
                label=label,
                type=type_label,
            )
        )
    return tuple(units)


def _root_deck_title(name: str) -> str:
    """``代词变形`` 这样的上级牌组名去掉「变形」，更贴近选择时的说法。"""
    return name[: -len("变形")] if name.endswith("变形") else name


def _card_group_title(card: Word, group_mode: str) -> str:
    meta = card.meta
    if group_mode == "pos":
        return meta.get("pos") or "未标注词性"
    if group_mode == "affix":
        return meta.get("affix") or "无固定后缀"
    parts = [part for part in (meta.get("deck") or "").split("::") if part]
    if parts:
        return " › ".join(parts[:2])
    if meta.get("change"):
        return meta["change"]
    return "未分组"


def _clean_block(value: Any, max_length: int) -> str:
    """多行文本：保留换行与词条后的两个空格。"""
    if not isinstance(value, str):
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in value.split("\n"):
        line = _CONTROL_RE.sub("", line)
        line = line.replace("\t", " ").replace("\u00a0", " ")
        line = re.sub(r" {3,}", "  ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:max_length]


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
