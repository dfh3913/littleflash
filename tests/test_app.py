from __future__ import annotations

import gzip
import json
import os
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from werkzeug.security import generate_password_hash

from littleflash.app import create_app
from littleflash.config import ConfigError, load_config

from conftest import PASSWORD, extract_csrf, login, sample_wordbook, write_config


def test_config_load_and_missing_password(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    config = load_config(path)
    assert config.port == 8000
    assert config.settings_password == PASSWORD
    assert config.preferences_path.name == "preferences.json"

    broken = write_config(tmp_path, password=None)
    with pytest.raises(ConfigError):
        load_config(broken)


def test_bootstrap_and_wordbook_are_public_and_complete(client) -> None:
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    data = bootstrap.get_json()
    assert data["ok"] is True
    assert [book["id"] for book in data["books"]] == ["alpha", "beta"]
    assert data["preferences"]["wordbook_id"] == "alpha"
    assert data["authenticated"] is False
    assert data["server"]["audioEnabled"] is True

    book = client.get("/api/wordbooks/alpha").get_json()
    assert book["book"]["title"] == "Alpha Book"
    assert book["book"]["wordCount"] == 3
    assert book["book"]["units"][0]["words"][0]["en"] == "apple"

    assert client.get("/api/wordbooks/missing").status_code == 404
    assert client.get("/api/does-not-exist").is_json


def test_wordbooks_are_discovered_dynamically(tmp_path: Path, services) -> None:
    directory = services.config.wordbooks_dir
    assert {book.id for book in services.wordbooks.books()} == {"alpha", "beta"}

    (directory / "nested").mkdir()
    (directory / "nested" / "gamma.json").write_text(
        json.dumps(
            {
                "title": "Gamma top-level words",
                "words": [{"en": "cherry", "zh": "n. 樱桃"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    services.wordbooks.reload_now()
    books = services.wordbooks.books()
    assert {book.id for book in books} == {"alpha", "beta", "gamma"}
    gamma = services.wordbooks.get("gamma")
    assert gamma is not None
    assert gamma.units[0].words[0].en == "cherry"

    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    (directory / "duplicate.json").write_text(
        json.dumps(sample_wordbook("alpha", "Duplicate")), encoding="utf-8"
    )
    services.wordbooks.reload_now()
    ids = [book.id for book in services.wordbooks.books()]
    assert ids.count("alpha") == 1
    assert "alpha-2" in ids
    assert any("broken.json" in warning for warning in services.wordbooks.warnings)


def test_symlinks_are_ignored(tmp_path: Path, services) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(sample_wordbook("outside")), encoding="utf-8")
    link = services.config.wordbooks_dir / "link.json"
    link.symlink_to(outside)
    services.wordbooks.reload_now()
    assert services.wordbooks.get("outside") is None


def test_login_protection_and_csrf(client) -> None:
    # A direct API call without first loading the settings page has no CSRF token.
    no_token = client.post("/api/auth/login", json={"password": PASSWORD})
    assert no_token.status_code == 403
    assert no_token.get_json()["code"] == "csrf_failed"

    page = client.get("/settings")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    # 未登录也可以打开并调整设置，只是保存到浏览器本机
    assert 'id="settingsForm"' in html
    assert "本地模式" in html
    assert 'name="settings-mode" content="local"' in html
    assert 'id="adminLogin"' in html
    assert PASSWORD not in html
    token = extract_csrf(html)

    wrong = client.post(
        "/api/auth/login",
        json={"password": "nope"},
        headers={"X-CSRF-Token": token},
    )
    assert wrong.status_code == 401
    assert wrong.get_json()["error"] == "密码不正确"

    good = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert good.status_code == 200
    assert good.get_json()["csrfToken"]

    authed = client.get("/settings")
    assert authed.status_code == 200
    authed_html = authed.get_data(as_text=True)
    assert 'id="settingsForm"' in authed_html
    assert 'name="settings-mode" content="server"' in authed_html
    assert "本地模式" not in authed_html
    assert 'id="adminLogin"' not in authed_html
    assert PASSWORD not in authed_html

    # Session cookie flags.
    cookie = good.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "littleflash_session" in cookie


def test_settings_write_requires_auth_csrf_and_valid_json(client) -> None:
    unauthenticated = client.put("/api/settings", json={"front": "zh"})
    assert unauthenticated.status_code == 401

    headers = login(client)
    no_csrf = client.put("/api/settings", json={"front": "zh"})
    assert no_csrf.status_code == 403

    wrong_content_type = client.put(
        "/api/settings",
        data="front=zh",
        content_type="text/plain",
        headers=headers,
    )
    assert wrong_content_type.status_code == 415

    updated = client.put(
        "/api/settings",
        json={
            "wordbook_id": "beta",
            "unit_id": "u1",
            "front": "zh",
            "order": "shuffle",
            "mode": "manual",
            "interval_seconds": 6.5,
            "audio_enabled": False,
            "audio_volume": 0.55,
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    prefs = updated.get_json()["preferences"]
    assert prefs["wordbook_id"] == "beta"
    assert prefs["front"] == "zh"
    assert prefs["interval_seconds"] == 6.5

    state_path = client.application.extensions["littleflash"].config.preferences_path
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert stored["wordbook_id"] == "beta"
    if os.name != "nt":
        assert state_path.stat().st_mode & 0o777 == 0o600

    invalid = client.put(
        "/api/settings",
        json={"interval_seconds": 99},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "invalid_settings"

    cross_origin = client.put(
        "/api/settings",
        json={"front": "en"},
        headers={**headers, "Origin": "https://evil.example"},
    )
    assert cross_origin.status_code == 403

    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert client.put("/api/settings", json={"front": "en"}).status_code == 401


def test_removed_wordbook_falls_back_gracefully(tmp_path: Path, client, services) -> None:
    headers = login(client)
    response = client.put(
        "/api/settings",
        json={
            "wordbook_id": "beta",
            "unit_id": "all",
            "front": "en",
            "order": "sequence",
            "mode": "auto",
            "interval_seconds": 4,
            "audio_enabled": True,
            "audio_volume": 0.8,
        },
        headers=headers,
    )
    assert response.status_code == 200

    (services.config.wordbooks_dir / "beta.json").unlink()
    services.wordbooks.reload_now()
    preferences = client.get("/api/bootstrap").get_json()["preferences"]
    assert preferences["wordbook_id"] == "alpha"
    assert preferences["unit_id"] == "all"


def test_login_rate_limit_and_retry_after(tmp_path: Path) -> None:
    path = write_config(tmp_path, attempts=2, lockout=60)
    app = create_app(str(path))
    app.config.update(TESTING=True)
    client = app.test_client()

    token = extract_csrf(client.get("/settings").get_data(as_text=True))
    first = client.post(
        "/api/auth/login",
        json={"password": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    second = client.post(
        "/api/auth/login",
        json={"password": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    locked = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert first.status_code == 401
    assert second.status_code == 429
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.get_json()["code"] == "rate_limited"


def test_hashed_password_support(tmp_path: Path) -> None:
    password_hash = generate_password_hash("a-different-password")
    path = write_config(tmp_path, password=None, password_hash=password_hash)
    app = create_app(str(path))
    app.config.update(TESTING=True)
    client = app.test_client()

    token = extract_csrf(client.get("/settings").get_data(as_text=True))
    wrong = client.post(
        "/api/auth/login",
        json={"password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert wrong.status_code == 401
    good = client.post(
        "/api/auth/login",
        json={"password": "a-different-password"},
        headers={"X-CSRF-Token": token},
    )
    assert good.status_code == 200


def test_unicode_plaintext_password(tmp_path: Path) -> None:
    path = write_config(tmp_path, password="闪词-密码-2026")
    app = create_app(str(path))
    app.config.update(TESTING=True)
    client = app.test_client()

    token = extract_csrf(client.get("/settings").get_data(as_text=True))
    good = client.post(
        "/api/auth/login",
        json={"password": "闪词-密码-2026"},
        headers={"X-CSRF-Token": token},
    )
    assert good.status_code == 200


def test_security_headers_and_cache_policy(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    csp = home.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert home.headers["X-Frame-Options"] == "DENY"
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert home.headers["Cache-Control"] == "no-store"
    assert "unsafe-inline" not in csp

    api = client.get("/api/health")
    assert api.headers["Cache-Control"] == "no-store"
    assert api.get_json()["books"] == 2


def test_audio_endpoint_allowlist_and_cache(client, services, monkeypatch) -> None:
    assert client.get("/api/audio?word=../../etc/passwd").status_code == 404
    assert client.get("/api/audio?word=not-in-book").status_code == 404
    assert client.get("/api/audio").status_code == 404

    calls: list[str] = []

    def fake_download(self, word: str, destination: Path) -> None:
        calls.append(word)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3" + b"\x00" * 600)

    monkeypatch.setattr(type(services.audio), "_download", fake_download)
    first = client.get("/api/audio?word=apple")
    assert first.status_code == 200
    assert first.content_type == "audio/mpeg"
    assert first.data.startswith(b"ID3")
    assert "max-age" in first.headers["Cache-Control"]
    assert calls == ["apple"]

    # Second request hits the on-disk cache.
    second = client.get("/api/audio?word=apple")
    assert second.status_code == 200
    assert calls == ["apple"]


def test_reset_restores_default_preferences(client) -> None:
    headers = login(client)
    response = client.put(
        "/api/settings",
        json={
            "wordbook_id": "beta",
            "unit_id": "u1",
            "front": "zh",
            "order": "shuffle",
            "mode": "manual",
            "interval_seconds": 9,
            "audio_enabled": False,
            "audio_volume": 0.3,
        },
        headers=headers,
    )
    assert response.status_code == 200
    reset = client.post("/api/settings/reset", headers=headers)
    assert reset.status_code == 200
    prefs = reset.get_json()["preferences"]
    assert prefs["wordbook_id"] == "alpha"
    assert prefs["front"] == "en"
    assert prefs["interval_seconds"] == 4.0
    assert prefs["audio_enabled"] is True


def test_error_page_and_api_not_found(client) -> None:
    page = client.get("/no-such-page")
    assert page.status_code == 404
    assert "这一页没有单词" in page.get_data(as_text=True)
    api = client.get("/api/no-such-endpoint")
    assert api.status_code == 404
    assert api.is_json
    assert api.get_json()["code"] == "not_found"


# --------------------------------------------------------- 0.1.3 新功能
def test_multi_book_selection_and_deck(client) -> None:
    headers = login(client)

    updated = client.put(
        "/api/settings",
        json={
            "selections": [
                {"wordbook_id": "alpha", "unit_ids": ["u1"]},
                {"wordbook_id": "beta", "unit_ids": ["all"]},
            ]
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    prefs = updated.get_json()["preferences"]
    assert len(prefs["selections"]) == 2
    assert prefs["wordbook_id"] == "alpha"
    assert prefs["unit_id"] == "u1"

    deck = client.get("/api/deck").get_json()
    assert deck["count"] == 4
    assert [item["en"] for item in deck["deck"]] == [
        "apple",
        "banana",
        "date",
        "elderberry",
    ]
    assert deck["deck"][0]["bookTitle"] == "Alpha Book"
    assert deck["deck"][-1]["unitTitle"] == "Unit 1 · Greetings"

    custom = client.get("/api/deck?s=alpha~u2|beta~u1").get_json()
    assert custom["count"] == 3
    assert [item["en"] for item in custom["deck"]] == ["cherry", "date", "elderberry"]
    assert custom["query"] == "alpha~u2|beta~u1"

    invalid = client.put(
        "/api/settings",
        json={"selections": [{"wordbook_id": "missing", "unit_ids": ["all"]}]},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "invalid_settings"


def test_seed_and_random_order(client) -> None:
    headers = login(client)

    updated = client.put(
        "/api/settings",
        json={"order": "random", "seed": "中文Seed-123"},
        headers=headers,
    ).get_json()["preferences"]
    assert updated["order"] == "random"
    assert updated["seed"] == "中文Seed-123"

    # 旧值 shuffle 自动迁移为 random
    legacy = client.put(
        "/api/settings", json={"order": "shuffle"}, headers=headers
    ).get_json()["preferences"]
    assert legacy["order"] == "random"

    # 空种子会自动生成一个
    regenerated = client.put(
        "/api/settings", json={"seed": "   "}, headers=headers
    ).get_json()["preferences"]
    assert regenerated["seed"]
    assert regenerated["seed"] != "中文Seed-123"

    long_seed = "字" * 100
    truncated = client.put(
        "/api/settings", json={"seed": long_seed}, headers=headers
    ).get_json()["preferences"]
    assert len(truncated["seed"]) == 64

    stored = client.get("/api/settings").get_json()["preferences"]
    assert stored["seed"] == truncated["seed"]


def test_preview_deck_etag_and_gzip(client) -> None:
    preview = client.get("/api/preview?book=alpha&unit=u2").get_json()
    assert preview["word"]["en"] == "cherry"
    assert preview["word"]["bookTitle"] == "Alpha Book"

    first = client.get("/api/deck?s=alpha~all")
    assert first.status_code == 200
    tag = first.headers["ETag"]
    assert "private" in first.headers["Cache-Control"]

    cached = client.get("/api/deck?s=alpha~all", headers={"If-None-Match": tag})
    assert cached.status_code == 304
    assert cached.get_data() == b""

    compressed = client.get(
        "/api/deck?s=alpha~all", headers={"Accept-Encoding": "gzip"}
    )
    assert compressed.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in compressed.headers.get("Vary", "")
    payload = json.loads(gzip.decompress(compressed.get_data()).decode("utf-8"))
    assert payload["count"] == 3


def test_preview_returns_batch_for_card_switch(client) -> None:
    batch = client.get("/api/preview?book=alpha&unit=all").get_json()
    assert [item["en"] for item in batch["words"]] == ["apple", "banana", "cherry"]
    # 兼容旧调用：word 仍然是第一张卡
    assert batch["word"] == batch["words"][0]

    limited = client.get("/api/preview?book=alpha&unit=all&limit=2").get_json()
    assert [item["en"] for item in limited["words"]] == ["apple", "banana"]

    clamped = client.get("/api/preview?book=alpha&unit=all&limit=999").get_json()
    assert len(clamped["words"]) == 3

    malformed = client.get("/api/preview?book=alpha&unit=all&limit=abc").get_json()
    assert len(malformed["words"]) == 3

    missing = client.get("/api/preview?book=missing").get_json()
    assert missing["word"] is None
    assert missing["words"] == []


def test_settings_preview_starts_paused_with_controls(client) -> None:
    page = client.get("/settings").get_data(as_text=True)
    for control in ("previewPrev", "previewPlay", "previewNext"):
        assert f'id="{control}"' in page
    assert 'id="previewBadge"' in page
    assert 'data-state="paused"' in page
    assert "已暂停" in page
    assert 'id="previewIndex"' in page
    # 播放按钮初始是未播放状态，暂停图标默认隐藏
    assert 'aria-pressed="false"' in page
    assert re.search(
        r'class="icon preview-control__icon-pause"[^>]*\shidden[\s>]', page
    )
    assert not re.search(
        r'class="icon preview-control__icon-play"[^>]*\shidden[\s>]', page
    )


def test_audio_prefetch_and_toggle(client, services, monkeypatch, tmp_path) -> None:
    headers = login(client)

    page = client.get("/settings").get_data(as_text=True)
    assert 'id="audioPrefetchToggle"' in page
    assert 'id="bookPicker"' in page
    assert 'id="seedInput"' in page

    disabled = client.put(
        "/api/settings", json={"audio_prefetch": False}, headers=headers
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["preferences"]["audio_prefetch"] is False

    invalid = client.put(
        "/api/settings", json={"audio_prefetch": "yes"}, headers=headers
    )
    assert invalid.status_code == 400

    from littleflash.audio import AudioStore

    cache_dir = tmp_path / "prefetch-cache"
    config = replace(
        services.config.audio,
        enabled=True,
        prefetch=True,
        cache_dir=cache_dir,
        cache_ttl_seconds=3600,
    )
    store = AudioStore(config, services.wordbooks)
    downloaded: list[str] = []

    def fake_download(self, word, destination):
        downloaded.append(word)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3" + b"\x00" * 128)

    monkeypatch.setattr(type(store), "_download", fake_download)
    queued = store.prefetch(["apple", "banana", "not-a-word"], limit=10)
    assert queued == 2

    deadline = time.time() + 5
    while time.time() < deadline:
        if store._prefetch_thread is None or not store._prefetch_thread.is_alive():
            break
        time.sleep(0.05)
    assert sorted(downloaded) == ["apple", "banana"]
    assert len(list(cache_dir.glob("*.mp3"))) == 2
    assert store.prefetch(["apple"]) == 0


def test_wordbooks_have_no_missing_translations() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "wordbooks"
    missing = []
    for path in root.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for unit in data.get("units", []):
            for word in unit.get("words", []):
                if word.get("zh") in {"无", "", "—", "-", "暂无"}:
                    missing.append(word.get("en"))
    assert missing == []
