"""Regression tests for CodeAct model-visible tool failures."""
import asyncio

from pydantic import BaseModel, ConfigDict

from mai.agent.codeact import CodeActExecutor, CodeActInput
from mai.tools.registry import ToolRegistry


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def execute(source: str, *, remote_supported: bool = False):
    registry = ToolRegistry()

    def fail(value: int):
        raise PermissionError("denied by the tool")

    registry.add(
        name="fail",
        description="A failing test tool",
        input_model=Input,
        handler=fail,
        metadata=(
            {"codeact_execution_target": "routed_pc"}
            if remote_supported else {}
        ),
    )
    executor = CodeActExecutor(registry, activated_tool_names=("fail",))
    return asyncio.run(executor.execute(CodeActInput(code=source)))


def test_handler_failure_is_returned_to_python_and_recorded():
    result = execute(
        "out = await tool('fail', {'value': 2})\n"
        "print(out['ok'], out['error_type'])"
    )
    assert "False PermissionError" in result.stdout
    assert len(result.calls) == 1
    assert result.calls[0].ok is False
    assert result.calls[0].handler_started is True
    assert result.calls[0].error_type == "PermissionError"


def test_invalid_arguments_are_failed_tool_result_without_handler_start():
    result = execute(
        "out = await tool('fail', {'value': 'invalid'})\n"
        "print(out['ok'], out['error_type'])"
    )
    assert "False ToolArgumentsError" in result.stdout
    assert result.calls[0].handler_started is False


def test_unactivated_call_returns_failure_and_code_can_continue():
    result = execute(
        "bad = await tool('not_activated', {})\n"
        "print(bad['ok'], bad['error_type'])\n"
        "other = await tool('fail', {'value': 3})\n"
        "print(other['error_type'])"
    )
    assert "False CodeActSyntaxError" in result.stdout
    assert "PermissionError" in result.stdout
    assert len(result.calls) == 2


def test_remote_not_supported_is_failed_tool_result():
    result = execute(
        "out = await tool('fail', {'value': 2}, is_remote=True)\n"
        "print(out['ok'], out['error_type'])"
    )
    assert "False CodeActTargetError" in result.stdout
    assert result.calls[0].target == "remote"
    assert result.calls[0].handler_started is False


def test_remote_worker_failure_stays_failed():
    result = execute(
        "out = await tool('fail', {'value': 2}, is_remote=True)\n"
        "print(out['ok'], out['error_type'])",
        remote_supported=True,
    )
    assert "False PermissionError" in result.stdout
    assert result.calls[0].target == "remote"
    assert result.calls[0].handler_started is True
