#!/usr/bin/env python3
"""把 Anki 牌组转换成 LittleFlash 的「卡片词书」。

用法::

    python scripts/import_word_formation.py                 # 自动找源文件
    python scripts/import_word_formation.py --source x.apkg
    python scripts/import_word_formation.py --source x.json -o out.json

支持三种来源，字段尽量对齐：

1. ``.apkg``：直接读压缩包里的 ``collection.anki2``（SQLite），
   牌组名形如 ``英语::语法填空::名词空::名词变形容词::-al/-ial::常规``，
   **任意层级的子牌组**都会保留下来；
2. 扁平导出 JSON（``meta`` / ``groups`` / ``cards``），本仓库
   ``Word Formation/语法填空-词性变换.json`` 就是这种；
3. 自带 ``decks`` 树的 JSON（``children`` 递归）或每张卡片自带
   ``deck`` 路径的 JSON。

输出给网页端的结构（``kind: cards``）：:

    {
      "id": "pep2019-word-formation",
      "kind": "cards",
      "cards": [
        {"en": "finance", "zh": "...", "front": "...", "back": "...",
         "note": "", "pos": "名词", "change": "名词变形容词",
         "affix": "-al/-ial", "type": "常规", "deck": "名词空::..."}
      ]
    }
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "wordbooks" / "pep2019-word-formation.json"

BOOK_ID = "pep2019-word-formation"
BOOK_TITLE = "词性转换 · 语法填空"
BOOK_STAGE = "senior"
BOOK_EDITION = "语法填空 · 词性变换"

DECK_SEP = "::"
TYPE_LABELS = {"常规", "特例"}
POS_SUFFIXES = ("空", "题")

_BLOCK_TAG_RE = re.compile(r"</?(div|p|li|ul|ol|h[1-6]|table|tr|td)\b[^>]*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t\u00a0]+")


# --------------------------------------------------------------- 文本清理
def html_to_text(value: str) -> str:
    """把 Anki 卡片 HTML 变成保留换行的纯文本。"""
    if not value:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = []
    for line in text.split("\n"):
        line = _SPACES_RE.sub(" ", line).strip()
        line = re.sub(r" {2,}", "  ", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def clean_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACES_RE.sub(" ", value).strip()


def split_head(line: str) -> tuple[str, str]:
    """把 ``finance  n.金融`` 拆成词条与词性释义。"""
    line = line.strip()
    if not line:
        return "", ""
    match = re.match(r"^([A-Za-z][A-Za-z'’.\- ]{0,60}?)\s{2,}(.+)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = re.match(r"^([A-Za-z][A-Za-z'’.\- ]*?)\s+((?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art)\.\s*.*)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return line.split(" ")[0], line


def deck_parts(name: str) -> list[str]:
    return [part.strip() for part in str(name or "").split(DECK_SEP) if part.strip()]


def strip_common_prefix(paths: Iterable[list[str]]) -> list[list[str]]:
    """去掉所有牌组共有的顶层前缀（例如 ``英语::语法填空``）。"""
    items = [list(path) for path in paths]
    if not items:
        return items
    common = list(items[0])
    for path in items[1:]:
        limit = min(len(common), len(path))
        index = 0
        while index < limit and common[index] == path[index]:
            index += 1
        common = common[:index]
        if not common:
            break
    # 至少给每张卡片留一层牌组名。
    depth = min(len(common), max(0, min(len(path) for path in items) - 1))
    return [path[depth:] for path in items]


def category_from_label(label: str) -> str:
    text = clean_line(label)
    for suffix in POS_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    if text.endswith("变形"):
        return text[: -len("变形")]
    return text


def meta_from_deck(parts: list[str]) -> dict[str, str]:
    """从牌组路径里推断 词性 / 变换方向 / 后缀 / 常规特例。"""
    cleaned = list(parts)
    type_label = ""
    if cleaned and cleaned[-1] in TYPE_LABELS:
        type_label = cleaned.pop()
    affix = cleaned[-1] if len(cleaned) >= 3 else ""
    change = cleaned[-2] if len(cleaned) >= 2 else ""
    category = category_from_label(cleaned[0]) if cleaned else ""
    return {
        "pos": category,
        "change": change,
        "affix": affix,
        "type": type_label,
    }


# --------------------------------------------------------------- 读取来源
def cards_from_json(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须是对象")

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    groups = {
        group.get("index"): group
        for group in payload.get("groups", [])
        if isinstance(group, dict)
    }

    entries: list[tuple[dict[str, Any], list[str]]] = []
    for raw in payload.get("cards", []):
        if not isinstance(raw, dict):
            continue
        group = groups.get(raw.get("group")) or {}
        deck = clean_line(raw.get("anki_deck") or group.get("anki_deck"))
        entries.append((raw, deck_parts(deck)))

    stripped = strip_common_prefix([parts for _, parts in entries])
    cards: list[dict[str, Any]] = []
    for (raw, _parts), parts in zip(entries, stripped):
        derived = meta_from_deck(parts)
        front = clean_line_block(raw.get("front"))
        back = clean_line_block(raw.get("back"))
        if not front or not back:
            continue
        base = clean_line(raw.get("base")) or split_head(front.split("\n")[0])[0]
        answer = clean_line(raw.get("answer")) or split_head(back.split("\n")[0])[0]
        cards.append(
            {
                "en": base,
                "answer": answer,
                "zh": back,
                "front": front,
                "back": back,
                "note": clean_line_block(raw.get("note")),
                "pos": clean_line(raw.get("category")) or derived["pos"],
                "change": clean_line(raw.get("change")) or derived["change"],
                "affix": clean_line(raw.get("suffix")) or derived["affix"],
                "type": clean_line(raw.get("type")) or derived["type"],
                "deck": DECK_SEP.join(parts),
            }
        )
    return cards, meta


def cards_from_nested_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容 ``decks`` 树（children 递归）或卡片自带路径的 JSON。"""
    if isinstance(payload.get("cards"), list):
        return cards_from_json_list(payload["cards"])

    entries: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], trail: list[str]) -> None:
        name = clean_line(node.get("name"))
        path = trail + ([name] if name else [])
        for card in node.get("cards", []) or []:
            if isinstance(card, dict):
                card = dict(card)
                card.setdefault("deck", DECK_SEP.join(path))
                entries.append(card)
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                walk(child, path)

    for root in payload.get("decks", []) or []:
        if isinstance(root, dict):
            walk(root, [])
    return cards_from_json_list(entries)


