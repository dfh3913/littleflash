from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from littleflash.wordbooks import WordbookManager

from conftest import login

REPO = Path(__file__).resolve().parents[1]

CARD_BOOK = {
    "id": "cards-test",
    "title": "Card Test",
    "stage": "senior",
    "edition": "Anki",
    "kind": "cards",
    "cards": [
        {
            "en": "finance",
            "answer": "financial",
            "front": "finance  n.金融\n变形容词",
            "back": "financial  adj.金融的\n-ce → -cial",
            "note": "a financial aid",
            "pos": "名词",
            "change": "名词变形容词",
            "affix": "-al/-ial",
            "type": "常规",
            "deck": "名词空::名词变形容词::-al/-ial::常规",
        },
        {
            "en": "luck",
            "answer": "lucky",
            "front": "luck  n.运气\n变形容词",
            "back": "lucky  adj.幸运的\n加 -y",
            "note": "",
            "pos": "名词",
            "change": "名词变形容词",
            "affix": "-y",
            "type": "特例",
            "deck": "名词空::名词变形容词::-y::特例",
        },
        {
            "en": "happy",
            "answer": "happiness",
            "front": "happy  adj.快乐的\n变名词",
            "back": "happiness  n.幸福\n加 -ness",
            "note": "",
            "pos": "形容词",
            "change": "形容词变名词",
            "affix": "-ness",
            "type": "特例",
            "deck": "形容词空::形容词变名词::-ness::常规",
        },
    ],
}


def add_card_book(services: Any) -> None:
    path = services.config.wordbooks_dir / "cards-test.json"
    path.write_text(json.dumps(CARD_BOOK, ensure_ascii=False), encoding="utf-8")
    services.wordbooks.reload_now()


# ------------------------------------------------------------------ 真实数据
def test_real_word_formation_book_sits_below_optional_4() -> None:
    manager = WordbookManager(REPO / "src" / "wordbooks")
    ids = [book.id for book in manager.books()]
    assert ids[-2:] == ["pep2019-optional-4", "pep2019-word-formation"]

    book = manager.get("pep2019-word-formation")
    assert book is not None
    assert book.is_card_book
    assert book.word_count == 320
    assert book.unit_count == 24


def test_real_word_formation_grouping_rules() -> None:
    manager = WordbookManager(REPO / "src" / "wordbooks")
    book = manager.get("pep2019-word-formation")
    units = {unit.title: unit for unit in book.units}

    # 只有单一子牌组的大类整支归到上级：代词 26 张卡不再拆成 6 组。
    assert "代词" in units
    assert units["代词"].word_count == 26
    assert not any(title.startswith("代词 ›") for title in units)

    # 同一个方向里既有常规又有非常规时拆开。
    assert "形容词变副词 · 常规" in units
    assert "形容词变副词 · 非常规" in units
    assert (
        units["形容词变副词 · 常规"].word_count
        + units["形容词变副词 · 非常规"].word_count
        == 34
    )

    # 只有一个变法的方向不重复标注类型。
    assert "名词变副词" in units
    assert "名词变副词 · 常规" not in units

    # 同名两条（常规 + 非常规）必须挨在一起，且顺序固定。
    titles = [unit.title for unit in book.units]
    assert titles[:4] == [
        "名词变形容词 · 常规",
        "名词变形容词 · 非常规",
        "名词变复数 · 常规",
        "名词变复数 · 非常规",
    ]
    for index, title in enumerate(titles):
        if title.endswith(" · 常规"):
            assert titles[index + 1] == title.replace(" · 常规", " · 非常规")


def test_real_cards_carry_front_back_and_answer() -> None:
    manager = WordbookManager(REPO / "src" / "wordbooks")
    book = manager.get("pep2019-word-formation")
    first = book.units[0].words[0]
    payload = first.as_api()
    assert payload["kind"] == "cards"
    assert payload["front"].startswith("finance")
    assert payload["back"].startswith("financial")
    assert payload["answer"] == "financial"
    # 背面发音要能在白名单里找到。
    assert manager.canonical_audio_word("financial") == "financial"


# --------------------------------------------------------------- 接口行为
def test_card_book_deck_exposes_both_faces(client, services) -> None:
    add_card_book(services)
    response = client.get("/api/deck?s=cards-test~all")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 3
    first = payload["deck"][0]
    assert first["kind"] == "cards"
    assert first["front"].startswith("finance")
    assert first["back"].startswith("financial")
    assert first["answer"] == "financial"
    # 名词空下有多个后缀方向，所以按「方向」分组并区分常规/非常规。
    assert first["unitTitle"] == "名词变形容词 · 常规"


def test_card_answers_are_allowed_for_audio(client, services) -> None:
    add_card_book(services)
    assert services.audio.manager.canonical_audio_word("happiness") == "happiness"
    assert services.audio.manager.canonical_audio_word("not-in-book") is None


def test_formation_display_settings_change_grouping(client, services) -> None:
    add_card_book(services)
    headers = login(client)

    response = client.put(
        "/api/settings",
        json={"formation_group": "pos"},
        headers=headers,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    book = services.wordbooks.get("cards-test")
    assert [unit.title for unit in book.units] == ["名词", "形容词"]

    response = client.put(
        "/api/settings",
        json={"formation_filter": "special"},
        headers=headers,
    )
    assert response.status_code == 200
    book = services.wordbooks.get("cards-test")
    assert book.word_count == 2
    assert book.units[0].words[0].en == "luck"

    response = client.put(
        "/api/settings",
        json={"formation_enabled": False},
        headers=headers,
    )
    assert response.status_code == 200
    assert services.wordbooks.get("cards-test") is None
    bootstrap = client.get("/api/bootstrap").get_json()
    assert all(book["id"] != "cards-test" for book in bootstrap["books"])


def test_font_scale_and_cache_defaults(client) -> None:
    prefs = client.get("/api/settings").get_json()["preferences"]
    assert prefs["font_scale"] == 1.0
    assert prefs["cache_interval_seconds"] == 1.0
    assert prefs["cache_limit"] == 2000
    assert prefs["formation_group"] == "deck"
    assert prefs["formation_filter"] == "all"
    assert prefs["formation_enabled"] is True


def test_cache_and_font_validation(client, services) -> None:
    headers = login(client)

    ok = client.put(
        "/api/settings",
        json={"cache_interval_seconds": 2.5, "cache_limit": 500, "font_scale": 1.25},
        headers=headers,
    )
    assert ok.status_code == 200
    prefs = ok.get_json()["preferences"]
    assert prefs["cache_interval_seconds"] == 2.5
    assert prefs["cache_limit"] == 500
    assert prefs["font_scale"] == 1.25
    assert services.audio.runtime()["cacheLimit"] == 500
    assert services.audio.runtime()["intervalSeconds"] == 2.5

    for bad in (
        {"cache_limit": 20},
        {"cache_limit": 99999},
        {"cache_interval_seconds": 0},
        {"cache_interval_seconds": 99},
        {"font_scale": 0.2},
        {"font_scale": 4},
        {"formation_group": "unknown"},
        {"formation_filter": "sometimes"},
    ):
        response = client.put("/api/settings", json=bad, headers=headers)
        assert response.status_code == 400, bad
