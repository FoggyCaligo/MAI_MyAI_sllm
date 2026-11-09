"""회귀 방지: 터미널 도구 모듈의 문법과 Bash 셸 계약을 확인한다."""
from __future__ import annotations

import ast
from pathlib import Path

from mai.tools.registry import ToolRegistry
from mai.tools.terminal import TerminalRunInput, register_terminal_tools


def test_terminal_module_parses_as_complete_python_source():
    path = Path("mai/tools/terminal.py")
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_terminal_registry_is_available_and_default_remains_bash(tmp_path):
    assert TerminalRunInput(command="pwd").shell == "bash"
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    definition = registry.get("terminal_run")
    assert definition.metadata["model_context"]["shell_family"] == "bash"
    assert definition.metadata["model_context"]["posix_shell_syntax"] is True
    assert "shell=powershell" in definition.description
