from __future__ import annotations

import os
from pathlib import Path

import run_server


def test_configure_runtime_cwd_defaults_to_user_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MAI_CWD", raising=False)
    monkeypatch.setattr(run_server.Path, "home", lambda: tmp_path)

    resolved = run_server.configure_runtime_cwd()

    assert resolved == str(tmp_path.resolve())
    assert os.environ["MAI_CWD"] == str(tmp_path.resolve())


def test_configure_runtime_cwd_preserves_explicit_value(monkeypatch, tmp_path: Path) -> None:
    configured = str(tmp_path / "custom-root")
    monkeypatch.setenv("MAI_CWD", configured)

    resolved = run_server.configure_runtime_cwd()

    assert resolved == configured
    assert os.environ["MAI_CWD"] == configured