def cards_from_json_list(raw_cards: list[Any]) -> list[dict[str, Any]]:
    entries = [
        (raw, deck_parts(clean_line(raw.get("deck"))))
        for raw in raw_cards
        if isinstance(raw, dict)
    ]
    stripped = strip_common_prefix([parts for _, parts in entries])
    return [build_card(raw, parts) for (raw, _), parts in zip(entries, stripped)]


def build_card(raw: dict[str, Any], parts: list[str]) -> dict[str, Any]:
    derived = meta_from_deck(parts)
    front = clean_line_block(raw.get("front"))
    back = clean_line_block(raw.get("back"))
    return {
        "en": clean_line(raw.get("en") or raw.get("base")) or split_head(front.split("\n")[0])[0],
        "answer": clean_line(raw.get("answer")) or split_head(back.split("\n")[0])[0],
        "zh": back,
        "front": front,
        "back": back,
        "note": clean_line_block(raw.get("note")),
        "pos": clean_line(raw.get("pos") or raw.get("category")) or derived["pos"],
        "change": clean_line(raw.get("change")) or derived["change"],
        "affix": clean_line(raw.get("affix") or raw.get("suffix")) or derived["affix"],
        "type": clean_line(raw.get("type")) or derived["type"],
        "deck": DECK_SEP.join(parts),
    }


