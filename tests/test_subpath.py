from __future__ import annotations

import json

from conftest import extract_csrf, login, read_credentials, sample_wordbook


def test_subpath_prefix_is_applied_to_assets_and_links(make_app, tmp_path):
    application = make_app(trust_proxy=True)
    client = application.test_client()

    response = client.get(
        "/settings", headers={"X-Forwarded-Prefix": "/littleflash"}
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="app-base" content="/littleflash"' in html
    assert 'src="/littleflash/static/js/common.js' in html
    assert 'href="/littleflash/static/css/settings.css' in html
    assert 'href="/littleflash/"' in html
    assert 'href="/littleflash/login"' in html

    # 不带前缀时回退到根路径
    plain = client.get("/settings").get_data(as_text=True)
    assert 'name="app-base" content=""' in plain
    assert 'src="/static/js/common.js' in plain
    assert 'href="/login"' in plain

    # 登录后的跳转地址也要带前缀
    creds = read_credentials(application)
    token = extract_csrf(
        client.get(
            "/login", headers={"X-Forwarded-Prefix": "/littleflash"}
        ).get_data(as_text=True)
    )
    logged_in = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": creds["admin"]},
        headers={
            "X-CSRF-Token": token,
            "X-Forwarded-Prefix": "/littleflash",
        },
    )
    assert logged_in.status_code == 200
    assert logged_in.get_json()["redirect"] == "/littleflash/"


def test_subpath_admin_page_links(make_app):
    application = make_app(trust_proxy=True)
    client = application.test_client()
    headers = login(client, "admin", read_credentials(application)["admin"])
    page = client.get(
        "/admin",
        headers={"X-Forwarded-Prefix": "/littleflash"},
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'src="/littleflash/static/js/account.js' in html
    assert 'href="/littleflash/"' in html
    assert headers  # 上一行的登录同时验证了会话可用
