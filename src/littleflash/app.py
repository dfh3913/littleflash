"""Flask application for LittleFlash 闪词."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__
from .audio import AudioError, AudioStore
from .config import AppConfig, load_config
from .preferences import SettingsStore, SettingsValidationError, sample_word
from .security import (
    LoginRateLimiter,
    csrf_equal,
    new_csrf_token,
    password_is_configured,
    verify_password,
)
from .wordbooks import WordbookManager

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        message: str,
        status: int = 400,
        *,
        code: str = "bad_request",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after


@dataclass(slots=True)
class Services:
    config: AppConfig
    wordbooks: WordbookManager
    preferences: SettingsStore
    audio: AudioStore
    login_limiter: LoginRateLimiter


def create_app(config_path: str | None = None) -> Flask:
    config = load_config(config_path)
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.config.update(
        SECRET_KEY=config.secret_key,
        SESSION_COOKIE_NAME="littleflash_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.security.cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(
            seconds=config.session_lifetime_seconds
        ),
        MAX_CONTENT_LENGTH=32 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=30),
        TEMPLATES_AUTO_RELOAD=config.debug,
    )
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    if config.security.trust_proxy_headers:
        # x_prefix 让应用可以在子路径（如 /littleflash）下正确生成静态资源、
        # 接口与跳转地址：nginx 需先剥掉前缀，再发送 X-Forwarded-Prefix。
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=1
        )

    wordbooks = WordbookManager(
        config.wordbooks_dir,
        auto_reload=config.wordbooks_auto_reload,
        max_file_bytes=config.wordbook_max_file_bytes,
        max_books=config.wordbook_max_books,
    )
    preferences = SettingsStore(config.preferences_path)
    audio = AudioStore(config.audio, wordbooks)
    limiter = LoginRateLimiter(
        config.security.login_max_attempts,
        config.security.login_window_seconds,
        config.security.login_lockout_seconds,
    )
    services = Services(config, wordbooks, preferences, audio, limiter)
    app.extensions["littleflash"] = services

    asset_version = _asset_version(app)
    configured_books = wordbooks.books()
    logger.info(
        "LittleFlash 已加载 %s 本词书（目录：%s）",
        len(configured_books),
        config.wordbooks_dir,
    )
    for warning in wordbooks.warnings:
        logger.warning(warning)
    try:
        config_mode = config.config_path.stat().st_mode & 0o777
        if config_mode & 0o077:
            logger.warning(
                "config.yml 权限为 %o，建议执行 chmod 600 config.yml，"
                "避免同机其他用户读取设置密码。",
                config_mode,
            )
    except OSError:
        pass
    if config.settings_password == "littleflash-admin":
        logger.warning(
            "正在使用默认设置页密码，请在部署前修改 config.yml 中的 "
            "settings.password！"
        )
    if not config.security.cookie_secure:
        logger.info(
            "Cookie 未启用 Secure 标记；若站点使用 HTTPS，请设置 "
            "security.cookie_secure: true。"
        )

    # ------------------------------------------------------------- helpers
    def now() -> int:
        return int(time.time())

    def is_authenticated() -> bool:
        if session.get("settings_authenticated") is not True:
            return False
        issued_at = session.get("auth_at")
        if not isinstance(issued_at, int):
            session.clear()
            return False
        if now() - issued_at > config.session_lifetime_seconds:
            session.clear()
            return False
        return True

    def ensure_csrf_token() -> str:
        token = session.get("_csrf")
        if not isinstance(token, str) or len(token) < 20:
            token = new_csrf_token()
            session["_csrf"] = token
        return token

    def csrf_header_valid() -> bool:
        expected = session.get("_csrf")
        supplied = request.headers.get("X-CSRF-Token")
        if not isinstance(expected, str):
            return False
        return csrf_equal(expected, supplied)

    def require_csrf() -> None:
        if not csrf_header_valid():
            raise ApiError(
                "安全令牌已失效，请刷新页面后重试",
                status=403,
                code="csrf_failed",
            )

    def require_settings_auth() -> None:
        if not is_authenticated():
            raise ApiError(
                "登录已过期，请重新输入设置密码",
                status=401,
                code="auth_required",
            )

    def protected_write(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            require_settings_auth()
            require_csrf()
            return fn(*args, **kwargs)

        return wrapper

    def read_json_body() -> dict[str, Any]:
        if not request.is_json:
            raise ApiError(
                "请求必须使用 application/json",
                status=415,
                code="json_required",
            )
        try:
            payload = request.get_json(silent=False)
        except Exception as exc:  # TypeError / BadRequest
            raise ApiError("无法解析 JSON 请求", 400, code="invalid_json") from exc
        if not isinstance(payload, dict):
            raise ApiError("请求内容必须是 JSON 对象", 400, code="invalid_json")
        return payload

    def client_key() -> str:
        return request.remote_addr or "unknown"

    def page_context(**extra: Any) -> dict[str, Any]:
        return {
            "asset_version": asset_version,
            "app_version": __version__,
            "current_year": time.localtime().tm_year,
            **extra,
        }

    # ---------------------------------------------------------- page routes
    @app.get("/")
    def index() -> str:
        return render_template("index.html", **page_context())

    @app.get("/settings")
    def settings_page() -> str:
        authenticated = is_authenticated()
        csrf_token = ensure_csrf_token()
        books = wordbooks.metadata_list()
        # 设置页对所有访客开放：未登录时表单读写浏览器本地存储，不会回写服务器；
        # 因此这里下发的只是与 /api/bootstrap 相同的公开默认值。
        prefs = preferences.load(wordbooks)

        selected_book = wordbooks.get(str(prefs["wordbook_id"]))
        if selected_book is None:
            selected_book = wordbooks.first_book()
        selected_book_meta = selected_book.metadata() if selected_book is not None else None
        units: list[dict[str, Any]] = (
            selected_book_meta["units"] if selected_book_meta else []
        )
        preview = sample_word(prefs, wordbooks)

        return render_template(
            "settings.html",
            **page_context(
                authenticated=authenticated,
                local_mode=not authenticated,
                csrf_token=csrf_token,
                books=books,
                preferences=prefs,
                selected_book=selected_book_meta,
                selected_book_id=selected_book.id if selected_book else "",
                units=units,
                preview=preview,
            ),
        )

    # --------------------------------------------------------------- API
    @app.get("/api/bootstrap")
    def api_bootstrap() -> Response:
        return jsonify(
            ok=True,
            # 未登录的访客会用浏览器本地设置覆盖下面的公开默认值。
            authenticated=is_authenticated(),
            books=wordbooks.metadata_list(),
            preferences=preferences.load(wordbooks),
            server={
                "audioEnabled": config.audio.enabled,
                "version": __version__,
            },
        )

    @app.get("/api/settings")
    def api_get_settings() -> Response:
        return jsonify(ok=True, preferences=preferences.load(wordbooks))

    @app.get("/api/wordbooks/<book_id>")
    def api_wordbook(book_id: str) -> Response:
        book = wordbooks.get(book_id)
        if book is None:
            raise ApiError("词书不存在或已被移除", 404, code="book_not_found")
        return jsonify(ok=True, book=book.as_api())

    @app.put("/api/settings")
    @protected_write
    def api_update_settings() -> Response:
        payload = read_json_body()
        try:
            updated = preferences.update(payload, wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        return jsonify(ok=True, preferences=updated)

    @app.post("/api/settings/reset")
    @protected_write
    def api_reset_settings() -> Response:
        try:
            defaults = preferences.reset(wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        return jsonify(ok=True, preferences=defaults, reset=True)

    @app.post("/api/auth/login")
    def api_login() -> Response:
        require_csrf()
        payload = read_json_body()
        candidate = payload.get("password")
        if not isinstance(candidate, str) or not candidate:
            raise ApiError("请输入设置密码", 400, code="password_required")
        if len(candidate) > 1024:
            raise ApiError("密码过长", 400, code="invalid_password")

        key = client_key()
        retry_after = limiter.retry_after(key)
        if retry_after:
            raise ApiError(
                f"尝试次数过多，请在 {retry_after} 秒后再试",
                429,
                code="rate_limited",
                retry_after=retry_after,
            )
        if not password_is_configured(
            config.settings_password, config.settings_password_hash
        ):
            raise ApiError(
                "服务器尚未配置设置页密码，请联系管理员",
                503,
                code="password_not_configured",
            )

        if verify_password(
            config.settings_password, config.settings_password_hash, candidate
        ):
            limiter.record_success(key)
            session.clear()
            session.permanent = True
            session["settings_authenticated"] = True
            session["auth_at"] = now()
            token = new_csrf_token()
            session["_csrf"] = token
            logger.info("设置页验证成功：%s", key)
            return jsonify(ok=True, csrfToken=token, expiresIn=config.session_lifetime_seconds)

        locked_for = limiter.record_failure(key)
        logger.warning("设置页验证失败：%s", key)
        if locked_for:
            raise ApiError(
                f"密码错误次数过多，请在 {locked_for} 秒后再试",
                429,
                code="rate_limited",
                retry_after=locked_for,
            )
        raise ApiError("密码不正确", 401, code="invalid_password")

    @app.post("/api/auth/logout")
    @protected_write
    def api_logout() -> Response:
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/audio")
    def api_audio() -> Response:
        word = request.args.get("word", "")
        try:
            asset = audio.get(word)
        except AudioError as exc:
            raise ApiError(str(exc), exc.status, code="audio_unavailable") from exc
        try:
            response = send_file(
                asset.path,
                mimetype=asset.mimetype,
                conditional=True,
                etag=True,
                last_modified=asset.path.stat().st_mtime,
                max_age=min(config.audio.cache_ttl_seconds, 30 * 24 * 3600),
            )
        except OSError as exc:
            raise ApiError("发音文件不可用", 502, code="audio_unavailable") from exc
        response.headers["Cache-Control"] = (
            f"public, max-age={min(config.audio.cache_ttl_seconds, 30 * 24 * 3600)}, "
            "immutable"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    @app.get("/api/health")
    def api_health() -> Response:
        return jsonify(
            ok=True,
            status="ok",
            books=len(wordbooks.books()),
            version=__version__,
        )

    # ----------------------------------------------------------- middleware
    @app.before_request
    def same_origin_guard() -> None:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        if not request.path.startswith("/api/"):
            return
        fetch_site = request.headers.get("Sec-Fetch-Site", "")
        if fetch_site == "cross-site":
            raise ApiError("跨站请求已被拒绝", 403, code="cross_origin")
        origin = request.headers.get("Origin")
        if not origin:
            return
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise ApiError("请求来源无效", 403, code="cross_origin") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ApiError("请求来源无效", 403, code="cross_origin")
        if parsed.netloc.casefold() != request.host.casefold():
            raise ApiError("跨站请求已被拒绝", 403, code="cross_origin")

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "media-src 'self'; "
            "manifest-src 'self'"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.path == "/api/audio":
            # send_file already provided a long-lived cache policy.
            pass
        elif request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -------------------------------------------------------- error handlers
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError) -> tuple[Response, int]:
        response = jsonify(
            ok=False,
            error=error.message,
            code=error.code,
        )
        response.status_code = error.status
        if error.retry_after:
            response.headers["Retry-After"] = str(error.retry_after)
        return response, error.status

    @app.errorhandler(404)
    def handle_not_found(error: HTTPException) -> Any:
        if request.path.startswith("/api/"):
            response = jsonify(ok=False, error="接口不存在", code="not_found")
            response.status_code = 404
            return response, 404
        return (
            render_template(
                "error.html",
                **page_context(
                    status_code=404,
                    status_title="这一页没有单词",
                    status_message="页面可能已经离开词库，返回闪卡继续学习吧。",
                ),
            ),
            404,
        )

    @app.errorhandler(405)
    def handle_method_not_allowed(error: HTTPException) -> Any:
        if request.path.startswith("/api/"):
            response = jsonify(
                ok=False, error="请求方法不允许", code="method_not_allowed"
            )
            response.status_code = 405
            return response, 405
        return handle_not_found(error)

    @app.errorhandler(413)
    def handle_too_large(error: HTTPException) -> Any:
        response = jsonify(ok=False, error="请求内容过大", code="too_large")
        response.status_code = 413
        return response, 413

    @app.errorhandler(500)
    def handle_server_error(error: Exception) -> Any:
        logger.exception("未处理的服务端错误")
        if request.path.startswith("/api/"):
            response = jsonify(
                ok=False, error="服务器开小差了，请稍后再试", code="server_error"
            )
            response.status_code = 500
            return response, 500
        return (
            render_template(
                "error.html",
                **page_context(
                    status_code=500,
                    status_title="闪了一下腰",
                    status_message="服务器遇到了一点问题，请稍后刷新重试。",
                ),
            ),
            500,
        )

    return app


def _asset_version(app: Flask) -> str:
    digest = hashlib.sha256()
    static_dir = app.static_folder
    if static_dir:
        root = Path(static_dir)
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in {".css", ".js", ".woff2", ".svg", ".png"} and path.is_file():
                stat = path.stat()
                digest.update(str(path.relative_to(root)).encode())
                digest.update(str(stat.st_mtime_ns).encode())
                digest.update(str(stat.st_size).encode())
    return digest.hexdigest()[:12]


__all__ = ["ApiError", "Services", "create_app"]
