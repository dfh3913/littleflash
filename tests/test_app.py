from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from littleflash.app import create_app
from littleflash.config import load_config
from littleflash.db import default_usernames

from conftest import extract_csrf, login, read_credentials, sample_wordbook


# --------------------------------------------------------------- 账号初始化
def test_default_usernames_cover_240101_to_240855() -> None:
    names = default_usernames()
    assert len(names) == 440
    assert names[0] == "240101"
    assert names[54] == "240155"
    assert names[55] == "240201"
    assert names[-1] == "240855"
    assert len(set(names)) == 440


def test_database_seeds_users_and_admin_credentials(app, services) -> None:
    from littleflash.users import count_by_role, find_user, list_users

    assert count_by_role(services.config.db_path, "user") == 6
    assert count_by_role(services.config.db_path, "admin") == 1
    assert count_by_role(services.config.db_path, "superadmin") == 1

    users = list_users(services.config.db_path)
    by_name = {user.username: user for user in users}
    assert by_name["240101"].role == "user"
    assert by_name["admin"].role == "admin"
    assert by_name["telecomadmin"].role == "superadmin"

    creds = read_credentials(app)
    assert set(creds) == {"admin", "telecomadmin"}
    assert len(creds["admin"]) == 8
    assert len(creds["telecomadmin"]) == 8
    assert creds["admin"].isalnum() and creds["telecomadmin"].isalnum()

    path = services.config.db_path.parent / "initial-accounts.txt"
    assert path.is_file()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert services.config.db_path.stat().st_mode & 0o777 == 0o600


def test_default_account_password_equals_username(client) -> None:
    headers = login(client, "240101", "240101")
    assert headers["X-CSRF-Token"]


def test_config_reads_sqlite_settings(make_app, monkeypatch) -> None:
    application = make_app()
    config = application.extensions["littleflash"].config
    assert config.port == 4570
    assert config.host == "127.0.0.1"
    assert config.db_path.name == "littleflash.db"
    assert config.secret_key

    monkeypatch.setenv("LITTLEFLASH_PORT", "5099")
    reloaded = load_config()
    assert reloaded.port == 5099


# ------------------------------------------------------------- 公开接口
def test_bootstrap_and_wordbook_are_public_and_complete(client) -> None:
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    data = bootstrap.get_json()
    assert data["ok"] is True
    assert [book["id"] for book in data["books"]] == ["alpha", "beta"]
    assert data["preferences"]["wordbook_id"] == "alpha"
    assert data["authenticated"] is False
    assert data["user"] is None
    assert data["progress"] is None
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
    assert {book.id for book in services.wordbooks.books()} == {
        "alpha",
        "beta",
        "gamma",
    }

    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    (directory / "duplicate.json").write_text(
        json.dumps(sample_wordbook("alpha", "Duplicate")), encoding="utf-8"
    )
    services.wordbooks.reload_now()
    ids = [book.id for book in services.wordbooks.books()]
    assert ids.count("alpha") == 1
    assert "alpha-2" in ids
    assert any("broken.json" in warning for warning in services.wordbooks.warnings)


# --------------------------------------------------------------- 登录/会话
def test_login_page_and_session_cookie(client, creds) -> None:
    page = client.get("/login")
    assert page.status_code == 200
    assert "登录闪词" in page.get_data(as_text=True)

    token = extract_csrf(page.get_data(as_text=True))
    wrong = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "nope"},
        headers={"X-CSRF-Token": token},
    )
    assert wrong.status_code == 401
    assert wrong.get_json()["code"] == "invalid_credentials"

    good = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "240101"},
        headers={"X-CSRF-Token": token},
    )
    assert good.status_code == 200
    body = good.get_json()
    assert body["user"]["username"] == "240101"
    assert body["redirect"] == "/"

    cookie = good.headers.get("Set-Cookie", "")
    assert "littleflash_session" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie

    settings_html = client.get("/settings").get_data(as_text=True)
    assert 'name="settings-mode" content="server"' in settings_html
    assert 'name="settings-scope" content="user"' in settings_html
    assert "已登录 · 设置会保存到你的账号" in settings_html
    assert "240101" in settings_html

    bootstrap = client.get("/api/bootstrap").get_json()
    assert bootstrap["authenticated"] is True
    assert bootstrap["user"]["username"] == "240101"

    logout = client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": body["csrfToken"]}
    )
    assert logout.status_code == 200
    assert client.get("/api/bootstrap").get_json()["authenticated"] is False


