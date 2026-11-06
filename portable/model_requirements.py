"""포터블 매니페스트와 실제 MAI 설정에서 다운로드할 Ollama 모델을 구한다."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from collections.abc import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from mai.app.settings import load_settings


def required_models(
    manifest: Path, settings: Path, *, environ: Mapping[str, str] | None = None,
) -> list[str]:
    config = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Unsupported portable provisioning manifest version")
    models = config.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Manifest must list exact Ollama model tags")

    effective = dict(os.environ if environ is None else environ)
    load_settings(settings, environ=effective)
    configured = [effective[key] for key in ("MAIN_MODEL", "VISION_MODEL", "VERIFIER_MODEL", "VERIFIER_DECISION_MODEL") if key in effective]
    result: list[str] = []
    for model in [*models, *configured]:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Model tags must be non-empty strings")
        if model not in result:
            result.append(model)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--settings", type=Path, default=Path(__file__).resolve().parents[1] / "settings.yaml")
    args = parser.parse_args()
    # 서버와 동일하게 프로세스 환경 > .env > settings.yaml 순서로 적용한다.
    load_dotenv(args.settings.with_name(".env"))
    print(json.dumps(required_models(args.manifest, args.settings)))


if __name__ == "__main__":
    main()
