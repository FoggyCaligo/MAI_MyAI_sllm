"""Contract regression checks for the Windows PowerShell 5.1 remote agent."""

from pathlib import Path


AGENT_SCRIPT = Path(__file__).resolve().parents[1] / "mai" / "app" / "static" / "remote-agent.ps1"


def test_remote_worker_uses_utf8_bytes_for_standard_input():
    script = AGENT_SCRIPT.read_text(encoding="utf-8")
    assert "$psi.RedirectStandardInput = $true" in script
    assert "[Text.Encoding]::UTF8.GetBytes($payload" in script
    assert "$process.StandardInput.BaseStream" in script
    assert "$stdin.Write($payloadBytes, 0, $payloadBytes.Length)" in script
    assert "$stdin.Close()" in script


def test_remote_worker_does_not_use_unsupported_framework_property():
    script = AGENT_SCRIPT.read_text(encoding="utf-8")
    assert "$psi.StandardInputEncoding =" not in script