def cards_from_apkg(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        source = "collection.anki21" if "collection.anki21" in names else "collection.anki2"
        if source not in names:
            raise ValueError("压缩包里没有 collection.anki2")
        payload = archive.read(source)

    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "collection.db"
        database.write_bytes(payload)
        connection = sqlite3.connect(str(database))
        try:
            row = connection.execute("select models, decks from col").fetchone()
            models = json.loads(row[0] or "{}")
            decks = json.loads(row[1] or "{}")
            deck_names = {
                int(key): (value or {}).get("name", "")
                for key, value in decks.items()
                if str(key).isdigit()
            }
            field_names: dict[int, list[str]] = {}
            for key, model in models.items():
                fields = [field.get("name", "") for field in model.get("flds", [])]
                if key.isdigit():
                    field_names[int(key)] = fields
            rows = connection.execute(
                "select n.mid, n.flds, n.tags, c.did from notes n "
                "join cards c on c.nid = n.id order by n.id"
            ).fetchall()
        finally:
            connection.close()

    cards: list[dict[str, Any]] = []
    for mid, fields, tags, did in rows:
        names = field_names.get(int(mid)) or ["正面", "背面", "备注"]
        values = str(fields or "").split("\x1f")
        raw = {name: values[index] if index < len(values) else "" for index, name in enumerate(names)}
        front = next((value for name, value in raw.items() if "正" in name), "")
        back = next((value for name, value in raw.items() if "背" in name), "")
        note = next((value for name, value in raw.items() if "备注" in name or "note" in name.lower()), "")
        parts = deck_parts(deck_names.get(int(did), ""))
        cards.append(
            {
                "front": html_to_text(front),
                "back": html_to_text(back),
                "note": html_to_text(note),
                "_parts": parts,
            }
        )

    # 去掉所有牌组共有的顶层前缀（英语::语法填空）。
    stripped = strip_common_prefix([card["_parts"] for card in cards])
    result: list[dict[str, Any]] = []
    for card, parts in zip(cards, stripped):
        result.append(
            build_card(
                {
                    "front": card["front"],
                    "back": card["back"],
                    "note": card["note"],
                },
                parts,
            )
        )
    return result


def clean_line_block(value: Any) -> str:
    """多行文本：保留行内两个空格（词条与词性之间的排版间隙）。"""
    if not isinstance(value, str):
        return ""
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.replace("\t", " ").replace("\u00a0", " ")
        line = re.sub(r" {3,}", "  ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------- 输出
def dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for card in cards:
        if not card["front"] or not card["back"]:
            continue
        key = (card["front"], card["back"])
        if key in seen:
            continue
        seen.add(key)
        result.append(card)
    return result


def default_source() -> Path | None:
    candidates = [
        ROOT.parent.parent / "Word Formation" / "语法填空-词性变换.json",
        ROOT.parent.parent / "Word Formation" / "语法填空-词性变换.apkg",
        ROOT.parent / "Word Formation" / "语法填空-词性变换.json",
        ROOT.parent / "Word Formation" / "语法填空-词性变换.apkg",
        ROOT.parent / "语法填空-词性变换.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_cards(source: Path) -> tuple[list[dict[str, Any]], str]:
    suffix = source.suffix.lower()
    if suffix == ".apkg":
        return dedupe(cards_from_apkg(source)), "Anki .apkg"
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("cards"), list) and "meta" in payload:
        cards, meta = cards_from_json(source)
        note = str(meta.get("title") or "Anki JSON")
    elif isinstance(payload, dict):
        cards = cards_from_nested_json(payload)
        note = "Anki JSON（牌组树）"
    elif isinstance(payload, list):
        cards = cards_from_json_list(payload)
        note = "Anki JSON（卡片数组）"
    else:
        raise ValueError("无法识别的 JSON 结构")
    return dedupe(cards), note


def build_payload(cards: list[dict[str, Any]], source_note: str) -> dict[str, Any]:
    decks = {card["deck"] for card in cards}
    return {
        "id": BOOK_ID,
        "title": BOOK_TITLE,
        "stage": BOOK_STAGE,
        "edition": BOOK_EDITION,
        "source": f"{source_note} · {len(cards)} 张卡片 / {len(decks)} 个子牌组",
        "kind": "cards",
        "cards": cards,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 Anki 词性转换牌组")
    parser.add_argument("--source", type=Path, help="源文件（.apkg 或 .json）")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="输出词书 JSON")
    args = parser.parse_args()

    source = args.source or default_source()
    if source is None or not source.is_file():
        raise SystemExit("找不到源文件，请用 --source 指定 .apkg 或 .json")

    cards, note = load_cards(source)
    payload = build_payload(cards, note)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    types: dict[str, int] = {}
    pos: dict[str, int] = {}
    for card in cards:
        types[card["type"] or "未标注"] = types.get(card["type"] or "未标注", 0) + 1
        pos[card["pos"] or "未标注"] = pos.get(card["pos"] or "未标注", 0) + 1
    print(f"source: {source}")
    print(f"cards:  {len(cards)} · decks: {len({card['deck'] for card in cards})}")
    print(f"type:   {types}")
    print(f"pos:    {pos}")
    print(f"output: {args.output}")


if __name__ == "__main__":
    main()
