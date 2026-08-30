"""Reset one configured MAI trial account for reuse.

Run this while the MAI server is stopped. Memory, persisted chat history, and
isolated uploads are owned by the account's stable db_id, not its mutable login
user_id.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from mai.app.access import AccessPolicy, AccessRole
from mai.app.uploads import trial_upload_directory


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset one configured MAI trial account so it can be reused.")
    parser.add_argument("trial_id", help="Exact user_id configured in TRIAL_USERS")
    parser.add_argument("--db", default=None, help="Memory SQLite path. Defaults to MEMORY_DB_PATH or ./data/memory.sqlite3.")
    parser.add_argument("--chat-db", default=None, help="Chat SQLite path. Defaults to CHAT_DB_PATH or ./data/chat.sqlite3.")
    parser.add_argument("--upload-root", default=None, help="Upload root. Defaults to MAI_UPLOAD_ROOT or ./mai_uploads.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without changing data.")
    return parser.parse_args()


def _load_trial_principal(trial_id: str):
    policy = AccessPolicy.from_env_values(
        owner_users=os.environ.get("OWNER_USERS"),
        trial_users=os.environ.get("TRIAL_USERS"),
    )
    principal = policy.configured_principal(trial_id)
    if principal.role is not AccessRole.TRIAL:
        raise ValueError("reset_trial.py only accepts user_id values configured in TRIAL_USERS")
    return principal


def _server_is_listening() -> tuple[bool, str, int]:
    host = os.environ.get("MAI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("MAI_PORT", "8000"))
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.35):
            return True, probe_host, port
    except OSError:
        return False, probe_host, port


def _json_payload(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _collect_reset_targets(connection: sqlite3.Connection, user_id: str) -> dict[str, set[int]]:
    anchor_row = connection.execute("SELECT node_id FROM user_anchors WHERE user_id = ?", (user_id,)).fetchone()
    anchor_ids = {int(anchor_row[0])} if anchor_row is not None else set()
    owned_node_ids: set[int] = set(anchor_ids)
    evidence_ids: set[int] = set()
    rows = connection.execute(
        "SELECT id, node_type, payload_json FROM nodes WHERE node_type IN ('utterance', 'fact')"
    ).fetchall()
    for row in rows:
        payload = _json_payload(str(row[2]))
        if payload.get("user_id") != user_id:
            continue
        node_id = int(row[0])
        owned_node_ids.add(node_id)
        if str(row[1]) == "utterance":
            evidence_id = payload.get("evidence_id")
            if isinstance(evidence_id, int) and evidence_id > 0:
                evidence_ids.add(evidence_id)
    return {"anchor_ids": anchor_ids, "owned_node_ids": owned_node_ids, "evidence_ids": evidence_ids}


def _count_edges_touching(connection: sqlite3.Connection, node_ids: set[int]) -> int:
    if not node_ids:
        return 0
    placeholders = ",".join("?" for _ in node_ids)
    params = tuple(sorted(node_ids))
    row = connection.execute(
        f"SELECT COUNT(*) FROM edges WHERE from_node_id IN ({placeholders}) OR to_node_id IN ({placeholders})",
        params + params,
    ).fetchone()
    return int(row[0])


def _delete_ids(connection: sqlite3.Connection, table: str, column: str, ids: set[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(sorted(ids)))


def _orphan_concept_ids(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute(
        """
        SELECT n.id FROM nodes n
        WHERE n.node_type = 'concept'
          AND NOT EXISTS (
              SELECT 1 FROM edges e
              WHERE e.from_node_id = n.id OR e.to_node_id = n.id
          )
        """
    ).fetchall()
    return {int(row[0]) for row in rows}


def _reset_memory(db_path: Path, db_id: str, *, dry_run: bool) -> dict[str, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"memory database does not exist: {db_path}")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        targets = _collect_reset_targets(connection, db_id)
        owned_node_ids = targets["owned_node_ids"]
        evidence_ids = targets["evidence_ids"]
        edge_count = _count_edges_touching(connection, owned_node_ids)
        if dry_run:
            return {"owned_nodes": len(owned_node_ids), "edges": edge_count, "evidence": len(evidence_ids), "orphan_concepts": 0}
        orphan_ids: set[int] = set()
        with connection:
            _delete_ids(connection, "nodes", "id", owned_node_ids)
            _delete_ids(connection, "evidence", "id", evidence_ids)
            orphan_ids = _orphan_concept_ids(connection)
            if orphan_ids:
                placeholders = ",".join("?" for _ in orphan_ids)
                params = tuple(sorted(orphan_ids))
                connection.execute(f"DELETE FROM memory_concept_fts WHERE rowid IN ({placeholders})", params)
                connection.execute(f"DELETE FROM memory_concept_exact WHERE node_id IN ({placeholders})", params)
                connection.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", params)
        return {"owned_nodes": len(owned_node_ids), "edges": edge_count, "evidence": len(evidence_ids), "orphan_concepts": len(orphan_ids)}
    finally:
        connection.close()


def _reset_chat_history(chat_db_path: Path, *, user_id: str, db_id: str, dry_run: bool) -> int:
    if not chat_db_path.exists():
        return 0
    connection = sqlite3.connect(chat_db_path)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chat_messages'"
        ).fetchone()
        if table is None:
            return 0
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(chat_messages)").fetchall()}
        if "db_id" in columns:
            column = "db_id"
            identities = tuple(dict.fromkeys((db_id, user_id)))
        elif "auth_user_id" in columns:
            column = "auth_user_id"
            identities = tuple(dict.fromkeys((user_id, db_id)))
        else:
            raise RuntimeError("chat_messages has neither db_id nor legacy auth_user_id")
        placeholders = ",".join("?" for _ in identities)
        row = connection.execute(
            f"SELECT COUNT(*) FROM chat_messages WHERE {column} IN ({placeholders})",
            identities,
        ).fetchone()
        count = int(row[0])
        if not dry_run and count:
            with connection:
                connection.execute(
                    f"DELETE FROM chat_messages WHERE {column} IN ({placeholders})",
                    identities,
                )
        return count
    finally:
        connection.close()


def _upload_stats(upload_dir: Path) -> tuple[int, int]:
    if not upload_dir.exists():
        return 0, 0
    files = 0
    total_bytes = 0
    for path in upload_dir.rglob("*"):
        if path.is_file():
            files += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return files, total_bytes


def _reset_uploads(upload_dir: Path, *, dry_run: bool) -> dict[str, int]:
    files, total_bytes = _upload_stats(upload_dir)
    if not dry_run and upload_dir.exists():
        shutil.rmtree(upload_dir)
    return {"files": files, "bytes": total_bytes}


def main() -> int:
    load_dotenv()
    args = _parse_args()
    listening, host, port = _server_is_listening()
    if listening:
        print(f"Refusing reset: MAI appears to be running at {host}:{port}. Stop the server first.", file=sys.stderr)
        return 2
    try:
        principal = _load_trial_principal(args.trial_id)
    except Exception as exc:
        print(f"Refusing reset: {exc}", file=sys.stderr)
        return 2

    db_path = Path(args.db or os.environ.get("MEMORY_DB_PATH", "./data/memory.sqlite3")).expanduser().resolve()
    chat_db_path = Path(args.chat_db or os.environ.get("CHAT_DB_PATH", "./data/chat.sqlite3")).expanduser().resolve(strict=False)
    upload_root = Path(args.upload_root or os.environ.get("MAI_UPLOAD_ROOT", "./mai_uploads")).expanduser().resolve(strict=False)
    upload_dir = trial_upload_directory(upload_root, principal.db_id)

    try:
        preview = _reset_memory(db_path, principal.db_id, dry_run=True)
        chat_preview = _reset_chat_history(chat_db_path, user_id=principal.user_id, db_id=principal.db_id, dry_run=True)
        upload_preview = _reset_uploads(upload_dir, dry_run=True)
    except Exception as exc:
        print(f"Could not inspect trial data: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Trial user_id  : {principal.user_id}")
    print(f"Stable db_id   : {principal.db_id}")
    print(f"Memory database: {db_path}")
    print(f"Chat database  : {chat_db_path}")
    print(f"Upload dir     : {upload_dir}")
    print(f"Owned nodes    : {preview['owned_nodes']}")
    print(f"Touching edges : {preview['edges']}")
    print(f"Evidence rows  : {preview['evidence']}")
    print(f"Chat messages  : {chat_preview}")
    print(f"Upload files   : {upload_preview['files']}")
    print(f"Upload bytes   : {upload_preview['bytes']}")

    if args.dry_run:
        print("Dry run only; nothing was deleted.")
        return 0
    if not args.yes:
        confirmation = input(f"Type the trial user_id '{principal.user_id}' again to permanently reset it: ").strip()
        if confirmation != principal.user_id:
            print("Reset cancelled.")
            return 1

    try:
        result = _reset_memory(db_path, principal.db_id, dry_run=False)
        chat_result = _reset_chat_history(chat_db_path, user_id=principal.user_id, db_id=principal.db_id, dry_run=False)
        upload_result = _reset_uploads(upload_dir, dry_run=False)
    except sqlite3.OperationalError as exc:
        print(f"Reset failed. Make sure the MAI server is stopped before running this utility. SQLite error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Reset failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("Reset complete.")
    print(f"Deleted owned nodes     : {result['owned_nodes']}")
    print(f"Removed edges           : {result['edges']}")
    print(f"Deleted evidence        : {result['evidence']}")
    print(f"Removed orphan concepts : {result['orphan_concepts']}")
    print(f"Deleted chat messages   : {chat_result}")
    print(f"Deleted upload files    : {upload_result['files']}")
    print(f"Deleted upload bytes    : {upload_result['bytes']}")
    print("Start MAI again before handing the trial user_id to the next user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
