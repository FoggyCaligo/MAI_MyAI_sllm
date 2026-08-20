from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from MK5 import config
from MK5.app.accounts import AccountStore
from MK5.app.sessions import SessionStore
from MK5.core.graph.repository import GraphRepository
from MK5.core.graph.service import GraphMemoryService


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect MK5 accounts, sessions, or purge one account's graph memory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List configured roles and graph identities without revealing login IDs.")
    subparsers.add_parser("list-sessions", help="List currently active sessions.")
    subparsers.add_parser("clear-sessions", help="Clear all active login sessions.")

    purge_parser = subparsers.add_parser("purge-memory", help="Delete graph memory for one allowed login ID.")
    purge_parser.add_argument("--login-id", help="Allowed login ID. Omit to enter it without terminal echo.")

    args = parser.parse_args()
    store = AccountStore()
    if args.command == "list":
        print(json.dumps(store.list_accounts(), ensure_ascii=False, indent=2))
        return

    if args.command == "list-sessions":
        session_store = SessionStore(
            ttl_seconds=config.SESSION_TTL_HOURS * 3600,
            max_active_sessions=config.MAX_ACTIVE_SESSIONS,
            path=config.SESSIONS_DB_PATH,
        )
        try:
            print(json.dumps(session_store.list_active(), ensure_ascii=False, indent=2))
        finally:
            session_store.close()
        return

    if args.command == "clear-sessions":
        session_store = SessionStore(
            ttl_seconds=config.SESSION_TTL_HOURS * 3600,
            max_active_sessions=config.MAX_ACTIVE_SESSIONS,
            path=config.SESSIONS_DB_PATH,
        )
        try:
            cleared = session_store.clear_all()
            print(f"Cleared {cleared} active sessions.")
        finally:
            session_store.close()
        return

    login_id = args.login_id or getpass.getpass("Login ID whose graph memory will be deleted: ")
    account = store.authenticate(login_id)
    if account is None:
        raise SystemExit("The login ID is not present in MK5_ALLOWED_LOGIN_IDS.")
    confirmation = input(f"Type DELETE {account.graph_user_id} to erase this graph memory: ")
    if confirmation != f"DELETE {account.graph_user_id}":
        raise SystemExit("Cancelled; confirmation did not match.")

    repo = GraphRepository()
    try:
        result = GraphMemoryService(repo).delete_user_memory(account.graph_user_id)
    finally:
        repo.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
