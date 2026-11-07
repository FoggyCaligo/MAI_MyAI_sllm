import asyncio

from pydantic import BaseModel, ConfigDict

from mai.agent.codeact import CodeActExecutor, CodeActInput
from mai.tools.registry import ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def run(coro):
    return asyncio.run(coro)


def test_codeact_remote_unsupported_tool_returns_failed_observation():
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text",
        input_model=EchoInput,
        handler=lambda text: {"text": text},
    )
    executor = CodeActExecutor(registry, activated_tool_names=("echo",))

    result = run(executor.execute(CodeActInput(
        code=(
            "first = await tool('echo', {'text': 'remote'}, is_remote=True)\n"
            "print(first['ok'])"
        )
    )))

    assert result.stdout.strip() == "False"
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.name == "echo"
    assert call.target == "remote"
    assert call.ok is False
    assert call.error_type == "CodeActTargetError"
    assert call.handler_started is False


def test_codeact_handler_failure_is_returned_and_code_continues():
    registry = ToolRegistry()

    def fail(text: str):
        raise PermissionError(f"denied: {text}")

    registry.add(
        name="fail",
        description="Fail deliberately",
        input_model=EchoInput,
        handler=fail,
    )
    registry.add(
        name="echo",
        description="Echo text",
        input_model=EchoInput,
        handler=lambda text: {"text": text},
    )
    executor = CodeActExecutor(
        registry,
        activated_tool_names=("fail", "echo"),
    )

    result = run(executor.execute(CodeActInput(
        code=(
            "failed = await tool('fail', {'text': 'x'})\n"
            "after = await tool('echo', {'text': 'still-running'})\n"
            "print(failed['ok'], after['ok'])"
        )
    )))

    assert result.stdout.strip() == "False True"
    assert [call.name for call in result.calls] == ["fail", "echo"]
    assert result.calls[0].ok is False
    assert result.calls[0].error_type == "PermissionError"
    assert result.calls[0].handler_started is True
    assert result.calls[1].ok is True


def test_codeact_unactivated_tool_call_is_observed_failure():
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text",
        input_model=EchoInput,
        handler=lambda text: {"text": text},
    )
    executor = CodeActExecutor(registry, activated_tool_names=())

    result = run(executor.execute(CodeActInput(
        code="result = await tool('echo', {'text': 'x'})\nprint(result['ok'])"
    )))

    assert result.stdout.strip() == "False"
    assert result.calls[0].ok is False
    assert result.calls[0].error_type == "CodeActSyntaxError"
    assert result.calls[0].handler_started is False
