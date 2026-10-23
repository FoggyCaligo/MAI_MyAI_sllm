"""Short-lived bearer download links for owner-requested local files."""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote


class DownloadLinkStore:
    def __init__(self, *, ttl_seconds: int = 3600) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._links: dict[str, tuple[Path, float]] = {}

    def create(self, path: str, *, base_url: str = "") -> dict[str, object]:
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_file():
            raise IsADirectoryError(str(target))
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + self._ttl
        with self._lock:
            self._links[token] = (target, expires_at)
        relative_url = "/download/" + quote(token, safe="")
        url = base_url.rstrip("/") + relative_url if base_url else relative_url
        return {
            "url": url,
            "markdown_link": f"[파일 다운로드]({url})",
            "path": str(target),
            "expires_at_unix": expires_at,
        }

    def resolve(self, token: str) -> Path | None:
        with self._lock:
            record = self._links.get(token)
            if record is None:
                return None
            path, expires_at = record
            if time.time() >= expires_at:
                del self._links[token]
                return None
        # Files may be removed after link creation; allow that failure to be
        # reported by the serving endpoint, never substitute another file.
        return path
