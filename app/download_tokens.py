from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DownloadToken:
    token: str
    path: Path
    filename: str
    created_at: float
    expires_at: float
    size_bytes: int


class DownloadTokenStore:
    """Short-lived, in-memory file download tokens."""

    def __init__(self, default_ttl_seconds: int = 3600) -> None:
        self._default_ttl_seconds = max(1, int(default_ttl_seconds))
        self._tokens: dict[str, DownloadToken] = {}
        self._lock = threading.Lock()

    def create(self, path: Path | str, ttl_seconds: int | None = None) -> DownloadToken:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("Download target must be a file")
        ttl = self._default_ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        now = time.time()
        item = DownloadToken(
            token=secrets.token_urlsafe(32),
            path=resolved,
            filename=resolved.name,
            created_at=now,
            expires_at=now + ttl,
            size_bytes=resolved.stat().st_size,
        )
        with self._lock:
            self._cleanup_expired_locked(now)
            self._tokens[item.token] = item
        return item

    def resolve(self, token: str, *, consume: bool = False) -> DownloadToken | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._cleanup_expired_locked(now)
            item = self._tokens.get(token)
            if item is None:
                return None
            if consume:
                self._tokens.pop(token, None)
            return item

    def revoke(self, token: str) -> bool:
        with self._lock:
            return self._tokens.pop(token, None) is not None

    def _cleanup_expired_locked(self, now: float) -> None:
        for token, item in list(self._tokens.items()):
            if item.expires_at <= now:
                self._tokens.pop(token, None)


default_download_token_store = DownloadTokenStore()
