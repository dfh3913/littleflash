#!/usr/bin/env python3
"""构建无后端单文件 HTML。

产物：dist/littleflash-0.1.3-standalone.html
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "littleflash" / "static"
STANDALONE = ROOT / "standalone"
DIST = ROOT / "dist"
VERSION = "0.1.3"


def load_data() -> dict:
    books = []
    for path in sorted((ROOT / "src" / "wordbooks").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        book = {
            "i": raw.get("id") or path.stem,
            "t": raw.get("title") or path.stem,
            "u": [],
        }
        if raw.get("stage"):
            book["s"] = raw["stage"]
        if raw.get("edition"):
            book["e"] = raw["edition"]
        for unit in raw.get("units", []):
            entry = {
                "i": unit.get("id") or "unit",
                "t": unit.get("title") or "未命名单元",
                "w": [],
            }
            for word in unit.get("words", []):
                en = str(word.get("en") or "").strip()
                if not en:
                    continue
                entry["w"].append(
                    [en, str(word.get("zh") or ""), str(word.get("phonetic") or "")]
                )
            if entry["w"]:
                book["u"].append(entry)
        if book["u"]:
            books.append(book)
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
    output.write_text(html, encoding="utf-8")

    raw = output.stat().st_size
    gz = len(gzip.compress(html.encode("utf-8"), 9))
    print(f"data words: {sum(len(u['w']) for b in data['books'] for u in b['u'])}")
    print(f"output: {output}")
    print(f"size: {raw / 1024:.1f} KB (gzip ≈ {gz / 1024:.1f} KB)")
    return output


if __name__ == "__main__":
    build()
