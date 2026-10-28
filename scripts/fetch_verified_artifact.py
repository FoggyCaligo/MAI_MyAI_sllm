"""Download a pinned release archive only when its SHA-256 matches the supplied digest.

Usage: python scripts/fetch_verified_artifact.py URL SHA256 DESTINATION
The caller is responsible for pinning the URL and independently obtaining a trusted digest.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen


def fetch_verified(url: str, sha256: str, destination: Path) -> None:
    if len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = hashlib.file_digest(destination.open("rb"), "sha256").hexdigest()
        if digest.lower() != sha256.lower():
            raise ValueError(f"Existing file digest mismatch: {destination}")
        return
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temp_path = Path(temporary.name)
        h = hashlib.sha256()
        try:
            with urlopen(url, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
                    h.update(chunk)
            if h.hexdigest().lower() != sha256.lower():
                raise ValueError(f"SHA-256 mismatch for {url}")
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    temp_path.replace(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("sha256")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    fetch_verified(args.url, args.sha256, args.destination)
