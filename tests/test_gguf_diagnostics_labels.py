"""Ensure human-readable GGUF labels are used in answer diagnostics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "mai/app/static/index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "mai/app/server.py").read_text(encoding="utf-8")


def test_answer_diagnostics_use_configured_model_labels():
    assert "diag.dataset.modelId=String(model||'unknown')" in UI
    assert "diag.textContent=`${modelDisplayName(model)} · rounds: ${rounds}`" in UI
    assert "Object.assign(modelLabels,data.labels||{});" in UI
    assert "refreshModelDiagnostics();" in UI


def test_trial_account_receives_only_visible_model_label():
    assert '"labels": {name: labels[name] for name in visible if name in labels}' in SERVER
