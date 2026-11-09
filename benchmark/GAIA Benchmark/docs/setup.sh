#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ADAPTER="$ROOT/adapter"
PYTHON="$ADAPTER/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$ADAPTER/.venv"
fi
"$PYTHON" -m pip install --disable-pip-version-check --no-input --upgrade pip
"$PYTHON" -m pip install --disable-pip-version-check --no-input 'datasets>=4,<5' 'huggingface_hub>=0.34' 'httpx>=0.27' 'python-dotenv>=1,<2'
echo "GAIA 약관 동의: https://huggingface.co/datasets/gaia-benchmark/GAIA"
"$PYTHON" "$ADAPTER/download_gaia.py"
