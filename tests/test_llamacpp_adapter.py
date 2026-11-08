import asyncio
import httpx
import pytest

from mai.llm.llamacpp import LlamaCppAdapter, _openai_message, _openai_messages
from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.ollama import OllamaProtocolError


def test_llamacpp_tool_call_and_thinking_round_trip():
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
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test", think=True), client=client)
            return await adapter.chat(ChatRequest(messages=[{"role":"user","content":"hello"}]))
    result = asyncio.run(run())
    assert result.thinking == "memory needed"
    assert result.tool_calls[0].arguments == {"keyword": "취미"}
    assert "reasoning_effort" not in captured[0]


def test_outgoing_tool_calls_serialized_to_openai_format():
    message = _openai_message({"role":"assistant","content":"","tool_calls":[
        {"type":"function","function":{"name":"memory_search","arguments":{"node_id":42}}}
    ]})
    assert message["tool_calls"][0]["function"]["arguments"] == '{"node_id": 42}'


def test_malformed_response_fails_visibly():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": []})
        )) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(messages=[{"role":"user","content":"hi"}]))
    with pytest.raises(OllamaProtocolError):
        asyncio.run(run())


def test_tool_result_history_preserves_call_identity():
    turns = _openai_messages([
        {"role": "user", "content": "기억 조회"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"type": "function", "function": {"name": "memory_search", "arguments": {"keyword": "취미"}}}
        ]},
        {"role": "tool", "tool_name": "memory_search", "content": "[]"},
    ])
    identifier = turns[1]["tool_calls"][0]["id"]
    assert turns[2]["tool_call_id"] == identifier
    assert turns[2]["role"] == "tool"
    assert "tool_name" not in turns[2]


def test_empty_structured_verifier_response_is_protocol_error_with_diagnostics():
    import json
    async def run():
        def reply(request):
            body = json.loads(request.content)
            assert body["model"] == "mai-verifier"
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "length",
                             "message": {"role": "assistant", "content": "",
                                         "reasoning_content": "not finished"}}],
                "usage": {"prompt_tokens": 7800, "completion_tokens": 391},
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="mai-verifier", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(
                messages=[{"role": "user", "content": "review"}],
                think=False,
                response_format={"type": "object", "properties": {"ok": {"type": "boolean"}},
                                 "required": ["ok"]},
            ))
    with pytest.raises(OllamaProtocolError, match="finish_reason='length'"):
        asyncio.run(run())


def test_llamacpp_omits_reasoning_effort_for_all_think_requests():
    captured = []

    def responder(request):
        import json
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "ok", "reasoning_content": ""}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            for think in (True, False, None):
                adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test", think=True), client=client)
                result = await adapter.chat(ChatRequest(
                    messages=[{"role": "user", "content": "test"}], think=think,
                ))
                assert result.content == "ok"

    asyncio.run(run())
    assert len(captured) == 3
    assert all("reasoning_effort" not in request for request in captured)
