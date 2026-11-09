"""Fetch the public cleaned LongMemEval-S dataset without redistributing it."""
from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download

DATASET_REPO = "xiaowu0162/longmemeval-cleaned"
FILE = "longmemeval_s_cleaned.json"


def main():
    parser = argparse.ArgumentParser(description="Download LongMemEval-S original conversation data")
    parser.add_argument("--local-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    args.local_dir.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        repo_id=DATASET_REPO,
        filename=FILE,
        repo_type="dataset",
        local_dir=str(args.local_dir),
    )
    print(Path(path).resolve())


if __name__ == "__main__":
    main()
