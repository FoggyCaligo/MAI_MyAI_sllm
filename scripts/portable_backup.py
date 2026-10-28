"""Create a verified, consistent MAI portable snapshot on a different disk.

Run while MAI is stopped. SQLite databases use the online backup API rather
than copying live WAL files. All files are copied to a temporary directory
and the snapshot becomes visible only after a successful complete copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone


IGNORED_DIRS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"})
IGNORED_FILES = frozenset({"Thumbs.db"})
IGNORED_SUFFIXES = ("-wal", "-shm")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def backup_sqlite(source: Path, destination: Path) -> None:
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30) as original:
        with sqlite3.connect(destination) as copy:
            original.backup(copy)
            check = copy.execute("PRAGMA integrity_check").fetchone()
            if check != ("ok",):
                raise RuntimeError(f"SQLite integrity failure: {source} => {check}")


def create_snapshot(root: Path, destination_root: Path) -> Path:
    root = root.resolve()
    destination_root = destination_root.resolve()
    if destination_root == root or root in destination_root.parents or destination_root in root.parents:
        raise ValueError("Backup destination must be on a separate directory tree")
    if not (root / "run_server.py").is_file() or not (root / ".env").is_file():
        raise FileNotFoundError("Expected MAI root with run_server.py and .env")
    if not (root / "portable-runtimes").is_dir() or not (root / "portable-models").is_dir():
        raise FileNotFoundError("Portable runtime and model directories must exist before backup")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination_root / f"mai-{stamp}"
    staging = destination_root / f".mai-{stamp}.incomplete"
    if staging.exists() or final.exists():
        raise FileExistsError("Snapshot destination already exists")
    staging.mkdir(parents=True)
    hashes: dict[str, str] = {}
    try:
        for directory, dir_names, file_names in os.walk(root):
            current = Path(directory)
            dir_names[:] = [name for name in dir_names if name not in IGNORED_DIRS]
            relative_dir = current.relative_to(root)
            target_dir = staging / relative_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename in file_names:
                if filename in IGNORED_FILES or filename.endswith(IGNORED_SUFFIXES):
                    continue
                source = current / filename
                if source.is_symlink():
                    raise ValueError(f"Portable backups cannot contain symlinks: {source}")
                target = target_dir / filename
                if source.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                    backup_sqlite(source, target)
                else:
                    shutil.copy2(source, target)
                hashes[target.relative_to(staging).as_posix()] = sha256(target)
        manifest = {"snapshot_version": 1, "created_at_utc": stamp, "files": hashes}
        (staging / "backup-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(create_snapshot(args.source, args.destination))


if __name__ == "__main__":
    main()
