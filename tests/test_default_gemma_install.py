from __future__ import annotations
import hashlib
import io
from pathlib import Path
import pytest
from portable import install_default_model as installer


def test_download_then_reuse(monkeypatch, tmp_path: Path):
    data = {name: b"GGUF" + name.encode() for name, _ in installer.ARTIFACTS}
    monkeypatch.setattr(installer, "ARTIFACTS", tuple(
        (name, hashlib.sha256(value).hexdigest()) for name, value in data.items()
    ))
    seen = []

    def fetch(request, *, timeout):
        seen.append(request.full_url)
        return io.BytesIO(data[request.full_url.rsplit("/", 1)[-1]])

    installer.install(tmp_path, fetch=fetch)
    assert len(seen) == 2
    installer.install(tmp_path, fetch=lambda *_a, **_k: pytest.fail("unnecessary download"))
    assert len(list((tmp_path / installer.MODEL_DIRECTORY).glob("*.gguf"))) == 2


def test_existing_invalid_file_is_preserved(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ARTIFACTS", (("model.gguf", hashlib.sha256(b"valid").hexdigest()),))
    target = tmp_path / installer.MODEL_DIRECTORY / "model.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken")
    with pytest.raises(RuntimeError, match="preserved"):
        installer.install(tmp_path)
    assert target.read_bytes() == b"broken"


def test_hash_mismatch_rejects_download(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ARTIFACTS", (("model.gguf", "0" * 64),))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        installer.install(tmp_path, fetch=lambda *_a, **_k: io.BytesIO(b"bad"))
    assert list((tmp_path / installer.MODEL_DIRECTORY).iterdir()) == []


def test_existing_partial_is_not_overwritten(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ARTIFACTS", (("model.gguf", "0" * 64),))
    partial = tmp_path / installer.MODEL_DIRECTORY / "model.gguf.incomplete"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    with pytest.raises(RuntimeError, match="incomplete"):
        installer.install(tmp_path)
    assert partial.read_bytes() == b"partial"


@pytest.mark.parametrize(("ram_gib", "model_name"), [
    (16, "Ornith-1.5-9B-Q4_K_M.gguf"),
    (32, "gemma-4-12b-it-qat-q4_0.gguf"),
])
def test_setup_installs_only_selected_ram_tier(monkeypatch, tmp_path: Path, ram_gib: int, model_name: str):
    selected = {
        "LLAMACPP_MAIN_GGUF_PATH": str(installer.MODEL_DIRECTORY / installer.ARTIFACTS[0][0]),
        "LLAMACPP_LOW_RAM_GGUF_PATH": str(installer.ORNITH_DIRECTORY / installer.ORNITH_ARTIFACTS[0][0]),
        "LLAMACPP_HIGH_RAM_GGUF_PATH": str(installer.MODEL_DIRECTORY / installer.ARTIFACTS[0][0]),
        "LLAMACPP_HIGH_RAM_MIN_MIB": 24576,
    }
    monkeypatch.setattr(installer, "read_config", lambda *_: selected)
    gemma = {name: b"GGUF" + name.encode() for name, _ in installer.ARTIFACTS}
    ornith = {name: b"GGUF" + name.encode() for name, _ in installer.ORNITH_ARTIFACTS}
    monkeypatch.setattr(installer, "ARTIFACTS", tuple(
        (name, hashlib.sha256(data).hexdigest()) for name, data in gemma.items()))
    monkeypatch.setattr(installer, "ORNITH_ARTIFACTS", tuple(
        (name, hashlib.sha256(data).hexdigest()) for name, data in ornith.items()))
    values = {**gemma, **ornith}

    def fetch(request, *, timeout):
        return io.BytesIO(values[request.full_url.rsplit("/", 1)[-1]])

    installer.install_selected(tmp_path, fetch=fetch, total_ram_bytes=ram_gib * 1024**3)
    assert len(list((tmp_path / "portable/portable-models/gguf").rglob("*.gguf"))) == 2
    assert (tmp_path / "portable/portable-models/gguf").joinpath(
        installer.ORNITH_DIRECTORY.name if ram_gib == 16 else installer.MODEL_DIRECTORY.name,
        model_name,
    ).is_file()
