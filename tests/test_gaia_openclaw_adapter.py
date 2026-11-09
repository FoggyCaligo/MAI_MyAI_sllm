"""OpenClaw GAIA adapter offline contract tests; never contact the real model."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "benchmark" / "GAIA Benchmark" / "adapter"
sys.path.insert(0, str(ADAPTER))
spec = importlib.util.spec_from_file_location("run_openclaw_gaia", ADAPTER / "run_openclaw_gaia.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_openclaw_config_pins_only_local_router_and_model():
    config = mod.openclaw_config("gguf-abc")
    assert config["models"]["mode"] == "replace"
    providers = config["models"]["providers"]
    assert set(providers) == {"mai-local"}
    assert providers["mai-local"]["baseUrl"] == "http://127.0.0.1:8080/v1"
    assert providers["mai-local"]["api"] == "openai-completions"
    assert config["agents"]["defaults"]["model"]["primary"] == "mai-local/gguf-abc"
    assert config["agents"]["defaults"]["contextInjection"] == "never"
    with pytest.raises(ValueError):
        mod.openclaw_config("openai/remote")


def test_openclaw_envelope_validates_structural_contract():
    payload = {"ok": True, "status": "ok", "final": "FINAL ANSWER: 17",
               "assistantTurns": 6, "toolSummary": {"calls": 8}}
    assert mod.parse_envelope(json.dumps(payload))["final"] == "FINAL ANSWER: 17"
    with pytest.raises(RuntimeError, match="failed"):
        mod.parse_envelope(json.dumps({"ok": False, "status": "error", "error": {"kind": "provider"}}))
    with pytest.raises(ValueError, match="final string"):
        mod.parse_envelope(json.dumps({"ok": True, "status": "ok", "payloads": []}))


def test_openclaw_exec_uses_config_and_task_workspace(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({
                "ok": True, "status": "ok", "final": "FINAL ANSWER: 17",
                "assistantTurns": 2, "toolSummary": {"calls": 1},
            }), stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    answer, envelope = mod.run_one(
        executable="openclaw", prompt="test problem", workspace=work,
        config=config, model_id="gguf-abc", timeout_seconds=1800,
    )
    assert answer == "17"
    assert envelope["toolSummary"]["calls"] == 1
    args = seen["cmd"]
    assert args[:3] == ["openclaw", "agent", "exec"]
    assert args[args.index("--cwd") + 1] == str(work)
    assert args[args.index("--config") + 1] == str(config)
    assert args[args.index("--model") + 1] == "mai-local/gguf-abc"
    assert args[args.index("--code-mode") + 1] == "direct"
    assert args[args.index("--timeout") + 1] == "1800"
    assert "--json" in args and "--message-file" in args
    assert "test problem" not in args
    assert (work / "gaia_prompt.txt").read_text(encoding="utf-8") == "test problem"
    assert (work / "openclaw_stdout.json").is_file()


def test_openclaw_cli_failure_cannot_be_scored_as_completion(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout='{"ok": false}', stderr="offline error")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="exited 1"):
        mod.run_one(
            executable="openclaw", prompt="test problem", workspace=work,
            config=config, model_id="gguf-abc", timeout_seconds=1800,
        )
    assert (work / "openclaw_stderr.log").read_text(encoding="utf-8") == "offline error"


def test_router_only_starts_no_mai_app_and_conflicts_with_gaia():
    launcher = (ROOT / "portable" / "start-portable-llamacpp-router.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$RouterOnly" in launcher
    assert "if ($GaiaBenchmark -and $RouterOnly)" in launcher
    assert "if ($RouterOnly)" in launcher
    assert "Wait-Process -Id $process.Id" in launcher
    assert "'--models-max', \"$ModelsMax\"" in launcher


def test_windows_openclaw_npm_cmd_is_resolved_before_subprocess(monkeypatch):
    seen = []
    monkeypatch.setattr(mod.os, "name", "nt")
    def which(name):
        seen.append(name)
        return r"C:\\Users\\tester\\AppData\\Roaming\\npm\\openclaw.cmd" if name == "openclaw.cmd" else None
    monkeypatch.setattr(mod.shutil, "which", which)
    assert mod.resolve_openclaw_executable("openclaw").endswith("openclaw.cmd")
    assert seen == ["openclaw.cmd"]


def test_missing_openclaw_reports_installation_problem(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="Get-Command openclaw"):
        mod.resolve_openclaw_executable("openclaw")
