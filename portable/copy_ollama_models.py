"""Copy selected Ollama manifests and referenced content-addressed blobs.

Only standard-library modules are used. A copied manifest is published last,
after every referenced blob passes size and SHA-256 verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


MODEL_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DIGEST = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


def model_manifest_path(root: Path, model: str) -> Path:
    if model.count(":") != 1:
        raise ValueError(f"Model must include an explicit name and tag: {model!r}")
    name, tag = model.split(":")
    components = name.split("/")
    if not components or not all(MODEL_PART.fullmatch(part) for part in (*components, tag)):
        raise ValueError(f"Unsafe or unsupported Ollama model name: {model!r}")
    return root / "manifests" / "registry.ollama.ai" / "library" / Path(*components) / tag


def manifest_blobs(manifest: Path) -> dict[str, int]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Invalid Ollama manifest: {manifest}")
    descriptors = [document.get("config"), *(document.get("layers") if isinstance(document.get("layers"), list) else [])]
    if not isinstance(document.get("config"), dict) or not isinstance(document.get("layers"), list):
        raise ValueError(f"Missing config or layers in {manifest}")
    blobs: dict[str, int] = {}
    for item in descriptors:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid layer descriptor in {manifest}")
        digest = item.get("digest")
        size = item.get("size")
        match = DIGEST.fullmatch(digest) if isinstance(digest, str) else None
        if match is None or type(size) is not int or size < 0:
            raise ValueError(f"Invalid SHA-256 or size in {manifest}")
        digest_hex = match.group(1).lower()
        if digest_hex in blobs and blobs[digest_hex] != size:
            raise ValueError(f"Inconsistent digest size in {manifest}")
        blobs[digest_hex] = size
    return blobs


def verify_blob(path: Path, expected_digest: str, expected_size: int) -> None:
    if not path.is_file() or path.stat().st_size != expected_size:
        raise ValueError(f"Missing or wrong-sized blob: {path}")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    if hasher.hexdigest() != expected_digest:
        raise ValueError(f"SHA-256 mismatch for blob: {path}")


def atomic_copy_verified(source: Path, target: Path, digest: str, size: int) -> None:
    if target.exists():
        verify_blob(target, digest, size)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mai-blob-", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copyfile(source, temp)
        verify_blob(temp, digest, size)
        if target.exists():
            verify_blob(target, digest, size)
        else:
            os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def copy_models(source_root: Path, target_root: Path, names: list[str]) -> None:
    if not names:
        raise ValueError("At least one model name is required")
    if source_root.resolve() == target_root.resolve():
        raise ValueError("Source and destination models directories are identical")
    pending: list[tuple[Path, Path]] = []
    blobs: dict[str, int] = {}
    for name in dict.fromkeys(names):
        source_manifest = model_manifest_path(source_root, name)
        if not source_manifest.is_file():
            raise FileNotFoundError(f"Missing source model manifest: {source_manifest}")
        entries = manifest_blobs(source_manifest)
        for digest, size in entries.items():
            if digest in blobs and blobs[digest] != size:
                raise ValueError(f"Conflicting blob size for SHA-256 {digest}")
            blobs[digest] = size
        pending.append((source_manifest, model_manifest_path(target_root, name)))

    # Verify all source data before publishing any manifests.
    for digest, size in blobs.items():
        verify_blob(source_root / "blobs" / f"sha256-{digest}", digest, size)

    for digest, size in blobs.items():
        atomic_copy_verified(
            source_root / "blobs" / f"sha256-{digest}",
            target_root / "blobs" / f"sha256-{digest}",
            digest,
            size,
        )
        print(f"Verified blob sha256:{digest}")

    for source_manifest, target_manifest in pending:
        content = source_manifest.read_bytes()
        if target_manifest.exists():
            if target_manifest.read_bytes() != content:
                raise ValueError(f"Destination manifest differs; refusing overwrite: {target_manifest}")
            print(f"Reusing model manifest: {target_manifest}")
            continue
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".mai-manifest-", dir=target_manifest.parent)
        os.close(fd)
        temp = Path(temp_name)
        try:
            temp.write_bytes(content)
            os.replace(temp, target_manifest)
        finally:
            temp.unlink(missing_ok=True)
        print(f"Copied model manifest: {target_manifest}")
    print(f"Verified {len(blobs)} unique blobs for {len(pending)} selected models")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True)
    args = parser.parse_args()
    copy_models(args.source, args.destination, args.model)


if __name__ == "__main__":
    main()
