"""Authorize the MAI owner's Google account locally with desktop OAuth.

Run on the MAI host. Never commit client secrets or the resulting token.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from mai.tools.google_workspace import SCOPES


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize Google Drive / Sheets for MAI owner")
    parser.add_argument("--client-secret", required=True, type=Path)
    parser.add_argument("--token", required=True, type=Path)
    args = parser.parse_args()
    if not args.client_secret.is_file():
        parser.error("OAuth desktop client JSON does not exist")
    if args.token.exists():
        parser.error("Token path already exists; refusing to overwrite credentials")
    if not args.token.parent.is_dir():
        parser.error("Token parent directory must already exist")
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), list(SCOPES))
    credentials = flow.run_local_server(host="localhost", port=0, open_browser=True)
    args.token.write_text(credentials.to_json(), encoding="utf-8")
    print("Google OAuth token saved at:", args.token)


if __name__ == "__main__":
    main()
