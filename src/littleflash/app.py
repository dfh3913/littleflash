"""Flask application for LittleFlash 闪词."""

from __future__ import annotations

import gzip
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
from .preferences import (
    MAX_BOOKS,
    SettingsStore,
    SettingsValidationError,
    iter_selected_words,
    normalize_selections,
    sample_word,
    selected_word_count,
    selection_units,
)
from .security import (
    LoginRateLimiter,
    csrf_equal,
    new_csrf_token,
    password_is_configured,
    verify_password,
)
from .wordbooks import WordbookManager

logger = logging.getLogger(__name__)

# 设置页预览一次最多下发多少张卡（够上/下切换，又不至于把整本书塞进响应）。
PREVIEW_LIMIT_MAX = 60


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

    def formation_settings(prefs: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": prefs.get("formation_enabled", True),
            "group": prefs.get("formation_group", "deck"),
            "filter": prefs.get("formation_filter", "all"),
        }

    def sync_formation(prefs: dict[str, Any]) -> None:
        """把管理员的「词性转换」显示设置同步给词书管理器。"""
        if wordbooks.set_formation_view(formation_settings(prefs)):
            logger.info("词性转换展示方式已更新：%s", wordbooks.formation_view())

    def apply_audio_runtime(prefs: dict[str, Any]) -> None:
        audio.set_runtime(
            interval_seconds=prefs.get("cache_interval_seconds", 1.0),
            cache_limit=prefs.get("cache_limit", 2000),
        )

    startup_prefs = preferences.load(wordbooks)
    sync_formation(startup_prefs)
    apply_audio_runtime(startup_prefs)

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

    def make_etag(*parts: Any) -> str:
        return hashlib.sha256(
            "|".join(str(part) for part in parts).encode("utf-8")
        ).hexdigest()[:32]

    def etag_matches(tag: str) -> bool:
        header = request.headers.get("If-None-Match", "")
        return header.strip().strip('"') == tag

    def parse_selection_param(raw: str | None) -> list[dict[str, Any]]:
        """解析 ``book1~u1,u2|book2~all`` 形式的紧凑选择参数。"""
        if not raw:
            return []
        items: list[dict[str, Any]] = []
        for part in raw.split("|")[:MAX_BOOKS]:
            book_id, _, units = part.partition("~")
            book_id = book_id.strip()
            if not book_id:
                continue
            if units.strip() in ("", "all"):
                unit_ids = ["all"]
            else:
                unit_ids = [value.strip() for value in units.split(",") if value.strip()]
            items.append({"wordbook_id": book_id, "unit_ids": unit_ids})
        return items

    def deck_items(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for book, unit, word in iter_selected_words(wordbooks, selections):
            item: dict[str, Any] = {
                "en": word.en,
                "zh": word.zh,
                "phonetic": word.phonetic,
                "bookId": book.id,
                "bookTitle": book.title,
                "unitId": unit.id,
                "unitTitle": unit.title,
            }
            if word.is_card:
                item["kind"] = "cards"
                item["front"] = word.front
                item["back"] = word.back
                item["answer"] = word.audio_answer
                if word.note:
                    item["note"] = word.note
            items.append(item)
        return items

    def selection_query(selections: list[dict[str, Any]]) -> str:
        parts = []
        for selection in selections:
            book_id = str(selection.get("wordbook_id", ""))
            if not book_id:
                continue
            unit_ids = selection.get("unit_ids") or ["all"]
            units = "all" if "all" in unit_ids else ",".join(
                str(unit_id) for unit_id in unit_ids
            )
            parts.append(f"{book_id}~{units}")
        return "|".join(parts)

    # ---------------------------------------------------------- page routes
    @app.get("/")
    def index() -> str:
        return render_template("index.html", **page_context())

    @app.get("/settings")
    def settings_page() -> str:
        authenticated = is_authenticated()
        csrf_token = ensure_csrf_token()
        # 设置页对所有访客开放：未登录时表单读写浏览器本地存储，不会回写服务器；
        # 因此这里下发的只是与 /api/bootstrap 相同的公开默认值。
        prefs = preferences.load(wordbooks)
        sync_formation(prefs)
        books = wordbooks.metadata_list()
        preview = sample_word(prefs, wordbooks)
        selection_map = {
            selection["wordbook_id"]: selection["unit_ids"]
            for selection in prefs["selections"]
        }

        return render_template(
            "settings.html",
            **page_context(
                authenticated=authenticated,
                local_mode=not authenticated,
                csrf_token=csrf_token,
                books=books,
                preferences=prefs,
                selection_map=selection_map,
                preview=preview,
                audio_cache=audio.runtime() if authenticated else None,
            ),
        )

    # --------------------------------------------------------------- API
    @app.get("/api/bootstrap")
    def api_bootstrap() -> Response:
        prefs = preferences.load(wordbooks)
        sync_formation(prefs)
        authenticated = is_authenticated()
        return jsonify(
            ok=True,
            # 未登录的访客会用浏览器本地设置覆盖下面的公开默认值。
            authenticated=authenticated,
            books=wordbooks.metadata_list(),
            preferences=prefs,
            selectionQuery=selection_query(prefs["selections"]),
            selectedWordCount=selected_word_count(wordbooks, prefs["selections"]),
            server={
                "audioEnabled": config.audio.enabled,
                "version": __version__,
                "formation": wordbooks.formation_view(),
                "audioCache": audio.runtime() if authenticated else None,
            },
        )

    @app.get("/api/settings")
    def api_get_settings() -> Response:
        return jsonify(ok=True, preferences=preferences.load(wordbooks))

    @app.get("/api/wordbooks/<book_id>")
    def api_wordbook(book_id: str) -> Response:
        sync_formation(preferences.load(wordbooks))
        book = wordbooks.get(book_id)
        if book is None:
            raise ApiError("词书不存在或已被移除", 404, code="book_not_found")
        tag = make_etag("book", wordbooks.revision(), book_id)
        if etag_matches(tag):
            response = Response(status=304)
            response.headers["ETag"] = f'"{tag}"'
            return response
        response = jsonify(ok=True, book=book.as_api())
        response.set_etag(tag)
        response.headers["Cache-Control"] = "private, max-age=300, must-revalidate"
        return response

    @app.get("/api/deck")
    def api_deck() -> Response:
        """返回当前选择范围内的扁平单词列表，供首页一次加载。"""
        prefs = preferences.load(wordbooks)
        sync_formation(prefs)
        raw = request.args.get("s")
        if raw:
            selections = normalize_selections(
                parse_selection_param(raw), wordbooks
            )
        else:
            selections = prefs["selections"]

        tag = make_etag("deck", wordbooks.revision(), selection_query(selections))
        if etag_matches(tag):
            response = Response(status=304)
            response.headers["ETag"] = f'"{tag}"'
            return response

        items = deck_items(selections)
        response = jsonify(
            ok=True,
            deck=items,
            selections=selections,
            query=selection_query(selections),
            count=len(items),
            books=[
                {"id": book.id, "title": book.title}
                for book in wordbooks.books()
                if any(
                    selection["wordbook_id"] == book.id
                    for selection in selections
                )
            ],
            audioPrefetch=bool(prefs.get("audio_prefetch")),
        )
        response.set_etag(tag)
        response.headers["Cache-Control"] = "private, max-age=60, must-revalidate"

        if items and prefs.get("audio_prefetch"):
            audio.prefetch([item["en"] for item in items])
        return response

    @app.get("/api/preview")
    def api_preview() -> Response:
        """设置页预览：返回指定词书+单元的一小批单词。

        返回 ``word``（第一个，兼容旧调用）与 ``words``（最多 limit 条），
        设置页用 ``words`` 支持预览窗的上一张/下一张切换。
        """
        book_id = request.args.get("book", "")
        unit_id = request.args.get("unit", "all")
        try:
            limit = int(request.args.get("limit", "24"))
        except (TypeError, ValueError):
            limit = 24
        limit = max(1, min(PREVIEW_LIMIT_MAX, limit))

        sync_formation(preferences.load(wordbooks))
        book = wordbooks.get(book_id) if book_id else wordbooks.first_book()
        if book is None:
            return jsonify(ok=True, word=None, words=[])

        units = book.units
        if unit_id != "all":
            unit = book.get_unit(unit_id)
            units = (unit,) if unit is not None else book.units

        words: list[dict[str, Any]] = []
        for unit in units:
            for word in unit.words:
                item: dict[str, Any] = {
                    "en": word.en,
                    "zh": word.zh,
                    "phonetic": word.phonetic,
                    "bookTitle": book.title,
                    "unitTitle": unit.title,
                }
                if word.is_card:
                    item["kind"] = "cards"
                    item["front"] = word.front
                    item["back"] = word.back
                    item["answer"] = word.audio_answer
                    if word.note:
                        item["note"] = word.note
                words.append(item)
                if len(words) >= limit:
                    break
            if len(words) >= limit:
                break

        return jsonify(ok=True, word=words[0] if words else None, words=words)

    @app.put("/api/settings")
    @protected_write
    def api_update_settings() -> Response:
        payload = read_json_body()
        # 「词性转换」的展示方式会改变词书结构，先应用再校验选择，
        # 否则同一请求里既改展示方式又改选择会互相打架。
        if any(
            key in payload
            for key in ("formation_enabled", "formation_group", "formation_filter")
        ):
            current = preferences.load(wordbooks)
            sync_formation(
                {
                    "formation_enabled": payload.get(
                        "formation_enabled", current["formation_enabled"]
                    ),
                    "formation_group": payload.get(
                        "formation_group", current["formation_group"]
                    ),
                    "formation_filter": payload.get(
                        "formation_filter", current["formation_filter"]
                    ),
                }
            )
        try:
            updated = preferences.update(payload, wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        sync_formation(updated)
        apply_audio_runtime(updated)
        if updated["audio_enabled"] and updated["audio_prefetch"]:
            audio.prefetch(
                [
                    word.en
                    for _, _, word in iter_selected_words(
                        wordbooks, updated["selections"]
                    )
                ]
            )
        return jsonify(ok=True, preferences=updated)

    @app.post("/api/settings/reset")
    @protected_write
    def api_reset_settings() -> Response:
        try:
            defaults = preferences.reset(wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        sync_formation(defaults)
        apply_audio_runtime(defaults)
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
            # /api/deck 与 /api/wordbooks/* 自带 ETag + 私有缓存策略，不要覆盖。
            if "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = "no-store"
        elif not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.after_request
    def compress_response(response: Response) -> Response:
        """对 HTML / JSON / CSS / JS 做 gzip，显著减小首屏与 deck 传输量。"""
        if response.direct_passthrough or response.status_code != 200:
            return response
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return response
        if response.headers.get("Content-Encoding"):
            return response
        content_type = (response.content_type or "").split(";")[0].strip().lower()
        if content_type not in {
            "text/html",
            "text/css",
            "application/json",
            "application/javascript",
            "text/javascript",
            "image/svg+xml",
        }:
            return response
        data = response.get_data()
        if len(data) < 512:
            return response
        compressed = gzip.compress(data, compresslevel=6)
        if len(compressed) >= len(data):
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        response.headers.add("Vary", "Accept-Encoding")
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
