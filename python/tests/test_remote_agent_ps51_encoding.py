"""Contract regression checks for the Windows PowerShell remote bootstrap."""

from pathlib import Path


AGENT_SCRIPT = Path(__file__).resolve().parents[1] / "mai" / "app" / "static" / "remote-agent.ps1"


def test_remote_agent_uses_persistent_python_client():
    script = AGENT_SCRIPT.read_text(encoding="utf-8")
    assert "Initialize-MaiRemotePython" in script
    assert "$env:MAI_REMOTE_SERVER = $Server" in script
    assert "$env:MAI_REMOTE_SESSION = $Session" in script
    assert "$env:MAI_REMOTE_KEY = $Key" in script
    assert "-m mai.remote.agent_client" in script
    assert "RedirectStandardInput" not in script
    assert "mai.remote.python_worker" not in script


def test_remote_agent_cleans_secret_environment():
    script = AGENT_SCRIPT.read_text(encoding="utf-8")
    assert "Remove-Item Env:MAI_REMOTE_KEY" in script
    assert "Remove-Item Env:MAI_REMOTE_SESSION" in script
    assert "Remove-Item Env:MAI_REMOTE_SERVER" in script


def test_remote_agent_connects_without_interactive_yes_prompt():
    script = AGENT_SCRIPT.read_text(encoding="utf-8")
    assert "Read-Host" not in script
