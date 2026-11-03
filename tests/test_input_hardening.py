from __future__ import annotations

import logging

import pytest

from conftest import PASSWORD, extract_csrf, login
from littleflash.security import (
    MAX_PASSWORD_LENGTH,
    InputRejected,
    detect_threat,
    validate_password,
)


def csrf(client) -> str:
    return extract_csrf(client.get("/settings").get_data(as_text=True))


def post_login(client, password, token: str):
    return client.post(
        "/api/auth/login",
        json={"password": password},
        headers={"X-CSRF-Token": token},
    )


# ------------------------------------------------------------------ 规则本身
@pytest.mark.parametrize(
    "payload",
    [
        "' OR '1'='1",
        "admin'--",
        "'; DROP TABLE users; --",
        "1; DELETE FROM preferences",
        "union select password from users",
        "select * from sqlite_master",
        "' and sleep(5) --",
        "load_file('/etc/passwd')",
        "' union all select 1 from information_schema.tables",
    ],
)
def test_injection_patterns_are_detected(payload: str) -> None:
    assert detect_threat(payload) is not None


@pytest.mark.parametrize(
    "payload",
    [
        "P@ssw0rd!",
        "中文密码也可以",
        "O'Brien2026",
        "a-b_c.d~e",
        "correct horse battery staple",
        "littleflash-admin",
    ],
)
def test_normal_passwords_pass_through(payload: str) -> None:
    assert detect_threat(payload) is None
    assert validate_password(payload) == payload


def test_control_characters_and_length_are_rejected() -> None:
    with pytest.raises(InputRejected):
        validate_password("abc\x00def")
    with pytest.raises(InputRejected):
        validate_password("abc\x1bdef")
    with pytest.raises(InputRejected):
        validate_password("a" * (MAX_PASSWORD_LENGTH + 1))
    with pytest.raises(InputRejected):
        validate_password("")
    with pytest.raises(InputRejected):
        validate_password(None)


# ------------------------------------------------------------------ 登录接口
def test_login_rejects_sql_injection(client) -> None:
    token = csrf(client)
    response = post_login(client, "' OR '1'='1", token)
    assert response.status_code == 400
    assert response.get_json()["code"] == "suspicious_input"


def test_login_rejects_control_characters(client) -> None:
    token = csrf(client)
    response = post_login(client, "test\x00password", token)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_password"


def test_login_rejects_overlong_password(client) -> None:
    token = csrf(client)
    response = post_login(client, "x" * (MAX_PASSWORD_LENGTH + 20), token)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_password"


def test_login_still_accepts_benign_special_characters(client) -> None:
    token = csrf(client)
    response = post_login(client, "P@ssw0rd!#$%^&*()", token)
    # 只是密码不对，不应该被当成攻击载荷。
    assert response.status_code == 401
    assert response.get_json()["code"] == "invalid_password"


def test_correct_password_still_works_after_rejected_attempt(client) -> None:
    token = csrf(client)
    assert post_login(client, "'; DROP TABLE users; --", token).status_code == 400
    response = post_login(client, PASSWORD, token)
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_login_never_logs_the_password(client, caplog) -> None:
    token = csrf(client)
    payload = "'; DROP TABLE users; --"
    with caplog.at_level(logging.WARNING, logger="littleflash.app"):
        post_login(client, payload, token)
    assert "ddl_statement" in caplog.text
    assert payload not in caplog.text
    assert PASSWORD not in caplog.text


# -------------------------------------------------------------- 设置写入接口
def test_settings_reject_injection_payload(client) -> None:
    headers = login(client)
    before = client.get("/api/settings").get_json()["preferences"]

    response = client.put(
        "/api/settings",
        json={"seed": "1' OR '1'='1"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "suspicious_input"

    after = client.get("/api/settings").get_json()["preferences"]
    assert after["seed"] == before["seed"]
    assert after["selections"] == before["selections"]


def test_settings_accept_quotes_in_seed(client) -> None:
    headers = login(client)
    response = client.put(
        "/api/settings",
        json={"seed": "O'Brien2026"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()["preferences"]["seed"] == "O'Brien2026"
