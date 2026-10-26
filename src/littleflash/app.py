"""Flask application for LittleFlash 闪词.

数据（账号、偏好、学习进度、站点设置）全部存放在 SQLite；本模块只负责
HTTP 层：会话、角色鉴权、CSRF、页面渲染与 JSON 接口。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__
from .audio import AudioError, AudioStore
from .config import AppConfig, load_config
from .db import (
    ANONYMOUS_OWNER,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_USER,
    generate_password,
)
from .preferences import (
    PreferenceStore,
    SettingsValidationError,
    owner_for_user,
    sample_word,
)
from .progress import get_progress, record_progress
from .security import LoginRateLimiter, csrf_equal, new_csrf_token
from .users import (
    User,
    UserError,
    authenticate,
    create_user,
    get_user,
    list_users_with_progress,
    set_active,
    set_password,
)
from .wordbooks import Wordbook, WordbookManager

logger = logging.getLogger(__name__)

ROLE_LEVEL = {ROLE_USER: 1, ROLE_ADMIN: 2, ROLE_SUPERADMIN: 3}

# 登录成功后允许跳转的页面（白名单，避免开放重定向）
LOGIN_NEXT = {
    "": "index",
    "home": "index",
    "index": "index",
    "settings": "settings_page",
    "settings_page": "settings_page",
    "account": "account_page",
    "account_page": "account_page",
    "admin": "admin_page",
    "admin_page": "admin_page",
}

_MISSING = object()


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
    preferences: PreferenceStore
    audio: AudioStore
    login_limiter: LoginRateLimiter
    ip_limiter: LoginRateLimiter


def create_app(config: AppConfig | None = None) -> Flask:
    if config is None:
        config = load_config()
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
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=config.session_lifetime_seconds),
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
    preferences = PreferenceStore(config.db_path)
    audio = AudioStore(config.audio, wordbooks)
    limiter = LoginRateLimiter(
        config.security.login_max_attempts,
        config.security.login_window_seconds,
        config.security.login_lockout_seconds,
    )
    ip_limiter = LoginRateLimiter(
        max(config.security.login_max_attempts * 6, 30),
        config.security.login_window_seconds,
        config.security.login_lockout_seconds,
    )
    services = Services(config, wordbooks, preferences, audio, limiter, ip_limiter)
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
        db_mode = config.db_path.stat().st_mode & 0o777
        if db_mode & 0o077:
            logger.warning(
                "数据库权限为 %o，建议执行 chmod 600 %s（内含账号与密码哈希）。",
                db_mode,
                config.db_path,
            )
    except OSError:
        pass
    if not config.security.cookie_secure:
        logger.info(
            "Cookie 未启用 Secure 标记；若站点使用 HTTPS，请把 "
            "site_settings 中的 security.cookie_secure 设为 true。"
        )

    # ------------------------------------------------------------- helpers
    def now() -> int:
        return int(time.time())

    def current_user() -> User | None:
        """按会话中的 uid 载入用户；会话过期或被停用时自动失效。"""
        cached = getattr(g, "_lf_user", _MISSING)
        if cached is not _MISSING:
            return cached

        user: User | None = None
        user_id = session.get("uid")
        issued_at = session.get("auth_at")
        if isinstance(user_id, int) and not isinstance(user_id, bool):
            if not isinstance(issued_at, int) or (
                now() - issued_at > config.session_lifetime_seconds
            ):
                session.clear()
            else:
                candidate = get_user(config.db_path, user_id)
                if candidate is not None and candidate.is_active:
                    user = candidate
                else:
                    session.clear()
        setattr(g, "_lf_user", user)
        return user

    def require_login() -> User:
        user = current_user()
        if user is None:
            raise ApiError("请先登录后再操作", 401, code="auth_required")
        return user

    def require_role(minimum: str) -> User:
        user = require_login()
        if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL[minimum]:
            raise ApiError("当前账号没有执行此操作的权限", 403, code="forbidden")
        return user

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

    def book_titles() -> dict[str, str]:
        return {book.id: book.title for book in wordbooks.books()}

    def login_key(username: str) -> str:
        return f"{client_key()}|{username.casefold()}"

    def page_context(**extra: Any) -> dict[str, Any]:
        return {
            "asset_version": asset_version,
            "app_version": __version__,
            "current_year": time.localtime().tm_year,
            "csrf_token": ensure_csrf_token(),
            "account": current_user(),
            **extra,
        }

    def settings_scope() -> str:
        """``site`` = 超级管理员编辑未登录访客的默认设置。"""
        if request.args.get("scope") == "site":
            user = current_user()
            if user is None or not user.is_superadmin:
                raise ApiError(
                    "只有超级管理员可以修改未登录用户的设置",
                    403,
                    code="forbidden",
                )
            return "site"
        return "user"

    def owner_for_scope(scope: str) -> str:
        if scope == "site":
            return ANONYMOUS_OWNER
        user = current_user()
        if user is None:
            raise ApiError(
                "请先登录后再保存到服务器；未登录的更改只会保存在本机浏览器",
                401,
                code="auth_required",
            )
        return owner_for_user(user.id)

    def progress_payload(user: User) -> dict[str, Any] | None:
        record = get_progress(config.db_path, user.id)
        if record is None:
            return None
        return record.public(book_titles())

    def deck_size(book: Wordbook, unit_id: str) -> int:
        units = book.units
        if unit_id != "all":
            units = tuple(unit for unit in units if unit.id == unit_id)
        return sum(len(unit.words) for unit in units)

    def page_login_required(next_endpoint: str) -> Response | None:
        if current_user() is not None:
            return None
        return redirect(url_for("login_page", next=next_endpoint))

    def forbidden_page() -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                **page_context(
                    status_code=403,
                    status_title="这里需要更高的权限",
                    status_message="当前账号无法查看这个页面，换一个账号或回到闪卡继续学习。",
                ),
            ),
            403,
        )

    # ---------------------------------------------------------- page routes
    @app.get("/")
    def index() -> str:
        return render_template("index.html", **page_context())

    @app.get("/login")
    def login_page() -> Any:
        if current_user() is not None:
            return redirect(url_for("index"))
        target = request.args.get("next", "")
        if target not in LOGIN_NEXT:
            target = ""
        return render_template(
            "login.html",
            **page_context(next_target=target),
        )

    @app.get("/settings")
    def settings_page() -> str:
        user = current_user()
        scope = "local"
        if user is not None:
            scope = (
                "site"
                if user.is_superadmin and request.args.get("scope") == "site"
                else "user"
            )
        owner = ANONYMOUS_OWNER if scope in {"local", "site"} else owner_for_user(user.id)
        books = wordbooks.metadata_list()
        # 未登录时下发的是站点默认值，前端会再用本机存储覆盖。
        prefs = preferences.load(owner, wordbooks)

        selected_book = wordbooks.get(str(prefs["wordbook_id"]))
        if selected_book is None:
            selected_book = wordbooks.first_book()
        selected_book_meta = (
            selected_book.metadata() if selected_book is not None else None
        )
        units: list[dict[str, Any]] = (
            selected_book_meta["units"] if selected_book_meta else []
        )
        preview = sample_word(prefs, wordbooks)

        return render_template(
            "settings.html",
            **page_context(
                authenticated=user is not None,
                scope=scope,
                local_mode=scope == "local",
                books=books,
                preferences=prefs,
                selected_book=selected_book_meta,
                selected_book_id=selected_book.id if selected_book else "",
                units=units,
                preview=preview,
                progress=progress_payload(user) if user is not None else None,
            ),
        )

    @app.get("/account")
    def account_page() -> Any:
        redirect_response = page_login_required("account_page")
        if redirect_response is not None:
            return redirect_response
        user = current_user()
        assert user is not None
        record = get_progress(config.db_path, user.id)
        book = wordbooks.get(record.wordbook_id) if record else None
        return render_template(
            "dashboard.html",
            **page_context(
                progress=record.public(book_titles()) if record else None,
                progress_book=book.metadata() if book else None,
            ),
        )

    @app.get("/admin")
    def admin_page() -> Any:
        redirect_response = page_login_required("admin_page")
        if redirect_response is not None:
            return redirect_response
        user = current_user()
        assert user is not None
        if not user.is_admin:
            return forbidden_page()
        return render_template(
            "admin.html",
            **page_context(
                can_manage_roles=user.is_superadmin,
            ),
        )

    # --------------------------------------------------------------- API
    @app.get("/api/bootstrap")
    def api_bootstrap() -> Response:
        user = current_user()
        owner = owner_for_user(user.id) if user else ANONYMOUS_OWNER
        return jsonify(
            ok=True,
            # 未登录的访客会用浏览器本地设置覆盖下面的站点默认值。
            authenticated=user is not None,
            user=user.public() if user else None,
            canEditSiteSettings=bool(user and user.is_superadmin),
            books=wordbooks.metadata_list(),
            preferences=preferences.load(owner, wordbooks),
            progress=progress_payload(user) if user else None,
            server={
                "audioEnabled": config.audio.enabled,
                "version": __version__,
            },
        )

    @app.get("/api/auth/me")
    def api_me() -> Response:
        user = current_user()
        return jsonify(ok=True, authenticated=user is not None, user=user.public() if user else None)

    @app.get("/api/settings")
    def api_get_settings() -> Response:
        scope = settings_scope()
        user = current_user()
        owner = (
            ANONYMOUS_OWNER
            if scope == "site" or user is None
            else owner_for_user(user.id)
        )
        return jsonify(
            ok=True,
            scope=scope,
            preferences=preferences.load(owner, wordbooks),
        )

    @app.get("/api/wordbooks/<book_id>")
    def api_wordbook(book_id: str) -> Response:
        book = wordbooks.get(book_id)
        if book is None:
            raise ApiError("词书不存在或已被移除", 404, code="book_not_found")
        return jsonify(ok=True, book=book.as_api())

    @app.put("/api/settings")
    def api_update_settings() -> Response:
        require_csrf()
        scope = settings_scope()
        owner = owner_for_scope(scope)
        payload = read_json_body()
        try:
            updated = preferences.update(owner, payload, wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        return jsonify(ok=True, scope=scope, preferences=updated)

    @app.post("/api/settings/reset")
    def api_reset_settings() -> Response:
        require_csrf()
        scope = settings_scope()
        owner = owner_for_scope(scope)
        try:
            defaults = preferences.reset(owner, wordbooks)
        except SettingsValidationError as exc:
            raise ApiError(str(exc), 400, code="invalid_settings") from exc
        return jsonify(ok=True, scope=scope, preferences=defaults, reset=True)

    @app.post("/api/auth/login")
    def api_login() -> Response:
        require_csrf()
        payload = read_json_body()
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not username.strip():
            raise ApiError("请输入用户名", 400, code="username_required")
        if not isinstance(password, str) or not password:
            raise ApiError("请输入密码", 400, code="password_required")
        username = username.strip()
        if len(username) > 64 or len(password) > 256:
            raise ApiError("用户名或密码过长", 400, code="invalid_credentials")

        per_user = login_key(username)
        retry_after = limiter.retry_after(per_user) or ip_limiter.retry_after(client_key())
        if retry_after:
            raise ApiError(
                f"尝试次数过多，请在 {retry_after} 秒后再试",
                429,
                code="rate_limited",
                retry_after=retry_after,
            )

        user = authenticate(config.db_path, username, password)
        if user is None:
            locked_for = limiter.record_failure(per_user)
            ip_locked = ip_limiter.record_failure(client_key())
            logger.warning("登录失败：%s（%s）", username, client_key())
            wait = locked_for or ip_locked
            if wait:
                raise ApiError(
                    f"密码错误次数过多，请在 {wait} 秒后再试",
                    429,
                    code="rate_limited",
                    retry_after=wait,
                )
            raise ApiError("用户名或密码不正确", 401, code="invalid_credentials")

        limiter.record_success(per_user)
        ip_limiter.record_success(client_key())
        session.clear()
        session.permanent = True
        session["uid"] = user.id
        session["auth_at"] = now()
        token = new_csrf_token()
        session["_csrf"] = token
        setattr(g, "_lf_user", user)

        endpoint = LOGIN_NEXT.get(str(payload.get("next") or ""), "index")
        if endpoint == "admin_page" and not user.is_admin:
            endpoint = "account_page"
        logger.info("登录成功：%s（%s）", user.username, client_key())
        return jsonify(
            ok=True,
            user=user.public(),
            csrfToken=token,
            redirect=url_for(endpoint),
        )

    @app.post("/api/auth/logout")
    def api_logout() -> Response:
        require_csrf()
        session.clear()
        return jsonify(ok=True)

    # ---------------------------------------------------------- 学习进度
    @app.get("/api/progress")
    def api_get_progress() -> Response:
        user = require_login()
        return jsonify(ok=True, progress=progress_payload(user))

    @app.post("/api/progress")
    def api_post_progress() -> Response:
        user = require_login()
        require_csrf()
        payload = read_json_body()

        book_id = payload.get("wordbook_id")
        book = wordbooks.get(book_id) if isinstance(book_id, str) else None
        if book is None:
            raise ApiError("词书不存在或已被移除", 400, code="book_not_found")

        unit_id = payload.get("unit_id")
        if not isinstance(unit_id, str) or (
            unit_id != "all" and book.get_unit(unit_id) is None
        ):
            unit_id = "all"

        limit = max(1, deck_size(book, unit_id))
        raw_index = payload.get("word_index", 0)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raw_index = 0
        index = max(0, min(limit - 1, raw_index))

        raw_rounds = payload.get("rounds", 1)
        if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
            raw_rounds = 1
        rounds = max(1, min(100000, raw_rounds))

        word_en = payload.get("word_en")
        word_en = word_en.strip()[:64] if isinstance(word_en, str) else ""

        raw_seconds = payload.get("seconds_delta", 0)
        if isinstance(raw_seconds, bool) or not isinstance(raw_seconds, (int, float)):
            raw_seconds = 0

        record = record_progress(
            config.db_path,
            user.id,
            wordbook_id=book.id,
            unit_id=unit_id,
            word_index=index,
            word_en=word_en,
            rounds=rounds,
            seconds_delta=int(raw_seconds),
        )
        return jsonify(ok=True, progress=record.public(book_titles()))

    @app.post("/api/account/password")
    def api_account_password() -> Response:
        user = require_login()
        require_csrf()
        payload = read_json_body()
        current = payload.get("current_password")
        new_password = payload.get("new_password")
        if not isinstance(current, str) or not current:
            raise ApiError("请输入当前密码", 400, code="password_required")
        if not isinstance(new_password, str) or not new_password:
            raise ApiError("请输入新密码", 400, code="password_required")
        if authenticate(config.db_path, user.username, current) is None:
            raise ApiError("当前密码不正确", 400, code="invalid_password")
        try:
            set_password(config.db_path, user.id, new_password)
        except UserError as exc:
            raise ApiError(str(exc), 400, code="invalid_password") from exc
        session.clear()
        session.permanent = True
        session["uid"] = user.id
        session["auth_at"] = now()
        token = new_csrf_token()
        session["_csrf"] = token
        return jsonify(ok=True, csrfToken=token)

    # ------------------------------------------------------------ 管理员
    @app.get("/api/admin/users")
    def api_admin_users() -> Response:
        require_role(ROLE_ADMIN)
        rows = list_users_with_progress(config.db_path, book_titles())
        return jsonify(ok=True, users=rows, summary=_summary(rows))

    @app.post("/api/admin/users")
    def api_admin_create_user() -> Response:
        actor = require_role(ROLE_ADMIN)
        require_csrf()
        payload = read_json_body()
        username = payload.get("username")
        password = payload.get("password")
        role = payload.get("role") or ROLE_USER
        display_name = payload.get("display_name") or ""
        if not isinstance(username, str) or not isinstance(password, str):
            raise ApiError("请填写用户名和密码", 400, code="invalid_user")
        if not isinstance(role, str) or role not in ROLE_LEVEL:
            raise ApiError("角色无效", 400, code="invalid_role")
        if role != ROLE_USER and not actor.is_superadmin:
            raise ApiError(
                "只有超级管理员可以创建管理员账号", 403, code="forbidden"
            )
        try:
            created = create_user(
                config.db_path,
                username,
                password,
                role=role,
                display_name=str(display_name),
            )
        except UserError as exc:
            raise ApiError(str(exc), 400, code="invalid_user") from exc
        logger.info("%s 创建了账号 %s（%s）", actor.username, created.username, role)
        return jsonify(ok=True, user=created.public())

    @app.post("/api/admin/users/<int:user_id>/password")
    def api_admin_reset_password(user_id: int) -> Response:
        actor = require_role(ROLE_ADMIN)
        require_csrf()
        target = get_user(config.db_path, user_id)
        if target is None:
            raise ApiError("用户不存在", 404, code="user_not_found")
        if target.role in (ROLE_ADMIN, ROLE_SUPERADMIN) and not actor.is_superadmin:
            raise ApiError(
                "只有超级管理员可以修改管理员账号的密码", 403, code="forbidden"
            )

        payload = read_json_body()
        raw_password = payload.get("password")
        generated: str | None = None
        if raw_password in (None, ""):
            generated = generate_password(8)
            password = generated
        elif isinstance(raw_password, str):
            password = raw_password
        else:
            raise ApiError("新密码格式无效", 400, code="invalid_password")

        try:
            set_password(config.db_path, target.id, password)
        except UserError as exc:
            raise ApiError(str(exc), 400, code="invalid_password") from exc

        logger.info("%s 重置了 %s 的密码", actor.username, target.username)
        return jsonify(
            ok=True,
            user=target.public(),
            generatedPassword=generated,
        )

    @app.post("/api/admin/users/<int:user_id>/active")
    def api_admin_set_active(user_id: int) -> Response:
        actor = require_role(ROLE_SUPERADMIN)
        require_csrf()
        target = get_user(config.db_path, user_id)
        if target is None:
            raise ApiError("用户不存在", 404, code="user_not_found")
        if target.id == actor.id:
            raise ApiError("不能停用当前登录的账号", 400, code="invalid_target")
        if target.is_superadmin:
            raise ApiError("不能停用超级管理员账号", 400, code="invalid_target")
        payload = read_json_body()
        active = payload.get("active")
        if not isinstance(active, bool):
            raise ApiError("active 必须是布尔值", 400, code="invalid_payload")
        set_active(config.db_path, target.id, active)
        refreshed = get_user(config.db_path, target.id)
        assert refreshed is not None
        return jsonify(ok=True, user=refreshed.public())

    # ---------------------------------------------------------------- audio
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

    @app.errorhandler(403)
    def handle_forbidden(error: HTTPException) -> Any:
        if request.path.startswith("/api/"):
            response = jsonify(ok=False, error="没有权限", code="forbidden")
            response.status_code = 403
            return response, 403
        return forbidden_page()

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


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """管理员页顶部统计：人数、今日活跃、总时长等。"""
    local = time.localtime()
    day_start = int(
        time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
    )
    total_seconds = 0
    studied = 0
    active_today = 0
    admins = 0
    for row in rows:
        seconds = int(row.get("totalSeconds") or 0)
        total_seconds += seconds
        if seconds > 0:
            studied += 1
        last_seen = row.get("lastStudyAt")
        if isinstance(last_seen, int) and last_seen >= day_start:
            active_today += 1
        if row.get("role") in (ROLE_ADMIN, ROLE_SUPERADMIN):
            admins += 1
    return {
        "total": len(rows),
        "active": sum(1 for row in rows if row.get("isActive")),
        "admins": admins,
        "studied": studied,
        "activeToday": active_today,
        "totalSeconds": total_seconds,
    }


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
