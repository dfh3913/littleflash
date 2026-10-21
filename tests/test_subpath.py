from __future__ import annotations

from pathlib import Path

import yaml

from littleflash.app import create_app

from conftest import write_config


def _trusting_app(tmp_path: Path):
    """构建一个开 trust_proxy_headers 的应用（等价于 nginx 反代场景）。"""
    config_path = write_config(tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["security"]["trust_proxy_headers"] = True
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    application = create_app(str(config_path))
    application.config.update(TESTING=True)
    return application


def test_subpath_deployment_uses_forwarded_prefix(tmp_path: Path) -> None:
    """部署在 /littleflash 子路径时，页面链接、静态资源与接口都要带前缀。"""
    app = _trusting_app(tmp_path)
    headers = {
        "X-Forwarded-Prefix": "/littleflash",
        "X-Forwarded-Proto": "https",
        "Host": "example.com",
    }
    client = app.test_client()

    home = client.get("/", headers=headers)
    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert 'href="/littleflash/static/css/base.css' in html
    assert 'name="app-base" content="/littleflash"' in html
    assert 'href="/littleflash/settings"' in html
    assert 'href="/littleflash/"' in html

    settings = client.get("/settings", headers=headers)
    assert settings.status_code == 200
    settings_html = settings.get_data(as_text=True)
    assert 'src="/littleflash/static/js/common.js' in settings_html
    assert 'action="/littleflash/api/auth/login"' in settings_html or "loginForm" in settings_html

    # 子路径下接口与静态资源依然按内部路径提供
    assert client.get("/api/bootstrap", headers=headers).status_code == 200
    assert (
        client.get("/static/css/base.css", headers=headers).status_code == 200
    )

    # 未带前缀头时回到根路径部署，保持向后兼容
    plain = app.test_client().get("/")
    assert plain.status_code == 200
    assert 'href="/static/css/base.css' in plain.get_data(as_text=True)
