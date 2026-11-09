"""Retain authenticated remote worker bundles, pruning only safely idle old caches.

The caller holds the active bundle lock throughout the worker lifetime. Legacy
caches are adopted conservatively: an absent last-used marker starts a fresh
30-day retention window rather than guessing when that cache was last used.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import os
from pathlib import Path
import re
import shutil
from typing import Iterator

RETENTION_DAYS = 30
INCOMPLETE_RETENTION_HOURS = 24
_SHARED_ENVS = ".shared-envs"
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
            if nonblocking and (getattr(exc, "winerror", None) in (33, 36)
                                or exc.errno in (errno.EACCES, errno.EAGAIN)):
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


def prune_incomplete_worker_cache(
    base: Path, active_digest: str, *, now: datetime | None = None,
) -> list[Path]:
    """Remove abandoned incomplete source bundles, not active or recent ones."""
    if _DIGEST.fullmatch(active_digest) is None:
        raise ValueError("remote worker bundle digest must be lowercase SHA-256")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = now.timestamp() - timedelta(hours=INCOMPLETE_RETENTION_HOURS).total_seconds()
    removed: list[Path] = []
    for candidate in base.iterdir():
        if candidate.name == active_digest or _DIGEST.fullmatch(candidate.name) is None:
            continue
        if candidate.is_symlink() or not candidate.is_dir() or (candidate / ".installed-ok").is_file():
            continue
        if candidate.stat().st_mtime >= cutoff:
            continue
        with _bundle_lock(candidate, nonblocking=True) as acquired:
            if not acquired or not candidate.is_dir() or (candidate / ".installed-ok").is_file():
                continue
            if candidate.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(candidate)
            removed.append(candidate)
    return removed


def prune_replaced_dependency_envs(base: Path, active_env_digest: str) -> list[Path]:
    """Delete obsolete completed dependency environments after the new one is ready.

    Every running modern worker holds an env lock. Older environments currently
    in use are skipped, never forcibly removed.
    """
    if _DIGEST.fullmatch(active_env_digest) is None:
        raise ValueError("remote environment digest must be lowercase SHA-256")
    root = base / _SHARED_ENVS
    if not root.is_dir():
        raise FileNotFoundError(f"shared remote environment directory missing: {root}")
    active = root / active_env_digest
    if not (active / ".installed-ok").is_file():
        raise RuntimeError("active remote dependency environment is incomplete")
    removed: list[Path] = []
    for candidate in root.iterdir():
        if candidate.name == active_env_digest or _DIGEST.fullmatch(candidate.name) is None:
            continue
        if candidate.is_symlink() or not candidate.is_dir() or not (candidate / ".installed-ok").is_file():
            continue
        with _bundle_lock(candidate, nonblocking=True) as acquired:
            if not acquired:
                continue
            if not candidate.is_dir() or not (candidate / ".installed-ok").is_file():
                continue
            shutil.rmtree(candidate)
            removed.append(candidate)
    return removed


@contextmanager
def maintain_remote_worker_cache(
    base: Path, digest: str, *, environment_digest: str | None = None,
) -> Iterator[None]:
    """Protect active source/environment and prune safely idle obsolete caches."""
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
        if environment_digest is None:
            environment = None
        else:
            if _DIGEST.fullmatch(environment_digest) is None:
                raise ValueError("remote dependency environment digest must be lowercase SHA-256")
            environment = base / _SHARED_ENVS / environment_digest
            if environment.is_symlink() or not (environment / ".installed-ok").is_file():
                raise RuntimeError("active remote dependency environment is incomplete")
        from contextlib import nullcontext
        env_lock = _bundle_lock(environment, nonblocking=True) if environment is not None else nullcontext(True)
        with env_lock as env_acquired:
            if not env_acquired:
                raise RuntimeError("remote dependency environment is already locked")
            deleted = prune_remote_worker_cache(base, digest)
            deleted.extend(prune_incomplete_worker_cache(base, digest))
            if environment_digest is not None:
                deleted.extend(prune_replaced_dependency_envs(base, environment_digest))
            for directory in deleted:
                print(f"Removed expired or superseded remote worker cache: {directory}", flush=True)
            try:
                yield
            finally:
                marker.touch()