def test_login_requires_csrf_and_rate_limits(make_app) -> None:
    application = make_app(attempts=2)
    client = application.test_client()

    no_token = client.post(
        "/api/auth/login", json={"username": "240101", "password": "x"}
    )
    assert no_token.status_code == 403
    assert no_token.get_json()["code"] == "csrf_failed"

    token = extract_csrf(client.get("/login").get_data(as_text=True))
    first = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    second = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    locked = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "240101"},
        headers={"X-CSRF-Token": token},
    )
    assert first.status_code == 401
    assert second.status_code == 429
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.get_json()["code"] == "rate_limited"


def test_session_expiry_sends_user_back_to_login(client) -> None:
    login(client, "240101", "240101")
    with client.session_transaction() as session:
        session["auth_at"] = int(time.time()) - 10 * 24 * 3600

    response = client.get("/account")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with client.session_transaction() as session:
        assert "uid" not in session


def test_protected_pages_require_login(client) -> None:
    account = client.get("/account")
    assert account.status_code == 302
    assert "next=account_page" in account.headers["Location"]

    admin = client.get("/admin")
    assert admin.status_code == 302
    assert "next=admin_page" in admin.headers["Location"]


# ------------------------------------------------------------- 设置与隔离
def test_settings_are_saved_per_user(client) -> None:
    login(client, "240101", "240101")
    token = extract_csrf(client.get("/settings").get_data(as_text=True))

    updated = client.put(
        "/api/settings",
        json={"front": "zh", "order": "shuffle", "interval_seconds": 7},
        headers={"X-CSRF-Token": token},
    )
    assert updated.status_code == 200
    prefs = updated.get_json()["preferences"]
    assert prefs["front"] == "zh"
    assert prefs["order"] == "shuffle"

    # 另一个账号不受影响
    other = client.application.test_client()
    login(other, "240102", "240102")
    other_prefs = other.get("/api/bootstrap").get_json()["preferences"]
    assert other_prefs["front"] == "en"
    assert other_prefs["order"] == "sequence"

    # 未登录访客仍然拿到站点默认值
    anonymous = client.application.test_client()
    anon_prefs = anonymous.get("/api/bootstrap").get_json()["preferences"]
    assert anon_prefs["front"] == "en"


