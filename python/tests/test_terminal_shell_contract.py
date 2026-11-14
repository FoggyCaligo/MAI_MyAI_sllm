"""PowerShell shell contract diagnostics require no additional model call."""
from __future__ import annotations

import asyncio

import pytest

from mai.tools.terminal import (
    TerminalShellContractError, _validate_shell_contract, terminal_run,
)


@pytest.mark.parametrize("command", [
    "powershell.exe -Command \"Get-ScheduledTask\"",
    "powershell -NoProfile -Command Get-ScheduledTask",
    "pwsh -Command Get-Process",
    " & powershell.exe -EncodedCommand AAAA",
])
def test_nested_powershell_wrapper_is_rejected(command):
    with pytest.raises(TerminalShellContractError, match="shell=powershell"):
        _validate_shell_contract(command, "powershell")


def test_standalone_powershell_and_other_shells_are_unaffected():
    _validate_shell_contract("Get-ScheduledTask | Select-Object TaskName", "powershell")
    _validate_shell_contract("Write-Output '$_.Path'", "powershell")
    _validate_shell_contract("Get-Process; powershell.exe -Command 'echo subprocess'", "powershell")
    _validate_shell_contract("powershell.exe -Command \"Get-Process\"", "bash")


def test_nested_shell_rejection_happens_before_subprocess(monkeypatch, tmp_path):
    import mai.tools.terminal as terminal
    invoked = False

    async def fake_run(*args, **kwargs):
        nonlocal invoked
        invoked = True
        return {}

    monkeypatch.setattr(terminal, "_run_command", fake_run)
    with pytest.raises(TerminalShellContractError):
        asyncio.run(terminal_run(
            command="powershell.exe -Command \"Get-Process\"",
            shell="powershell", cwd=str(tmp_path),
        ))
    assert not invoked
