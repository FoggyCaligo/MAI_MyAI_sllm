from __future__ import annotations

import asyncio
import sys

import pytest

from mai.tools.terminal import TerminalOutputLimitError, terminal_run


def test_terminal_output_limit_is_an_explicit_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_MAX_OUTPUT_BYTES", "128")
    command = f'"{sys.executable}" -c "print(\'x\'*5000)"'
    with pytest.raises(TerminalOutputLimitError) as exc_info:
        asyncio.run(terminal_run(command=command, default_cwd=tmp_path))
    assert exc_info.value.result["output_limit_exceeded"] is True
    assert exc_info.value.result["max_output_bytes"] == 128
