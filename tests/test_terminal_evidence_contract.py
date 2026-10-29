from __future__ import annotations

import asyncio
import sys

import pytest

from mai.tools.terminal import (
    TerminalRunError,
    TerminalOutputLimitError,
    terminal_run,
    terminal_run_steps,
)


def run(coro):
    return asyncio.run(coro)


def _python(statement: str) -> str:
    # A single independent command is enough for these POSIX/Windows tests.
    return f'"{sys.executable}" -c "{statement}"'


def test_multiple_steps_stop_on_real_nonzero_exit(tmp_path):
    first = _python("print(1)")
    second = _python("import sys; sys.exit(7)")
    third = _python("print(3)")
    with pytest.raises(TerminalRunError) as exc:
        run(terminal_run_steps(
            commands=[first, second, third],
            default_cwd=tmp_path,
        ))
    assert exc.value.result["step_index"] == 1
    assert len(exc.value.result["completed_steps"]) == 1
    assert exc.value.result["failed_step"]["returncode"] == 7


def test_all_steps_preserve_separate_evidence(tmp_path):
    response = run(terminal_run_steps(
        commands=[_python("print(1)"), _python("print(2)")],
        default_cwd=tmp_path,
    ))
    assert response["completed_steps"] == 2
    assert [step["returncode"] for step in response["steps"]] == [0, 0]


def test_output_limit_fails_explicitly(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_MAX_OUTPUT_BYTES", "128")
    with pytest.raises(TerminalOutputLimitError) as exc:
        run(terminal_run(command=_python("print('x'*5000)"), default_cwd=tmp_path))
    assert exc.value.result["output_limit_exceeded"] is True
    assert exc.value.result["max_output_bytes"] == 128
