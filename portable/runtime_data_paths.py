"""One-time non-destructive relocation of MAI runtime data directories.

No deletion, overwriting or merging of user data. Invoked before service startup
on both Portable launchers; can also be run manually while MAI is stopped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

MEMORY_RELATIVE = Path("mai/mcp_tools/mcp_memorizer/MAI_MEMORY")
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


def _update_memorizer_config(root: Path) -> bool:
    config_file = root / "mai/mcp_tools/mcp_servers.json"
    if not config_file.is_file():
        return False
    config = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("servers"), list):
        raise DataPathMigrationError(f"Invalid MCP config; existing file left unchanged: {config_file}")
    prior = (root / "MAI_MEMORY").resolve()
    target = (root / MEMORY_RELATIVE).resolve()
    changed = False
    for entry in config["servers"]:
        if not isinstance(entry, dict) or entry.get("id") != "memorizer":
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            continue
        configured = env.get("MEMORIZER_DATA_DIR")
        if isinstance(configured, str) and Path(configured).expanduser().resolve() == prior:
            env["MEMORIZER_DATA_DIR"] = str(target)
            changed = True
    if changed:
        # Preserve the operator's original settings and enabled toggles.
        backup = config_file.with_name("mcp_servers.pre-data-path-migration.json")
        if not backup.exists():
            shutil.copy2(config_file, backup)
        temp = config_file.with_suffix(".migration.tmp")
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(config_file)
    return changed


def migrate(root: Path) -> dict[str, bool]:
    root = root.expanduser().resolve()
    if not (root / "mai").is_dir():
        raise DataPathMigrationError(f"Not an MAI project root: {root}")
    moved = {
        "memory": _relocate(root / "MAI_MEMORY", root / MEMORY_RELATIVE),
        "uploads": _relocate(root / "mai_uploads", root / UPLOAD_RELATIVE),
    }
    moved["memorizer_config_updated"] = _update_memorizer_config(root)
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
