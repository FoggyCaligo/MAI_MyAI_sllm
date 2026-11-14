"""One-time non-destructive relocation of MAI runtime data directories.

No deletion, overwriting or merging of user data. Invoked before service startup
on both Portable launchers; can also be run manually while MAI is stopped.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

MEMORY_RELATIVE = Path("MAI_MEMORY")
LEGACY_MEMORY_RELATIVE = Path("mai/mcp_tools/mcp_memorizer/MAI_MEMORY")
UPLOAD_RELATIVE = Path("mai/mai_uploads")


class DataPathMigrationError(RuntimeError):
    pass


def _relocate(old: Path, new: Path) -> bool:
    if not old.exists():
        return False
    if old.is_symlink() or new.is_symlink():
        raise DataPathMigrationError(f"Refusing to move symbolic-link data directory: {old} -> {new}")
    if not old.is_dir():
        raise DataPathMigrationError(f"Expected old data directory, found file: {old}")
    if new.exists():
        if not new.is_dir() or any(new.iterdir()):
            # Empty source is harmless; leave it for operator cleanup.
            if not any(old.iterdir()):
                return False
            raise DataPathMigrationError(
                f"Both old and new data paths exist. Refusing to overwrite or merge: {old} -> {new}. "
                "Stop MAI and reconcile or back up the folders manually."
            )
        new.rmdir()
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        old.rename(new)
    except OSError as exc:
        # Do NOT copy-and-delete, because interruption could leave partial
        # SQLite files and ambiguous concurrent writable copies.
        raise DataPathMigrationError(
            f"Atomic directory rename failed: {old} -> {new}: {exc}. "
            "No copy/delete fallback performed."
        ) from exc
    return True


def normalize_legacy_environment(root: Path) -> dict[str, str]:
    """Redirect only canonical old MAI paths; preserve intentional custom paths."""
    root = root.expanduser().resolve()
    mapping = {
        "MAI_UPLOAD_ROOT": (root / "mai_uploads", root / UPLOAD_RELATIVE),
        "MEMORY_DB_PATH": (root / LEGACY_MEMORY_RELATIVE / "memory.sqlite3",
                           root / MEMORY_RELATIVE / "memory.sqlite3"),
        "SENTENCE_BREAKER_DB_PATH": (root / LEGACY_MEMORY_RELATIVE / "sentence_breaker.sqlite3",
                                     root / MEMORY_RELATIVE / "sentence_breaker.sqlite3"),
        "CHAT_DB_PATH": (root / LEGACY_MEMORY_RELATIVE / "chat.sqlite3",
                         root / MEMORY_RELATIVE / "chat.sqlite3"),
        "LOG_DB_PATH": (root / LEGACY_MEMORY_RELATIVE / "log.sqlite3",
                        root / MEMORY_RELATIVE / "log.sqlite3"),
    }
    changed = {}
    for key, (old, destination) in mapping.items():
        current = os.environ.get(key)
        if current and Path(current).expanduser().resolve() == old.resolve():
            os.environ[key] = str(destination)
            changed[key] = str(destination)
    return changed


def migrate(root: Path) -> dict[str, bool]:
    root = root.expanduser().resolve()
    if not (root / "mai").is_dir():
        raise DataPathMigrationError(f"Not an MAI project root: {root}")
    moved = {
        "memory": _relocate(root / LEGACY_MEMORY_RELATIVE, root / MEMORY_RELATIVE),
        "uploads": _relocate(root / "mai_uploads", root / UPLOAD_RELATIVE),
    }
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="Move legacy MAI data dirs without overwriting")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print("MAI runtime data path migration:", migrate(args.root), flush=True)
    except (DataPathMigrationError, OSError, ValueError) as exc:
        print(f"MAI DATA MIGRATION BLOCKED: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
