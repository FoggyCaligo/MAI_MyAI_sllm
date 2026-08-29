"""Reset one configured MAI trial account for reuse.

Run this while the MAI server is stopped. The reset removes only memory owned by
the selected trial identity, plus concept nodes that become completely orphaned
after that user's memory is removed. Shared concepts used by another user's
memory are preserved.

Chat/login sessions are process-local and therefore disappear when the stopped
server is started again.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

from mai.app.access import AccessPolicy, AccessRole


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset one configured MAI trial account so it can be reused.",
    )
    parser.add_argument("trial_id", help="Exact trial ID listed in TRIAL_IDS")
    parser.add_argument(
        "--db",
        default=None,
        help="Memory SQLite path. Defaults to MEMORY_DB_PATH or ./data/memory.sqlite3.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without changing the database.",
    )
    return parser.parse_args()


def _load_trial_principal(trial_id: str):
    policy = AccessPolicy.from_env_values(
        owner_id=os.environ.get("OWNER_ID"),
        owner_memory_id=os.environ.get("OWNER_MEMORY_ID"),
        trial_ids=os.environ.get("TRIAL_IDS"),
    )
    principal = policy.authenticate(trial_id)
    if principal.role is not AccessRole.TRIAL:
        raise ValueError("reset_trial.py only accepts IDs configured as trial accounts")
    return principal


def _json_payload(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _collect_reset_targets(connection: sqlite3.Connection, user_id: str) -> dict[str, set[int]]:
    anchor_row = connection.execute(
        "SELECT node_id FROM user_anchors WHERE user_id = ?",
        (user_id,),
    ).fetchone()
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

    return {
        "anchor_ids": anchor_ids,
        "owned_node_ids": owned_node_ids,
        "evidence_ids": evidence_ids,
    }


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
    connection.execute(
        f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
        tuple(sorted(ids)),
    )


def _orphan_concept_ids(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute(
        """
        SELECT n.id
        FROM nodes n
        WHERE n.node_type = 'concept'
          AND NOT EXISTS (
              SELECT 1 FROM edges e
              WHERE e.from_node_id = n.id OR e.to_node_id = n.id
          )
        """
    ).fetchall()
    return {int(row[0]) for row in rows}


def _reset_memory(db_path: Path, user_id: str, *, dry_run: bool) -> dict[str, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"memory database does not exist: {db_path}")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        targets = _collect_reset_targets(connection, user_id)
        owned_node_ids = targets["owned_node_ids"]
        evidence_ids = targets["evidence_ids"]
        edge_count = _count_edges_touching(connection, owned_node_ids)

        if dry_run:
            return {
                "owned_nodes": len(owned_node_ids),
                "edges": edge_count,
                "evidence": len(evidence_ids),
                "orphan_concepts": 0,
            }

        with connection:
            # Deleting owned nodes cascades user_anchors and all touching edges.
            _delete_ids(connection, "nodes", "id", owned_node_ids)
            _delete_ids(connection, "evidence", "id", evidence_ids)

            # Concepts are global/deduplicated. Remove only those no longer connected
            # to any user's remaining memory, and keep the persisted lookup index in sync.
            orphan_ids = _orphan_concept_ids(connection)
            if orphan_ids:
                placeholders = ",".join("?" for _ in orphan_ids)
                params = tuple(sorted(orphan_ids))
                connection.execute(
                    f"DELETE FROM memory_concept_fts WHERE rowid IN ({placeholders})",
                    params,
                )
                connection.execute(
                    f"DELETE FROM memory_concept_exact WHERE node_id IN ({placeholders})",
                    params,
                )
                connection.execute(
                    f"DELETE FROM nodes WHERE id IN ({placeholders})",
                    params,
                )

        return {
            "owned_nodes": len(owned_node_ids),
            "edges": edge_count,
            "evidence": len(evidence_ids),
            "orphan_concepts": len(orphan_ids),
        }
    finally:
        connection.close()


def main() -> int:
    load_dotenv()
    args = _parse_args()

    try:
        principal = _load_trial_principal(args.trial_id)
    except Exception as exc:
        print(f"Refusing reset: {exc}", file=sys.stderr)
        return 2

    db_path = Path(
        args.db or os.environ.get("MEMORY_DB_PATH", "./data/memory.sqlite3")
    ).expanduser().resolve()

    try:
        preview = _reset_memory(db_path, principal.memory_user_id, dry_run=True)
    except Exception as exc:
        print(f"Could not inspect trial memory: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"Trial account : {principal.auth_user_id}")
    print(f"Memory user   : {principal.memory_user_id}")
    print(f"Database      : {db_path}")
    print(f"Owned nodes   : {preview['owned_nodes']}")
    print(f"Touching edges: {preview['edges']}")
    print(f"Evidence rows : {preview['evidence']}")

    if args.dry_run:
        print("Dry run only; nothing was deleted.")
        return 0

    if not args.yes:
        confirmation = input(
            f"Type the trial ID '{principal.auth_user_id}' again to permanently reset it: "
        ).strip()
        if confirmation != principal.auth_user_id:
            print("Reset cancelled.")
            return 1

    try:
        result = _reset_memory(db_path, principal.memory_user_id, dry_run=False)
    except sqlite3.OperationalError as exc:
        print(
            "Reset failed. Make sure the MAI server is stopped before running this utility. "
            f"SQLite error: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(f"Reset failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("Reset complete.")
    print(f"Deleted owned nodes : {result['owned_nodes']}")
    print(f"Removed edges       : {result['edges']}")
    print(f"Deleted evidence    : {result['evidence']}")
    print(f"Removed orphan concepts: {result['orphan_concepts']}")
    print("Start MAI again before handing the trial ID to the next user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
