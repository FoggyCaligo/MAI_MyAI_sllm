import hashlib
import json
from pathlib import Path

import pytest

from scripts.copy_ollama_models import copy_models


def add_model(root: Path, name: str, blob: bytes) -> Path:
    digest = hashlib.sha256(blob).hexdigest()
    source_blob = root / "blobs" / f"sha256-{digest}"
    source_blob.parent.mkdir(parents=True, exist_ok=True)
    source_blob.write_bytes(blob)
    name_part, tag = name.split(":")
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / name_part / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schemaVersion": 2,
        "config": {"digest": f"sha256:{digest}", "size": len(blob)},
        "layers": [{"digest": f"sha256:{digest}", "size": len(blob)}],
    }))
    return manifest


def test_selected_models_only_and_idempotent(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    add_model(src, "ornith-1.5:9b", b"shared")
    add_model(src, "gemma4:e4b", b"shared")
    add_model(src, "lfm2.5:8b", b"other")
    add_model(src, "unused:latest", b"unused")
    names = ["ornith-1.5:9b", "gemma4:e4b", "lfm2.5:8b"]
    copy_models(src, dst, names)
    copy_models(src, dst, names)
    assert len(list((dst / "blobs").iterdir())) == 2
    assert not (dst / "manifests" / "registry.ollama.ai" / "library" / "unused").exists()


def test_missing_source_blob_does_not_publish_manifest(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    manifest = add_model(src, "ornith-1.5:9b", b"model")
    (src / "blobs" / f"sha256-{hashlib.sha256(b'model').hexdigest()}").unlink()
    with pytest.raises(ValueError, match="Missing or wrong-sized blob"):
        copy_models(src, dst, ["ornith-1.5:9b"])
    assert not (dst / "manifests").exists()


def test_corrupt_existing_blob_fails_without_overwrite(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    add_model(src, "ornith-1.5:9b", b"good")
    digest = hashlib.sha256(b"good").hexdigest()
    bad = dst / "blobs" / f"sha256-{digest}"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"bad!")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        copy_models(src, dst, ["ornith-1.5:9b"])
    assert bad.read_bytes() == b"bad!"


@pytest.mark.parametrize("model", ["../bad:tag", "model", "name:../bad", "a:b:c"])
def test_reject_unsafe_names(tmp_path: Path, model: str):
    with pytest.raises(ValueError):
        copy_models(tmp_path / "src", tmp_path / "dst", [model])
