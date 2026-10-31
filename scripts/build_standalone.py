#!/usr/bin/env python3
"""构建无后端单文件 HTML。

产物：dist/littleflash-<版本号>-standalone.html
- 词书压缩成紧凑数组（en/zh/phonetic）；
- 内联 base.css + home.css + standalone.css（去掉外部字体，减少请求）；
- 首屏 HTML 在前、数据块在后，先把界面画出来再解析大 JSON；
- 图标以 64px PNG data URI 内嵌。
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "littleflash" / "static"
STANDALONE = ROOT / "standalone"
DIST = ROOT / "dist"

sys.path.insert(0, str(ROOT / "src"))
from littleflash.wordbooks import WordbookManager  # noqa: E402


def project_version() -> str:
    """版本号只从 pyproject.toml 读，避免和包版本走偏。"""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


VERSION = project_version()


def load_data() -> dict:
    """用后端的词书解析逻辑，保证单文件版和线上版分组一致。"""
    manager = WordbookManager(ROOT / "src" / "wordbooks")
    books = []
    for book in manager.books():
        entry = {"i": book.id, "t": book.title, "u": []}
        if book.stage:
            entry["s"] = book.stage
        if book.edition:
            entry["e"] = book.edition
        if book.is_card_book:
            entry["k"] = "cards"
        for unit in book.units:
            unit_entry = {"i": unit.id, "t": unit.title, "w": []}
            if unit.group:
                unit_entry["g"] = unit.group
                unit_entry["l"] = unit.label
                unit_entry["y"] = unit.type
            for word in unit.words:
                if word.is_card:
                    unit_entry["w"].append(
                        [
                            word.en,
                            word.zh,
                            "",
                            word.front,
                            word.back,
                            word.note,
                            word.answer,
                        ]
                    )
                else:
                    unit_entry["w"].append([word.en, word.zh, word.phonetic])
            if unit_entry["w"]:
                entry["u"].append(unit_entry)
        if entry["u"]:
            books.append(entry)
    return {"v": VERSION, "books": books}


def minify_css(css: str) -> str:
    css = re.sub(r"@font-face\s*\{[^}]*\}", "", css)
    css = re.sub(r"/\*[\s\S]*?\*/", "", css)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>])\s*", r"\1", css)
    return css.strip()


def build() -> Path:
    data = load_data()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")

    css_parts = [
        (STATIC / "css" / "base.css").read_text(encoding="utf-8"),
        (STATIC / "css" / "home.css").read_text(encoding="utf-8"),
        (STANDALONE / "standalone.css").read_text(encoding="utf-8"),
    ]
    css = minify_css("\n".join(css_parts))
    js = (STANDALONE / "app.js").read_text(encoding="utf-8")

    icon = base64.b64encode((STATIC / "favicon.png").read_bytes()).decode("ascii")
    favicon = f"data:image/png;base64,{icon}"

    template = (STANDALONE / "template.html").read_text(encoding="utf-8")
    html = (
        template.replace("{{CSS}}", css)
        .replace("{{DATA}}", payload)
        .replace("{{JS}}", js)
        .replace("{{FAVICON}}", favicon)
        .replace("{{VERSION}}", VERSION)
    )

    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"littleflash-{VERSION}-standalone.html"
    output.write_bytes(html.encode("utf-8"))

    raw = output.stat().st_size
    gz = len(gzip.compress(html.encode("utf-8"), 9))
    print(f"data words: {sum(len(u['w']) for b in data['books'] for u in b['u'])}")
    print(f"output: {output}")
    print(f"size: {raw / 1024:.1f} KB (gzip ≈ {gz / 1024:.1f} KB)")
    return output


if __name__ == "__main__":
    build()
