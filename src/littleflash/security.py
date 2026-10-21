"""Small, dependency-light security primitives used by the web app."""

from __future__ import annotations

import hmac
import math
import secrets
import threading
import time
from dataclasses import dataclass, field

from werkzeug.security import check_password_hash


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_equal(expected: str | None, actual: str | None) -> bool:
    if not expected or not actual:
        return False
    return hmac.compare_digest(expected, actual)


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
