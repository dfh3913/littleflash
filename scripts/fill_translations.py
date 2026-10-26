#!/usr/bin/env python3
"""补齐词书中 ``zh == "无"`` 的释义。

数据来源优先级：
1. 有道词典 jsonapi 的 ``ec`` 释义（最准确）；
2. 有道 ``web_trans`` 网络释义（专有名词通常在这里）；
3. Bing 词典页面 meta description 里的「网络释义」。

清洗规则：去 HTML、去重、单条义项 ≤ 28 字、总长度 ≤ 60 字、最多 3 条，
避免把词典里冗长的解释整段搬进闪卡。
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
WORDBOOKS = ROOT / "src" / "wordbooks"
CACHE_PATH = ROOT / "scripts" / ".translation-cache.json"

MISSING = {"无", "", "—", "-", "暂无"}
MAX_SENSE = 28
MAX_TOTAL = 60
MAX_SENSES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

CJK = re.compile(r"[\u4e00-\u9fff]")


def fetch_json(url: str, timeout: int = 12) -> dict | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://dict.youdao.com/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def fetch_text(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def clean_text(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    text = re.sub(r"\s+", " ", text).strip(" ;；,，.。:：")
    return text


def has_cjk(value: str) -> bool:
    return bool(CJK.search(value))


def trim_sense(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"^(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int)\.\s*", "", text, flags=re.I)
    if len(text) > MAX_SENSE:
        # 优先在标点处截断，避免把一句话拦腰截断
        cut = max(text.rfind("，", 0, MAX_SENSE), text.rfind(",", 0, MAX_SENSE))
        text = text[: cut if cut >= 8 else MAX_SENSE].strip(" ，,;；")
    return text


def add_unique(target: list[str], value: str, *, limit: int = MAX_SENSES) -> None:
    text = trim_sense(value)
    if not text or not has_cjk(text) or len(text) < 1:
        return
    for existing in target:
        if text in existing or existing in text:
            return
    if len("；".join(target + [text])) > MAX_TOTAL:
        return
    target.append(text)
    if len(target) >= limit:
        raise StopIteration


def from_youdao(word: str) -> tuple[list[str], str]:
    url = "https://dict.youdao.com/jsonapi?" + urllib.parse.urlencode({"q": word})
    data = fetch_json(url)
    if not data:
        return [], ""

    senses: list[str] = []
    try:
        entries = data.get("ec", {}).get("word", [])
        for entry in entries[:2]:
            for tr in entry.get("trs", []):
                for item in tr.get("tr", []):
                    for value in item.get("l", {}).get("i", []):
                        for part in re.split(r"[；;]", str(value)):
                            try:
                                add_unique(senses, part)
                            except StopIteration:
                                return senses, "youdao-ec"
    except Exception:
        pass
    if senses:
        return senses, "youdao-ec"

    try:
        for item in data.get("web_trans", {}).get("web-translation", [])[:4]:
            for tr in item.get("trans", [])[:4]:
                try:
                    add_unique(senses, str(tr.get("value", "")), limit=2)
                except StopIteration:
                    return senses, "youdao-web"
    except Exception:
        pass
    if senses:
        return senses, "youdao-web"

    fanyi = data.get("fanyi") or {}
    tran = fanyi.get("tran")
    if isinstance(tran, str):
        try:
            add_unique(senses, tran, limit=2)
        except StopIteration:
            pass
    return senses, "youdao-fanyi" if senses else ""


def from_bing(word: str) -> tuple[list[str], str]:
    url = "https://cn.bing.com/dict/search?q=" + urllib.parse.quote(word)
    page = fetch_text(url)
    if not page:
        return [], ""
    match = re.search(r'<meta name="description" content="([^"]+)"', page)
    if not match:
        return [], ""
    description = html.unescape(match.group(1))
    marker = "网络释义：" if "网络释义：" in description else "释义："
    if marker not in description:
        return [], ""
    body = description.split(marker, 1)[1]
    senses: list[str] = []
    for part in re.split(r"[；;]", body):
        try:
            add_unique(senses, part, limit=2)
        except StopIteration:
            break
    return senses, "bing" if senses else ""


def lookup(word: str) -> tuple[str, str]:
    for candidate in query_variants(word):
        senses, source = from_youdao(candidate)
        if senses:
            return "；".join(senses), source
        senses, source = from_bing(candidate)
        if senses:
            return "；".join(senses), source
    return "", ""


def query_variants(word: str) -> Iterable[str]:
    yield word
    stripped = re.sub(r"^(?:the|a|an)\s+", "", word, flags=re.I).strip()
    if stripped and stripped != word:
        yield stripped
    # 短语去掉末尾的介词/副词再试一次，例如 keep your eyes open for
    trimmed = re.sub(r"\s+(?:for|to|of|in|on|at|with|by|from)$", "", word, flags=re.I)
    if trimmed and trimmed != word:
        yield trimmed


def load_cache() -> dict[str, dict[str, str]]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_cache(cache: dict[str, dict[str, str]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    cache = load_cache()
    filled: list[tuple[str, str, str]] = []
    unresolved: list[str] = []
    total = 0

    for path in sorted(WORDBOOKS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for unit in data.get("units", []):
            for word in unit.get("words", []):
                if word.get("zh") not in MISSING:
                    continue
                total += 1
                en = str(word.get("en", "")).strip()
                entry = cache.get(en)
                if not entry:
                    translation, source = lookup(en)
                    entry = {"zh": translation, "source": source}
                    cache[en] = entry
                    time.sleep(0.35)
                if entry.get("zh"):
                    word["zh"] = entry["zh"]
                    changed = True
                    filled.append((en, entry["zh"], entry.get("source", "")))
                else:
                    unresolved.append(en)
        if changed:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"updated {path.name}")

    save_cache(cache)
    print(f"\n共 {total} 条缺失，已补 {len(filled)} 条，未解决 {len(unresolved)} 条")
    for en, zh, source in filled:
        print(f"  [{source}] {en} -> {zh}")
    if unresolved:
        print("\n未解决：")
        for en in unresolved:
            print("  -", en)


if __name__ == "__main__":
    main()
