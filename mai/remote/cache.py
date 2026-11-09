"""Retain authenticated remote worker bundles, pruning only safely idle old caches.

The caller holds the active bundle lock throughout the worker lifetime. Legacy
caches are adopted conservatively: an absent last-used marker starts a fresh
30-day retention window rather than guessing when that cache was last used.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
from typing import Iterator

RETENTION_DAYS = 30
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LOCK_NAME = ".active.lock"
_LAST_USED_NAME = ".last-used"


def _lock(file, *, nonblocking: bool) -> bool:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK
        try:
            msvcrt.locking(file.fileno(), mode, 1)
        except OSError as exc:
            if nonblocking and getattr(exc, "winerror", None) in (33, 36):
                return False
            raise
    else:
        import fcntl

        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(file.fileno(), flags)
        except BlockingIOError:
            if nonblocking:
                return False
            raise
    return True


def _unlock(file) -> None:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _bundle_lock(directory: Path, *, nonblocking: bool) -> Iterator[bool]:
    lock_dir = directory.parent / ".cache-locks"
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / (directory.name + _LOCK_NAME)
    with lock_path.open("a+b") as file:
        if file.seek(0, os.SEEK_END) == 0:
            file.write(b"\\0")
            file.flush()
        acquired = _lock(file, nonblocking=nonblocking)
        try:
            yield acquired
        finally:
            if acquired:
                _unlock(file)


def prune_remote_worker_cache(
    base: Path, active_digest: str, *, now: datetime | None = None,
) -> list[Path]:
    """Remove old completed hash directories, never the current or locked one."""
    if _DIGEST.fullmatch(active_digest) is None:
        raise ValueError("remote worker bundle digest must be lowercase SHA-256")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = now.timestamp() - timedelta(days=RETENTION_DAYS).total_seconds()
    deleted: list[Path] = []
    for candidate in base.iterdir():
        if candidate.name == active_digest or _DIGEST.fullmatch(candidate.name) is None:
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        if not (candidate / ".installed-ok").is_file():
            # Partial installations are not completed cache entries.
            continue
        last_used = candidate / _LAST_USED_NAME
        if last_used.is_symlink():
            continue
        if not last_used.exists():
            # Pre-retention versions have no trustworthy last-use timestamp.
            last_used.touch()
            continue
        if last_used.stat().st_mtime >= cutoff:
            continue
        with _bundle_lock(candidate, nonblocking=True) as acquired:
            if not acquired:
                continue
            if not (candidate / ".installed-ok").is_file():
                continue
            if last_used.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(candidate)
            deleted.append(candidate)
    return deleted


@contextmanager
def maintain_remote_worker_cache(base: Path, digest: str) -> Iterator[None]:
    """Lock the active bundle for the entire session and update its last-use."""
    if _DIGEST.fullmatch(digest) is None:
        raise ValueError("remote worker bundle digest must be lowercase SHA-256")
    base = base.resolve(strict=True)
    active = base / digest
    if active.is_symlink() or not active.is_dir() or not (active / ".installed-ok").is_file():
        raise RuntimeError("remote worker cache is not a completed bundle")
    with _bundle_lock(active, nonblocking=True) as acquired:
        if not acquired:
            raise RuntimeError("remote worker bundle is already locked by another process")
        marker = active / _LAST_USED_NAME
        if marker.is_symlink():
            raise RuntimeError("remote worker last-used marker must not be a symbolic link")
        marker.touch()
        removed = prune_remote_worker_cache(base, digest)
        for directory in removed:
            print(f"Removed expired remote worker cache: {directory}", flush=True)
        try:
            yield
        finally:
            marker.touch()
