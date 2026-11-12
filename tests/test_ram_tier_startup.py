"""RAM-based initial model selection must not affect explicit runtime switching."""
from __future__ import annotations

from pathlib import Path
import pytest

from portable.llamacpp_model_catalog import discover, selected_default_path


def model(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + bytes(16))
    return path


@pytest.mark.parametrize(
    ("ram_gib", "expected"),
    [(8, "ornith"), (16, "ornith"), (23, "ornith"), (24, "gemma"), (32, "gemma"), (64, "gemma")],
)
def test_startup_selection_tier(ram_gib: int, expected: str):
    config = {
        "LLAMACPP_LOW_RAM_GGUF_PATH": "models/ornith.gguf",
        "LLAMACPP_HIGH_RAM_GGUF_PATH": "models/gemma.gguf",
        "LLAMACPP_HIGH_RAM_MIN_MIB": 24576,
    }
    assert selected_default_path(config, ram_gib * 1024**3) == f"models/{expected}.gguf"


def test_catalog_uses_installed_capacity_and_keeps_both_models_selectable(tmp_path: Path):
    base = tmp_path / "gguf"
    ornith = model(base / "ornith" / "Ornith.gguf")
    gemma = model(base / "gemma" / "Gemma.gguf")
    config = {
        "LLAMACPP_GGUF_DIRECTORY": str(base),
        "LLAMACPP_MAIN_GGUF_PATH": str(gemma),
        "LLAMACPP_LOW_RAM_GGUF_PATH": str(ornith),
        "LLAMACPP_HIGH_RAM_GGUF_PATH": str(gemma),
        "LLAMACPP_HIGH_RAM_MIN_MIB": 24576,
    }
    low = discover(tmp_path, config, total_ram_bytes=16 * 1024**3)
    high = discover(tmp_path, config, total_ram_bytes=32 * 1024**3)
    low_default = next(item for item in low["models"] if item["id"] == low["default"])
    high_default = next(item for item in high["models"] if item["id"] == high["default"])
    assert low_default["path"] == str(ornith)
    assert high_default["path"] == str(gemma)
    assert {entry["path"] for entry in low["models"]} == {str(ornith), str(gemma)}
    assert {entry["path"] for entry in high["models"]} == {str(ornith), str(gemma)}


def test_missing_selected_model_fails_instead_of_falling_back(tmp_path: Path):
    base = tmp_path / "gguf"
    gemma = model(base / "gemma" / "Gemma.gguf")
    config = {
        "LLAMACPP_GGUF_DIRECTORY": str(base),
        "LLAMACPP_MAIN_GGUF_PATH": str(gemma),
        "LLAMACPP_LOW_RAM_GGUF_PATH": str(base / "ornith" / "Ornith.gguf"),
        "LLAMACPP_HIGH_RAM_GGUF_PATH": str(gemma),
        "LLAMACPP_HIGH_RAM_MIN_MIB": 24576,
    }
    with pytest.raises(FileNotFoundError):
        discover(tmp_path, config, total_ram_bytes=16 * 1024**3)


def test_partial_tier_config_fails_explicitly():
    with pytest.raises(ValueError, match="Both RAM tier"):
        selected_default_path(
            {"LLAMACPP_LOW_RAM_GGUF_PATH": "ornith.gguf", "LLAMACPP_HIGH_RAM_MIN_MIB": 24576},
            16 * 1024**3,
        )
