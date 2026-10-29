"""Regression tests for the read-only codebase audit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_codebase import audit


def _sample(tmp_path: Path) -> Path:
    pkg = tmp_path / "mai"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "one.py").write_text(
        "from . import two\n\ndef same():\n    return [1,2,3,4,5,6,7,8]\n", encoding="utf-8"
    )
    (pkg / "two.py").write_text(
        "from . import one\n\ndef same():\n    return [1,2,3,4,5,6,7,8]\n", encoding="utf-8"
    )
    (pkg / "empty.py").write_text('"""A placeholder."""\n', encoding="utf-8")
    return tmp_path


def test_audit_reports_candidates_without_mutation(tmp_path: Path) -> None:
    root = _sample(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*.py")}
    result = audit(root)
    assert result["summary"]["modules"] == 4
    kinds = {finding["kind"] for finding in result["findings"]}
    assert "docstring_only" in kinds
    assert "import_cycle" in kinds
    assert before == {p: p.read_bytes() for p in root.rglob("*.py")}


def test_audit_parse_failure_is_not_suppressed(tmp_path: Path) -> None:
    root = _sample(tmp_path)
    (root / "mai" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    import pytest
    with pytest.raises(SyntaxError):
        audit(root)


def test_cli_json_output(tmp_path: Path) -> None:
    root = _sample(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_codebase.py"
    process = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--json"],
        capture_output=True, text=True, check=True,
    )
    result = json.loads(process.stdout)
    assert result["schema_version"] == 1
