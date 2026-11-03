"""Small, dependency-light security primitives used by the web app.

0.1.5 起，设置密码与设置写入都会先过一遍输入检查：

- 密码只接受 1–256 个可打印字符，拒绝 NUL / 控制字符 / 零宽与双向控制符；
- 明显像 SQL 注入的载荷（联合查询、堆叠语句、恒真条件、注释截断、
  时间盲注、读文件、系统表探测）会被识别出来并拒绝，同时计入登录限流。

主线没有数据库，也没有任何 SQL 语句（0.2.0 的 SQLite 分支已弃用），
所以这里防的是「将来接数据库时忘了处理」和「把恶意串写进日志/配置」，
而不是补救一个已经存在的注入点。
"""

from __future__ import annotations

import hmac
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from werkzeug.security import check_password_hash

MAX_PASSWORD_LENGTH = 256

# NUL、C0/C1 控制字符、零宽字符与双向覆盖字符：正常密码里不会出现。
_UNSAFE_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    "\u200b-\u200f\u202a-\u202e\u2060\ufeff]"
)

# 明显是注入载荷的特征。命中即拒绝；宁可让用户换一个密码，也不要把可疑串写进配置。
_THREAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ddl_statement",
        re.compile(r"\b(?:drop|truncate|alter)\s+(?:table|database|schema|view)\b", re.I),
    ),
    (
        "stacked_query",
        re.compile(
            r";\s*(?:select|insert|update|delete|drop|alter|create|exec|call|grant|revoke)\b",
            re.I,
        ),
    ),
    ("union_select", re.compile(r"\bunion\s+(?:all\s+)?select\b", re.I)),
    ("select_from", re.compile(r"\bselect\b[\s\S]{0,80}?\bfrom\b", re.I)),
    (
        "dml_statement",
        re.compile(r"\b(?:insert\s+into|update\s+[\w.\"`]+\s+set|delete\s+from)\b", re.I),
    ),
    (
        "tautology",
        re.compile(
            r"(?:'|\")\s*(?:or|and)\s+(?:'|\")?[\w\d]+(?:'|\")?\s*=\s*(?:'|\")?[\w\d]+",
            re.I,
        ),
    ),
    ("comment_payload", re.compile(r"(?:'|\")\s*(?:--|#|/\*)")),
    ("time_based", re.compile(r"\b(?:sleep|benchmark|pg_sleep)\s*\(", re.I)),
    (
        "file_access",
        re.compile(r"\b(?:load_file|into\s+outfile|xp_cmdshell)\b", re.I),
    ),
    (
        "system_catalog",
        re.compile(r"\b(?:information_schema|sysobjects|pg_catalog)\b", re.I),
    ),
)


class InputRejected(ValueError):
    """客户端提供的值看起来不安全（不是「密码错了」，而是「不接受这个输入」）。"""

    def __init__(self, message: str, *, code: str = "suspicious_input") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _iter_strings(value: Any, *, budget: int = 64 * 1024) -> Iterable[str]:
    """深度优先遍历 JSON 结构里的字符串，限制总长度避免被超大载荷拖垮。"""
    stack: list[Any] = [value]
    scanned = 0
    while stack and scanned < budget:
        item = stack.pop()
        if isinstance(item, str):
            scanned += len(item)
            yield item
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_equal(expected: str | None, actual: str | None) -> bool:
    if not expected or not actual:
        return False
    return hmac.compare_digest(expected, actual)


def detect_threat(value: Any) -> str | None:
    """返回命中的威胁特征名；没有命中返回 ``None``。"""
    for text in _iter_strings(value):
        for name, pattern in _THREAT_PATTERNS:
            if pattern.search(text):
                return name
    return None


def validate_password(raw: Any) -> str:
    """设置密码的输入检查：只放行长度合理、没有隐藏字符的字符串。"""
    if not isinstance(raw, str) or not raw:
        raise InputRejected("请输入设置密码", code="password_required")
    if len(raw) > MAX_PASSWORD_LENGTH:
        raise InputRejected("密码过长", code="invalid_password")
    if _UNSAFE_CHARS.search(raw):
        raise InputRejected("密码包含不可见字符", code="invalid_password")
    threat = detect_threat(raw)
    if threat:
        raise InputRejected("请求包含不被接受的字符", code="suspicious_input")
    return raw


def verify_password(
    configured_password: str | None,
    configured_hash: str | None,
    candidate: str,
) -> bool:
    """Check a plaintext or Werkzeug-hashed settings password safely."""
    if not candidate or len(candidate) > 1024:
        return False
    try:
        if configured_hash:
            return bool(check_password_hash(configured_hash, candidate))
        if configured_password:
            return hmac.compare_digest(
                configured_password.encode("utf-8"), candidate.encode("utf-8")
            )
    except (ValueError, TypeError):
        return False
    return False


def password_is_configured(
    configured_password: str | None, configured_hash: str | None
) -> bool:
    return bool(configured_password or configured_hash)


@dataclass(slots=True)
class _AttemptState:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginRateLimiter:
    """In-memory login throttle, effective for the single-process deployment.

    The sample nginx/gunicorn setup uses one worker with threads.  For multiple
    worker processes this limiter remains a useful local guard, but a shared
    store (Redis) would be needed for a cluster-wide counter.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._states: dict[str, _AttemptState] = {}
        self._lock = threading.Lock()
        self._operations = 0

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return 0
            if state.locked_until > now:
                return max(1, math.ceil(state.locked_until - now))
            if state.locked_until:
                state.locked_until = 0.0
                state.failures.clear()
            self._prune_state(state, now)
            return 0

    def record_failure(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(key, _AttemptState())
            if state.locked_until > now:
                return max(1, math.ceil(state.locked_until - now))
            if state.locked_until:
                state.locked_until = 0.0
                state.failures.clear()
            self._prune_state(state, now)
            state.failures.append(now)
            if len(state.failures) >= self.max_attempts:
                state.failures.clear()
                state.locked_until = now + self.lockout_seconds
                retry = self.lockout_seconds
            else:
                retry = 0
            self._operations += 1
            if self._operations % 256 == 0:
                self._prune_all(now)
            return retry

    def record_success(self, key: str) -> None:
        with self._lock:
            self._states.pop(key, None)

    def _prune_state(self, state: _AttemptState, now: float) -> None:
        cutoff = now - self.window_seconds
        state.failures = [stamp for stamp in state.failures if stamp >= cutoff]

    def _prune_all(self, now: float) -> None:
        stale: list[str] = []
        for key, state in self._states.items():
            if state.locked_until > now:
                continue
            self._prune_state(state, now)
            if not state.failures:
                stale.append(key)
        for key in stale:
            self._states.pop(key, None)
