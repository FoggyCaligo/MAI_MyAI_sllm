"""GAIA 데이터 다운로드: 이용약관 동의 후 개인 로컬 폴더에만 저장한다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DATASET_ID = "gaia-benchmark/GAIA"
DATA_DIR = Path(__file__).resolve().parent / "data" / "gaia"


def download(*, revision: str | None = None) -> dict[str, object]:
    from huggingface_hub import snapshot_download
    from datasets import load_dataset

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=DATASET_ID,
        repo_type="dataset",
        revision=revision,
        local_dir=DATA_DIR,
    )
    meta: dict[str, object] = {
        "dataset": DATASET_ID,
        "revision_requested": revision,
        "data_directory": str(DATA_DIR),
        "splits": {},
    }
    for split in ("validation", "test"):
        examples = load_dataset(str(DATA_DIR), "2023_all", split=split)
        meta["splits"][split] = len(examples)
        for record in examples:
            rel = record.get("file_path")
            if not rel:
                continue
            path = (DATA_DIR / str(rel)).resolve()
            if not path.is_relative_to(DATA_DIR.resolve()):
                raise ValueError(f"GAIA attachment escapes data directory: {rel!r}")
            if not path.is_file():
                raise FileNotFoundError(f"GAIA attachment is missing: {path}")
    # Metadata does not expose questions, answers or attachment contents.
    (DATA_DIR.parent / "download-info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="개인용 GAIA 데이터 다운로드")
    parser.add_argument("--revision", help="재현성을 위한 Hugging Face 커밋 해시")
    args = parser.parse_args()
    print(json.dumps(download(revision=args.revision), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
