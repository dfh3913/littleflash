from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from littleflash.app import create_app

PASSWORD = "test-settings-password"


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
                    {
                        "en": "apple",
                        "zh": "n. 苹果",
                        "phonetic": "/ˈæpəl/",
                    },
                    {
                        "en": "banana",
                        "zh": "n. 香蕉",
                        "phonetic": "/bəˈnɑːnə/",
                    },
                ],
            }
        ],
    }


def write_config(
    tmp_path: Path,
    *,
    password: str | None = PASSWORD,
    password_hash: str | None = None,
    attempts: int = 5,
    lockout: int = 60,
    cookie_secure: bool = False,
    max_file_size_kb: int = 4096,
) -> Path:
    wordbooks = tmp_path / "wordbooks"
    wordbooks.mkdir(exist_ok=True)
    (wordbooks / "alpha.json").write_text(
        json.dumps(sample_wordbook("alpha", "Alpha Book")), encoding="utf-8"
    )
    (wordbooks / "beta.json").write_text(
        json.dumps(sample_wordbook("beta", "Beta Book")), encoding="utf-8"
    )
    settings: dict[str, Any] = {
        "state_file": "data/preferences.json",
    }
    if password is not None:
        settings["password"] = password
    if password_hash is not None:
        settings["password_hash"] = password_hash

    data = {
        "app": {
            "host": "127.0.0.1",
            "port": 8000,
            "session_hours": 2,
            "debug": False,
        },
        "wordbooks": {
            "directory": str(wordbooks),
            "auto_reload": True,
            "max_file_size_kb": max_file_size_kb,
            "max_books": 20,
        },
        "settings": settings,
        "audio": {
            "enabled": True,
            "type": 2,
            "timeout_seconds": 2,
            "max_bytes": 1048576,
            "cache_dir": str(tmp_path / "audio-cache"),
            "cache_ttl_hours": 24,
        },
        "security": {
            "trust_proxy_headers": False,
            "cookie_secure": cookie_secure,
            "login_max_attempts": attempts,
            "login_window_seconds": 600,
            "login_lockout_seconds": lockout,
        },
    }
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def app(tmp_path: Path):
    config_path = write_config(tmp_path)
    application = create_app(str(config_path))
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def services(app):
    return app.extensions["littleflash"]


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "CSRF meta tag missing"
    return match.group(1)


def login(client, password: str = PASSWORD) -> dict[str, str]:
    page = client.get("/settings")
    token = extract_csrf(page.get_data(as_text=True))
    response = client.post(
        "/api/auth/login",
        json={"password": password},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    return {
        "X-CSRF-Token": data["csrfToken"],
    }
