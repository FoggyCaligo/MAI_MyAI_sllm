import httpx
import pytest

from mai.llm.llamacpp import LlamaCppAdapter, _openai_message
from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.ollama import OllamaProtocolError


@pytest.mark.asyncio
async def test_llamacpp_tool_call_and_thinking_round_trip():
    captured = []
    def responder(request):
        import json
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "", "reasoning_content": "memory needed",
            "tool_calls": [{"type": "function", "function": {
                "name": "memory_search", "arguments": "{\"keyword\":\"취미\"}"
            }}],
        }}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test", think=True), client=client)
        result = await adapter.chat(ChatRequest(messages=[{"role":"user","content":"hello"}]))
    assert result.thinking == "memory needed"
    assert result.tool_calls[0].arguments == {"keyword": "취미"}
    assert captured[0]["reasoning_effort"] == "medium"


def test_outgoing_tool_calls_serialized_to_openai_format():
    message = _openai_message({"role":"assistant","content":"","tool_calls":[
        {"type":"function","function":{"name":"memory_search","arguments":{"node_id":42}}}
    ]})
    assert message["tool_calls"][0]["function"]["arguments"] == '{"node_id": 42}'


@pytest.mark.asyncio
async def test_malformed_response_fails_visibly():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": []})
    )) as client:
        adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
        with pytest.raises(OllamaProtocolError):
            await adapter.chat(ChatRequest(messages=[{"role":"user","content":"hi"}]))
