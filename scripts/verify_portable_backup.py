"""Verify a completed portable MAI backup against its SHA-256 manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(root: Path) -> None:
    root = root.resolve()
    data = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    if data.get("snapshot_version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("Unsupported or malformed backup manifest")
    expected = data["files"]
    observed = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and item.name != "backup-manifest.json"
    }
    if observed != set(expected):
        raise ValueError(f"File set mismatch: missing={sorted(set(expected) - observed)}, unexpected={sorted(observed - set(expected))}")
    for relative, hexdigest in expected.items():
        candidate = (root / relative).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError(f"Invalid snapshot file path: {relative}")
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            while part := handle.read(1024 * 1024):
                digest.update(part)
        if digest.hexdigest() != hexdigest:
            raise ValueError(f"Snapshot checksum mismatch: {relative}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    verify(args.snapshot)
    print("Snapshot integrity verified.")
