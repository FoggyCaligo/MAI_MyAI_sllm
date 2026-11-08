"""Stable-prefix regression: dynamic clock belongs with the user message, not system."""
import asyncio

from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


class CapturingAdapter:
    def __init__(self):
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return ModelTurn(content="done", thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": "done"})


def test_system_prefix_is_identical_on_independent_requests():
    adapter = CapturingAdapter()
    runtime = AgentRuntime(adapter, ToolRegistry())
    asyncio.run(runtime.run_user_message("첫 질문", prior_messages=[
        {"role": "system", "content": "STATIC SYSTEM"},
    ]))
    asyncio.run(runtime.run_user_message("두 번째 질문", prior_messages=[
        {"role": "system", "content": "STATIC SYSTEM"},
    ]))
    left, right = (request.messages for request in adapter.requests)
    left_system = [m["content"] for m in left if m["role"] == "system"]
    right_system = [m["content"] for m in right if m["role"] == "system"]
    assert left_system == right_system
    assert left_system[0] == "STATIC SYSTEM"
    assert not any("authoritative_current_time" in x for x in left_system)
