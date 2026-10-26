from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from littleflash.app import create_app


def sample_wordbook(book_id: str, title: str = "Test Wordbook") -> dict[str, Any]:
    return {
        "id": book_id,
        "title": title,
        "stage": "senior",
        "edition": "TEST",
        "source": "pytest",
        "units": [
            {
                "id": "u1",
                "title": "Unit 1 · Greetings",
                "words": [
                    {"en": "apple", "zh": "n. 苹果", "phonetic": "/ˈæpəl/"},
                    {"en": "banana", "zh": "n. 香蕉", "phonetic": "/bəˈnɑːnə/"},
                ],
            },
            {
                "id": "u2",
                "title": "Unit 2 · Fruit",
                "words": [
                    {"en": "cherry", "zh": "n. 樱桃", "phonetic": "/ˈtʃeri/"},
                ],
            },
        ],
    }


def read_credentials(app) -> dict[str, str]:
    """读取首次建库时写入的初始账号文件。"""
    config = app.extensions["littleflash"].config
    path = config.db_path.parent / "initial-accounts.txt"
    creds: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, password = line.partition("\t")
        creds[name] = password
    return creds


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "CSRF meta tag missing"
    return match.group(1)


def login(
    client,
    username: str,
    password: str,
    *,
    page: str = "/login",
) -> dict[str, str]:
    token = extract_csrf(client.get(page).get_data(as_text=True))
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    return {"X-CSRF-Token": data["csrfToken"]}


@pytest.fixture()
def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """按需创建独立数据库的应用实例（每个测试互不干扰）。"""
    counter = {"n": 0}

    def factory(
        *,
        seed_accounts: int = 6,
        attempts: int = 5,
        lockout: int = 60,
        cookie_secure: bool = False,
        session_hours: float = 2,
        trust_proxy: bool = False,
    ):
        counter["n"] += 1
        home = tmp_path / f"home-{counter['n']}"
        wordbooks = home / "src" / "wordbooks"
        wordbooks.mkdir(parents=True)
        (wordbooks / "alpha.json").write_text(
            json.dumps(sample_wordbook("alpha", "Alpha Book")), encoding="utf-8"
        )
        (wordbooks / "beta.json").write_text(
            json.dumps(sample_wordbook("beta", "Beta Book")), encoding="utf-8"
        )

        monkeypatch.setenv("LITTLEFLASH_HOME", str(home))
        monkeypatch.setenv("LITTLEFLASH_DB", str(home / "data" / "littleflash.db"))
        monkeypatch.setenv("LITTLEFLASH_WORDBOOKS", str(wordbooks))
        monkeypatch.setenv("LITTLEFLASH_SEED_ACCOUNTS", str(seed_accounts))
        monkeypatch.setenv("LITTLEFLASH_TRUST_PROXY", "1" if trust_proxy else "0")
        monkeypatch.setenv("LITTLEFLASH_LOGIN_MAX_ATTEMPTS", str(attempts))
        monkeypatch.setenv("LITTLEFLASH_LOGIN_LOCKOUT", str(lockout))
        monkeypatch.setenv(
            "LITTLEFLASH_COOKIE_SECURE", "1" if cookie_secure else "0"
        )
        monkeypatch.setenv("LITTLEFLASH_SESSION_HOURS", str(session_hours))

        application = create_app()
        application.config.update(TESTING=True)
        return application

    return factory


@pytest.fixture()
def app(make_app):
    application = make_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def services(app):
    return app.extensions["littleflash"]


@pytest.fixture()
def creds(app) -> dict[str, str]:
    return read_credentials(app)


@pytest.fixture()
def admin_headers(client, creds) -> dict[str, str]:
    return login(client, "admin", creds["admin"])


@pytest.fixture()
def superadmin_headers(client, creds) -> dict[str, str]:
    return login(client, "telecomadmin", creds["telecomadmin"])


@pytest.fixture()
def user_headers(client) -> dict[str, str]:
    # 默认账号 240101 的密码与用户名相同
    return login(client, "240101", "240101")