def test_anonymous_settings_write_requires_login(client) -> None:
    token = extract_csrf(client.get("/settings").get_data(as_text=True))
    response = client.put(
        "/api/settings", json={"front": "zh"}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 401
    assert response.get_json()["code"] == "auth_required"

    no_csrf = client.put("/api/settings", json={"front": "zh"})
    assert no_csrf.status_code == 403


def test_settings_validation_and_csrf(client) -> None:
    login(client, "240101", "240101")
    token = extract_csrf(client.get("/settings").get_data(as_text=True))

    wrong_type = client.put(
        "/api/settings",
        data="front=zh",
        content_type="text/plain",
        headers={"X-CSRF-Token": token},
    )
    assert wrong_type.status_code == 415

    invalid = client.put(
        "/api/settings", json={"interval_seconds": 99}, headers={"X-CSRF-Token": token}
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "invalid_settings"

    cross_origin = client.put(
        "/api/settings",
        json={"front": "en"},
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
    )
    assert cross_origin.status_code == 403

    reset = client.post(
        "/api/settings/reset", headers={"X-CSRF-Token": token}
    )
    assert reset.status_code == 200
    assert reset.get_json()["preferences"]["front"] == "en"


def test_superadmin_edits_anonymous_defaults(make_app) -> None:
    application = make_app()
    superadmin = application.test_client()
    creds = read_credentials(application)
    login(superadmin, "telecomadmin", creds["telecomadmin"])
    token = extract_csrf(superadmin.get("/settings?scope=site").get_data(as_text=True))

    page = superadmin.get("/settings?scope=site").get_data(as_text=True)
    assert 'name="settings-scope" content="site"' in page
    assert "站点默认设置" in page

    saved = superadmin.put(
        "/api/settings?scope=site",
        json={"front": "zh", "interval_seconds": 3},
        headers={"X-CSRF-Token": token},
    )
    assert saved.status_code == 200

    anonymous = application.test_client()
    prefs = anonymous.get("/api/bootstrap").get_json()["preferences"]
    assert prefs["front"] == "zh"
    assert prefs["interval_seconds"] == 3.0

    # 普通用户和普通管理员都不能改站点默认值
    user = application.test_client()
    login(user, "240101", "240101")
    user_token = extract_csrf(user.get("/settings").get_data(as_text=True))
    forbidden = user.put(
        "/api/settings?scope=site",
        json={"front": "en"},
        headers={"X-CSRF-Token": user_token},
    )
    assert forbidden.status_code == 403

    admin = application.test_client()
    login(admin, "admin", creds["admin"])
    admin_token = extract_csrf(admin.get("/settings").get_data(as_text=True))
    denied = admin.put(
        "/api/settings?scope=site",
        json={"front": "en"},
        headers={"X-CSRF-Token": admin_token},
    )
    assert denied.status_code == 403


# --------------------------------------------------------------- 学习进度
def test_progress_is_recorded_and_accumulated(client) -> None:
    login(client, "240101", "240101")
    token = extract_csrf(client.get("/").get_data(as_text=True))

    first = client.post(
        "/api/progress",
        json={
            "wordbook_id": "alpha",
            "unit_id": "all",
            "word_index": 1,
            "word_en": "banana",
            "rounds": 1,
            "seconds_delta": 40,
        },
        headers={"X-CSRF-Token": token},
    )
    assert first.status_code == 200
    assert first.get_json()["progress"]["totalSeconds"] == 40

    second = client.post(
        "/api/progress",
        json={
            "wordbook_id": "alpha",
            "unit_id": "all",
            "word_index": 2,
            "word_en": "cherry",
            "rounds": 2,
            "seconds_delta": 30,
        },
        headers={"X-CSRF-Token": token},
    )
    assert second.status_code == 200

    progress = client.get("/api/progress").get_json()["progress"]
    assert progress["totalSeconds"] == 70
    assert progress["wordIndex"] == 2
    assert progress["wordEn"] == "cherry"
    assert progress["rounds"] == 2
    assert progress["wordbookTitle"] == "Alpha Book"

    # 单次上报的时长被限制在 600 秒，越界索引被夹到词书范围内
    clamped = client.post(
        "/api/progress",
        json={
            "wordbook_id": "alpha",
            "unit_id": "all",
            "word_index": 999,
            "seconds_delta": 99999,
        },
        headers={"X-CSRF-Token": token},
    )
    assert clamped.status_code == 200
    saved = clamped.get_json()["progress"]
    assert saved["totalSeconds"] == 70 + 600
    assert saved["wordIndex"] == 2


def test_progress_requires_login_and_csrf(client) -> None:
    response = client.post(
        "/api/progress", json={"wordbook_id": "alpha", "seconds_delta": 10}
    )
    assert response.status_code == 401

    login(client, "240101", "240101")
    no_csrf = client.post(
        "/api/progress", json={"wordbook_id": "alpha", "seconds_delta": 10}
    )
    assert no_csrf.status_code == 403

    unknown = client.post(
        "/api/progress",
        json={"wordbook_id": "missing", "seconds_delta": 10},
        headers={"X-CSRF-Token": extract_csrf(client.get("/settings").get_data(as_text=True))},
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["code"] == "book_not_found"


# ----------------------------------------------------------- 用户中心/改密
def test_account_password_change(client) -> None:
    login(client, "240101", "240101")
    token = extract_csrf(client.get("/account").get_data(as_text=True))

    wrong = client.post(
        "/api/account/password",
        json={"current_password": "bad", "new_password": "newpass123"},
        headers={"X-CSRF-Token": token},
    )
    assert wrong.status_code == 400

    changed = client.post(
        "/api/account/password",
        json={"current_password": "240101", "new_password": "newpass123"},
        headers={"X-CSRF-Token": token},
    )
    assert changed.status_code == 200
    new_token = changed.get_json()["csrfToken"]
    assert new_token

    client.post("/api/auth/logout", headers={"X-CSRF-Token": new_token})

    old = client.post(
        "/api/auth/login",
        json={"username": "240101", "password": "240101"},
        headers={"X-CSRF-Token": extract_csrf(client.get("/login").get_data(as_text=True))},
    )
    assert old.status_code == 401

    relogin = login(client, "240101", "newpass123")
    assert relogin["X-CSRF-Token"]


# --------------------------------------------------------------- 管理员
def test_admin_lists_users_with_progress(make_app) -> None:
    application = make_app()
    user = application.test_client()
    login(user, "240101", "240101")
    token = extract_csrf(user.get("/").get_data(as_text=True))
    user.post(
        "/api/progress",
        json={
            "wordbook_id": "alpha",
            "unit_id": "u1",
            "word_index": 1,
            "word_en": "banana",
            "rounds": 1,
            "seconds_delta": 125,
        },
        headers={"X-CSRF-Token": token},
    )

    admin = application.test_client()
    login(admin, "admin", read_credentials(application)["admin"])
    response = admin.get("/api/admin/users")
    assert response.status_code == 200
    data = response.get_json()
    assert data["summary"]["total"] == 8
    assert data["summary"]["studied"] == 1
    assert data["summary"]["totalSeconds"] == 125

    rows = {row["username"]: row for row in data["users"]}
    assert rows["240101"]["wordEn"] == "banana"
    assert rows["240101"]["unitId"] == "u1"
    assert rows["240101"]["wordbookTitle"] == "Alpha Book"
    assert rows["admin"]["roleLabel"] == "管理员"
    assert rows["telecomadmin"]["role"] == "superadmin"

    page = admin.get("/admin").get_data(as_text=True)
    assert "用户管理" in page
    assert 'data-can-manage-roles="0"' in page

    superadmin = application.test_client()
    login(superadmin, "telecomadmin", read_credentials(application)["telecomadmin"])
    super_page = superadmin.get("/admin").get_data(as_text=True)
    assert 'data-can-manage-roles="1"' in super_page


def test_admin_can_create_accounts_and_reset_passwords(make_app) -> None:
    application = make_app()
    creds = read_credentials(application)

    admin = application.test_client()
    login(admin, "admin", creds["admin"])
    token = extract_csrf(admin.get("/admin").get_data(as_text=True))

    created = admin.post(
        "/api/admin/users",
        json={"username": "student01", "password": "secret123"},
        headers={"X-CSRF-Token": token},
    )
    assert created.status_code == 200, created.get_data(as_text=True)
    assert created.get_json()["user"]["role"] == "user"

    duplicate = admin.post(
        "/api/admin/users",
        json={"username": "student01", "password": "secret123"},
        headers={"X-CSRF-Token": token},
    )
    assert duplicate.status_code == 400

    cannot_create_admin = admin.post(
        "/api/admin/users",
        json={"username": "another-admin", "password": "secret123", "role": "admin"},
        headers={"X-CSRF-Token": token},
    )
    assert cannot_create_admin.status_code == 403

    rows = admin.get("/api/admin/users").get_json()["users"]
    ids = {row["username"]: row["id"] for row in rows}

    reset = admin.post(
        f"/api/admin/users/{ids['240102']}/password",
        json={},
        headers={"X-CSRF-Token": token},
    )
    assert reset.status_code == 200
    generated = reset.get_json()["generatedPassword"]
    assert generated and len(generated) == 8

    student = application.test_client()
    login(student, "240102", generated)
    assert student.get("/api/bootstrap").get_json()["authenticated"] is True

    superadmin = application.test_client()
    login(superadmin, "telecomadmin", creds["telecomadmin"])
    super_token = extract_csrf(superadmin.get("/admin").get_data(as_text=True))
    created_admin = superadmin.post(
        "/api/admin/users",
        json={"username": "helper", "password": "secret123", "role": "admin"},
        headers={"X-CSRF-Token": super_token},
    )
    assert created_admin.status_code == 200
    assert created_admin.get_json()["user"]["role"] == "admin"


def test_admin_cannot_reset_superadmin_password(make_app) -> None:
    application = make_app()
    creds = read_credentials(application)
    admin = application.test_client()
    login(admin, "admin", creds["admin"])
    token = extract_csrf(admin.get("/admin").get_data(as_text=True))

    rows = admin.get("/api/admin/users").get_json()["users"]
    by_name = {row["username"]: row["id"] for row in rows}

    denied = admin.post(
        f"/api/admin/users/{by_name['telecomadmin']}/password",
        json={"password": "hacked123"},
        headers={"X-CSRF-Token": token},
    )
    assert denied.status_code == 403

    # 管理员之间也不能互相改密，只有超级管理员可以
    other_admin = admin.post(
        f"/api/admin/users/{by_name['admin']}/password",
        json={"password": "hacked123"},
        headers={"X-CSRF-Token": token},
    )
    assert other_admin.status_code == 403

    # 普通用户仍然可以重置
    normal = admin.post(
        f"/api/admin/users/{by_name['240105']}/password",
        json={"password": "newpass123"},
        headers={"X-CSRF-Token": token},
    )
    assert normal.status_code == 200


def test_superadmin_can_disable_and_enable_users(make_app) -> None:
    application = make_app()
    creds = read_credentials(application)
    superadmin = application.test_client()
    login(superadmin, "telecomadmin", creds["telecomadmin"])
    token = extract_csrf(superadmin.get("/admin").get_data(as_text=True))

    rows = superadmin.get("/api/admin/users").get_json()["users"]
    by_name = {row["username"]: row["id"] for row in rows}

    disabled = superadmin.post(
        f"/api/admin/users/{by_name['240103']}/active",
        json={"active": False},
        headers={"X-CSRF-Token": token},
    )
    assert disabled.status_code == 200

    blocked = application.test_client()
    page_token = extract_csrf(blocked.get("/login").get_data(as_text=True))
    response = blocked.post(
        "/api/auth/login",
        json={"username": "240103", "password": "240103"},
        headers={"X-CSRF-Token": page_token},
    )
    assert response.status_code == 401

    enabled = superadmin.post(
        f"/api/admin/users/{by_name['240103']}/active",
        json={"active": True},
        headers={"X-CSRF-Token": token},
    )
    assert enabled.status_code == 200
    login(blocked, "240103", "240103")

    self_disable = superadmin.post(
        f"/api/admin/users/{by_name['telecomadmin']}/active",
        json={"active": False},
        headers={"X-CSRF-Token": token},
    )
    assert self_disable.status_code == 400


def test_normal_user_cannot_open_admin(make_app) -> None:
    application = make_app()
    user = application.test_client()
    login(user, "240101", "240101")

    page = user.get("/admin")
    assert page.status_code == 403
    assert "这里需要更高的权限" in page.get_data(as_text=True)

    api = user.get("/api/admin/users")
    assert api.status_code == 403
    assert api.get_json()["code"] == "forbidden"

    create = user.post(
        "/api/admin/users",
        json={"username": "nobody", "password": "secret123"},
        headers={"X-CSRF-Token": extract_csrf(user.get("/").get_data(as_text=True))},
    )
    assert create.status_code == 403


# ------------------------------------------------------------ 其他旧行为
def test_removed_wordbook_falls_back_gracefully(tmp_path: Path, client, services) -> None:
    login(client, "240101", "240101")
    token = extract_csrf(client.get("/settings").get_data(as_text=True))
    response = client.put(
        "/api/settings",
        json={"wordbook_id": "beta"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200

    (services.config.wordbooks_dir / "beta.json").unlink()
    services.wordbooks.reload_now()
    preferences = client.get("/api/bootstrap").get_json()["preferences"]
    assert preferences["wordbook_id"] == "alpha"
    assert preferences["unit_id"] == "all"


def test_security_headers_and_cache_policy(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    csp = home.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert home.headers["X-Frame-Options"] == "DENY"
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert home.headers["Cache-Control"] == "no-store"

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

    second = client.get("/api/audio?word=apple")
    assert second.status_code == 200
    assert calls == ["apple"]


def test_error_page_and_api_not_found(client) -> None:
    page = client.get("/no-such-page")
    assert page.status_code == 404
    assert "这一页没有单词" in page.get_data(as_text=True)
    api = client.get("/api/no-such-endpoint")
    assert api.status_code == 404
    assert api.is_json
    assert api.get_json()["code"] == "not_found"
