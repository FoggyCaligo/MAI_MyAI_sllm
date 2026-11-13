from __future__ import annotations
import hashlib
import io
from pathlib import Path
import pytest
from portable import install_default_model as installer


def test_ornith_q8_default_matches_settings_and_verified_manifest():
    from portable.resolve_llamacpp_settings import read_config

    settings = read_config(Path(__file__).resolve().parents[1] / "settings.yaml")
    default = Path(settings["LLAMACPP_MAIN_GGUF_PATH"])
    assert default.parent == installer.ORNITH_DIRECTORY
    assert default.name == installer.ORNITH_ARTIFACTS[0][0]
    assert installer.ORNITH_ARTIFACTS[0] == (
        "Ornith-1.5-9B-Q8_0.gguf",
        "22086870b009dbe9815ee752c48a82de930118a7c5ce5599590892ae03b8b010",
    )
    assert installer.ORNITH_ARTIFACTS[1][0] == "mmproj-Ornith-1.5-9B-BF16.gguf"


def test_download_then_reuse(monkeypatch, tmp_path: Path):
    data = {name: b"GGUF" + name.encode() for name, _ in installer.ORNITH_ARTIFACTS}
    monkeypatch.setattr(installer, "ORNITH_ARTIFACTS", tuple(
        (name, hashlib.sha256(value).hexdigest()) for name, value in data.items()
    ))
    seen = []

    def fetch(request, *, timeout):
        seen.append(request.full_url)
        return io.BytesIO(data[request.full_url.rsplit("/", 1)[-1]])

    installer.install(tmp_path, fetch=fetch)
    assert len(seen) == 2
    installer.install(tmp_path, fetch=lambda *_a, **_k: pytest.fail("unnecessary download"))
    assert len(list((tmp_path / installer.ORNITH_DIRECTORY).glob("*.gguf"))) == 2


def test_existing_invalid_file_is_preserved(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ORNITH_ARTIFACTS", (("model.gguf", hashlib.sha256(b"valid").hexdigest()),))
    target = tmp_path / installer.ORNITH_DIRECTORY / "model.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken")
    with pytest.raises(RuntimeError, match="preserved"):
        installer.install(tmp_path)
    assert target.read_bytes() == b"broken"


def test_hash_mismatch_rejects_download(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ORNITH_ARTIFACTS", (("model.gguf", "0" * 64),))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        installer.install(tmp_path, fetch=lambda *_a, **_k: io.BytesIO(b"bad"))
    assert list((tmp_path / installer.ORNITH_DIRECTORY).iterdir()) == []


def test_existing_partial_is_not_overwritten(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(installer, "ORNITH_ARTIFACTS", (("model.gguf", "0" * 64),))
    partial = tmp_path / installer.ORNITH_DIRECTORY / "model.gguf.incomplete"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    with pytest.raises(RuntimeError, match="incomplete"):
        installer.install(tmp_path)
    assert partial.read_bytes() == b"partial"


def test_optional_gemma_install_does_not_replace_ornith_default(monkeypatch, tmp_path: Path):
    data = {name: b"GGUF" + name.encode() for name, _ in installer.ARTIFACTS}
    monkeypatch.setattr(installer, "ARTIFACTS", tuple(
        (name, hashlib.sha256(value).hexdigest()) for name, value in data.items()
    ))
    installer.install_gemma(
        tmp_path,
        fetch=lambda req, *, timeout: io.BytesIO(data[req.full_url.rsplit("/", 1)[-1]]),
    )
    assert len(list((tmp_path / installer.MODEL_DIRECTORY).glob("*.gguf"))) == 2
    assert not (tmp_path / installer.ORNITH_DIRECTORY).exists()
