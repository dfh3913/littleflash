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
from dataclasses import dataclass
from pathlib import Path

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

    def get(self, word: str) -> AudioAsset:
        if not self.config.enabled:
            raise AudioDisabled()
        canonical = self.manager.canonical_audio_word(word)
        if canonical is None:
            raise UnknownAudioWord()

        cache_key = hashlib.sha256(
            f"youdao:{self.config.voice_type}:{canonical}".encode("utf-8")
        ).hexdigest()
        cached = self.config.cache_dir / f"{cache_key}.mp3"
        if self._is_fresh(cached):
            return AudioAsset(cached)

        lock = self._lock_for(cache_key)
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
