"""Server-side word pronunciation proxy with a local disk cache.

The browser never talks to Youdao directly.  Requests are restricted to words
that actually exist in an installed wordbook, which prevents this endpoint from
becoming an open proxy.  The upstream host is fixed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import AudioConfig
from .wordbooks import WordbookManager

logger = logging.getLogger(__name__)

YOUDAO_ENDPOINT = "https://dict.youdao.com/dictvoice"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36 LittleFlash/1.0"
)


def _is_youdao_host(hostname: str | None) -> bool:
    host = (hostname or "").lower()
    return host == "youdao.com" or host.endswith(".youdao.com")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib follows them, not just afterwards."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ) -> urllib.request.Request | None:
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme not in {"http", "https"} or not _is_youdao_host(
            parsed.hostname
        ):
            raise AudioError("发音服务返回了不可信的跳转")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class AudioError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class UnknownAudioWord(AudioError):
    def __init__(self) -> None:
        super().__init__("该单词不在词书中", status=404)


class AudioDisabled(AudioError):
    def __init__(self) -> None:
        super().__init__("发音服务未启用", status=503)


@dataclass(frozen=True, slots=True)
class AudioAsset:
    path: Path
    mimetype: str = "audio/mpeg"


class AudioStore:
    def __init__(self, config: AudioConfig, manager: WordbookManager) -> None:
        self.config = config
        self.manager = manager
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._prefetch_guard = threading.Lock()
        self._prefetch_queue: deque[str] = deque()
        self._prefetch_seen: set[str] = set()
        self._prefetch_thread: threading.Thread | None = None
        # 运行期可调（管理员在设置页修改，1 个 worker 下即时生效）。
        self._prefetch_interval = 1.0
        self._cache_limit = 2000
        self._cache_count: int | None = None

    def set_runtime(
        self,
        *,
        interval_seconds: float | None = None,
        cache_limit: int | None = None,
    ) -> None:
        """更新预缓存节奏与上限（默认每秒 1 个、最多 2000 个）。"""
        with self._prefetch_guard:
            if interval_seconds is not None:
                self._prefetch_interval = max(0.0, min(60.0, float(interval_seconds)))
            if cache_limit is not None:
                self._cache_limit = max(0, int(cache_limit))

    def runtime(self) -> dict[str, float]:
        with self._prefetch_guard:
            return {
                "intervalSeconds": round(self._prefetch_interval, 2),
                "cacheLimit": self._cache_limit,
                "cached": self._cached_count(),
            }

    def _cached_count(self) -> int:
        if self._cache_count is None:
            try:
                self._cache_count = sum(
                    1 for path in self.config.cache_dir.glob("*.mp3") if path.is_file()
                )
            except OSError:
                self._cache_count = 0
        return self._cache_count

    def get(self, word: str) -> AudioAsset:
        if not self.config.enabled:
            raise AudioDisabled()
        canonical = self.manager.canonical_audio_word(word)
        if canonical is None:
            raise UnknownAudioWord()

        cached = self._cache_path(canonical)
        if self._is_fresh(cached):
            return AudioAsset(cached)

        lock = self._lock_for(canonical)
        with lock:
            if self._is_fresh(cached):
                return AudioAsset(cached)
            try:
                self._download(canonical, cached)
            except AudioError:
                # A stale file is better than no sound when the upstream service
                # has a temporary outage.
                if cached.is_file() and cached.stat().st_size > 0:
                    logger.warning("有道发音暂时不可用，继续使用缓存：%s", canonical)
                    return AudioAsset(cached)
                raise
        return AudioAsset(cached)

    def prefetch(self, words: Iterable[str], *, limit: int | None = None) -> int:
        """把选中范围内的发音排进后台队列，提前下载到本地缓存。

        只做尽力而为的预热：不阻塞请求、失败静默重试，且跳过仍然新鲜的缓存。
        队列长度受 ``cache_limit``（默认 2000）约束，达到上限后不再入队。
        返回本次实际入队的单词数。
        """
        if not self.config.enabled or not self.config.prefetch:
            return 0
        queued = 0
        with self._prefetch_guard:
            if len(self._prefetch_seen) > 50000:
                self._prefetch_seen.clear()
            remaining = self._cache_limit - self._cached_count()
            budget = remaining if limit is None else min(remaining, limit)
            if budget <= 0:
                return 0
            for word in words:
                if queued >= budget:
                    break
                canonical = self.manager.canonical_audio_word(word)
                if not canonical or canonical in self._prefetch_seen:
                    continue
                if self._is_fresh(self._cache_path(canonical)):
                    self._prefetch_seen.add(canonical)
                    continue
                self._prefetch_seen.add(canonical)
                self._prefetch_queue.append(canonical)
                queued += 1
            if queued and (
                self._prefetch_thread is None or not self._prefetch_thread.is_alive()
            ):
                self._prefetch_thread = threading.Thread(
                    target=self._prefetch_worker,
                    name="littleflash-audio-prefetch",
                    daemon=True,
                )
                self._prefetch_thread.start()
        if queued:
            logger.info("音频预缓存：本次入队 %s 个单词", queued)
        return queued

    def _prefetch_worker(self) -> None:
        while True:
            with self._prefetch_guard:
                if not self._prefetch_queue:
                    self._prefetch_thread = None
                    return
                if self._cached_count() >= self._cache_limit:
                    logger.info(
                        "音频缓存已达上限 %s，停止预缓存", self._cache_limit
                    )
                    self._prefetch_queue.clear()
                    self._prefetch_thread = None
                    return
                word = self._prefetch_queue.popleft()
                interval = self._prefetch_interval
            try:
                self.get(word)
            except Exception as exc:  # noqa: BLE001 - 预热失败不应影响服务
                with self._prefetch_guard:
                    self._prefetch_seen.discard(word)
                logger.debug("音频预缓存失败 %s：%s", word, exc)
            if interval > 0:
                time.sleep(interval)

    def cache_path(self, word: str) -> Path | None:
        canonical = self.manager.canonical_audio_word(word)
        return self._cache_path(canonical) if canonical else None

    def _cache_path(self, canonical: str) -> Path:
        cache_key = hashlib.sha256(
            f"youdao:{self.config.voice_type}:{canonical}".encode("utf-8")
        ).hexdigest()
        return self.config.cache_dir / f"{cache_key}.mp3"

    def _is_fresh(self, path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return False
        if stat.st_size <= 0:
            return False
        return (time.time() - stat.st_mtime) < self.config.cache_ttl_seconds

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _download(self, word: str, destination: Path) -> None:
        query = urllib.parse.urlencode(
            {"audio": word, "type": self.config.voice_type}
        )
        url = f"{YOUDAO_ENDPOINT}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.1",
                "Referer": "https://dict.youdao.com/",
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        try:
            with opener.open(
                request, timeout=self.config.timeout_seconds
            ) as response:
                final_host = urllib.parse.urlsplit(response.geturl()).hostname
                if not _is_youdao_host(final_host):
                    raise AudioError("发音服务返回了不可信的跳转")
                if response.status != 200:
                    raise AudioError("发音服务暂时不可用")
                content_type = (response.headers.get("Content-Type") or "").lower()
                if content_type and not (
                    content_type.startswith("audio/")
                    or content_type.startswith("application/octet-stream")
                ):
                    raise AudioError("发音服务返回了非音频内容")
                declared_length = response.headers.get("Content-Length")
                if declared_length and declared_length.isdigit():
                    if int(declared_length) > self.config.max_bytes:
                        raise AudioError("发音文件过大")
                payload = self._read_limited(response)
        except AudioError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("获取有道发音失败（%s）：%s", word, exc)
            raise AudioError("发音服务暂时不可用") from exc

        if len(payload) < 256:
            raise AudioError("发音文件为空或已损坏")
        # Youdao serves MP3. Reject an HTML error page that happened to arrive
        # with a permissive content type.
        looks_like_mp3 = payload.startswith(b"ID3") or (
            len(payload) > 1
            and payload[0] == 0xFF
            and (payload[1] & 0xE0) == 0xE0
        )
        if not looks_like_mp3:
            raise AudioError("发音服务返回了非音频内容")
        self._write_cache(destination, payload)

    def _read_limited(self, response) -> bytes:  # type: ignore[no-untyped-def]
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > self.config.max_bytes:
                raise AudioError("发音文件过大")
            chunks.append(chunk)
        return b"".join(chunks)

    def _write_cache(self, destination: Path, payload: bytes) -> None:
        existed = destination.is_file()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=destination.parent,
                prefix=".audio.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                if "temporary" in locals():
                    os.unlink(temporary)
            except OSError:
                pass
            logger.warning("写入发音缓存失败：%s", exc)
            raise AudioError("服务器无法缓存发音文件") from exc
        if not existed:
            with self._prefetch_guard:
                if self._cache_count is not None:
                    self._cache_count += 1
