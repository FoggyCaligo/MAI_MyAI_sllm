"""Install verified official Google Gemma 4 12B QAT GGUF and projector."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portable.llamacpp_model_catalog import selected_default_path
from portable.resolve_llamacpp_settings import read_config
from portable.system_memory import installed_ram_bytes

MODEL_DIRECTORY = Path("portable/portable-models/gguf/gemma4_12b_Q4")
BASE_URL = "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main"
ARTIFACTS = (
    ("gemma-4-12b-it-qat-q4_0.gguf", "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b"),
    ("mmproj-gemma-4-12b-it-qat-q4_0.gguf", "cb018338a7538a9814d994bfe54644c71eb7ed54e31eae2f721e45fd3c260da7"),
)
ORNITH_DIRECTORY = Path("portable/portable-models/gguf/ornith_1.5_9b_Q4")
ORNITH_BASE_URL = "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main"
ORNITH_ARTIFACTS = (
    ("Ornith-1.5-9B-Q4_K_M.gguf", "70c112196e0b7023803c9762752e46d29e612a92c83f995bc3ba1ceb07e8fab6"),
    ("mmproj-Ornith-1.5-9B-BF16.gguf", "626f9f90627402a6bf4a999111d0fbd69b5fcca7aa8ba089d69e5f10e8858e1d"),
)
CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def install_artifacts(root: Path, *, directory: Path, base_url: str, artifacts, fetch=None) -> None:
    dest = root.resolve() / directory
    dest.mkdir(parents=True, exist_ok=True)
    fetch = fetch or urllib.request.urlopen
    for filename, expected in artifacts:
        target = dest / filename
        if target.exists():
            if not target.is_file() or sha256_file(target) != expected:
                raise RuntimeError(f"Existing model artifact failed SHA-256 (preserved): {target}")
            print(f"Verified existing model artifact: {target}", flush=True)
            continue
        partial = target.with_name(filename + ".incomplete")
        if partial.exists():
            raise RuntimeError(f"Preexisting incomplete model download: {partial}")
        digest = hashlib.sha256()
        request = urllib.request.Request(
            f"{base_url}/{filename}", headers={"User-Agent": "MAI-setup/1.0"}
        )
        try:
            with fetch(request, timeout=120) as response, partial.open("xb") as output:
                while True:
                    data = response.read(CHUNK_SIZE)
                    if not data:
                        break
                    digest.update(data)
                    output.write(data)
            if digest.hexdigest() != expected:
                raise RuntimeError(f"Model SHA-256 mismatch for {filename}: {digest.hexdigest()} != {expected}")
            if target.exists():
                raise FileExistsError(f"Model target appeared during download: {target}")
            os.rename(partial, target)
            print(f"Installed verified model artifact: {target}", flush=True)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise


def install(root: Path, *, fetch=None) -> None:
    """Retain the explicit Gemma installer for direct use."""
    install_artifacts(root, directory=MODEL_DIRECTORY, base_url=BASE_URL,
                      artifacts=ARTIFACTS, fetch=fetch)


def install_selected(root: Path, *, fetch=None, total_ram_bytes: int | None = None) -> None:
    """Install the startup RAM tier only; no silent substitute model."""
    root = root.resolve()
    config = read_config(root / "settings.yaml")
    ram = installed_ram_bytes() if total_ram_bytes is None else total_ram_bytes
    chosen = selected_default_path(config, ram)
    selected = Path(chosen)
    selected = (selected if selected.is_absolute() else root / selected).resolve()
    profiles = (
        (MODEL_DIRECTORY, BASE_URL, ARTIFACTS),
        (ORNITH_DIRECTORY, ORNITH_BASE_URL, ORNITH_ARTIFACTS),
    )
    matches = [
        (directory, base_url, artifacts)
        for directory, base_url, artifacts in profiles
        if selected == (root / directory / artifacts[0][0]).resolve()
    ]
    if len(matches) != 1:
        raise ValueError(f"Selected startup model has no declared verified installer: {selected}")
    directory, base_url, artifacts = matches[0]
    install_artifacts(root, directory=directory, base_url=base_url, artifacts=artifacts, fetch=fetch)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--selected", action="store_true")
    args = parser.parse_args()
    if args.selected:
        install_selected(args.root)
    else:
        install(args.root)


if __name__ == "__main__":
    main()
