"""Offline tests for optional cloud provider adapters; no paid calls."""
import json

import pytest

from mai.llm.cloud_api import CloudAPIAdapter, configured_models
from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.errors import ModelProtocolError


def test_model_allowlist_requires_key(monkeypatch):
    for provider in ("OPENAI", "ANTHROPIC", "OPENROUTER"):
        monkeypatch.delenv(provider + "_API_KEY", raising=False)
        monkeypatch.setenv("MAI_" + provider + "_MODELS", "sample-model")
    assert configured_models() == ()
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-test-key")
    assert configured_models() == ("openrouter:sample-model",)


def test_openrouter_tool_payload():
    adapter = CloudAPIAdapter("openrouter", ModelConfig(model="vendor/model"))
    request = ChatRequest(messages=({"role": "user", "content": "calculate"},),
                          tools=({"type": "function", "function": {
                              "name": "calculator", "parameters": {"type": "object", "properties": {}}}},))
    payload = adapter._openai_payload(request)
    assert payload["model"] == "vendor/model"
    assert payload["tools"][0]["function"]["name"] == "calculator"


def test_openai_tool_response_normalization():
    adapter = CloudAPIAdapter("openai", ModelConfig(model="example"))
    data = {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"type": "function", "id": "call_123", "function": {
            "name": "calculator", "arguments": json.dumps({"expression": "1+1"})}}]}}]}
    turn = adapter._openai_turn(data, ChatRequest(messages=()))
    assert turn.tool_calls[0].arguments["expression"] == "1+1"
    assert turn.assistant_message["tool_calls"][0]["function"]["name"] == "calculator"


def test_anthropic_tool_use_roundtrip():
    adapter = CloudAPIAdapter("anthropic", ModelConfig(model="test"))
    request = ChatRequest(messages=(
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "compute"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "type": "function", "function": {"name": "calculator", "arguments": {"value": 1}}}]},
        {"role": "tool", "tool_name": "calculator", "content": "done"},
    ), tools=({"type": "function", "function": {
        "name": "calculator", "parameters": {"type": "object"}}},))
    payload = adapter._anthropic_payload(request)
    assert payload["system"] == "system instructions"
    assert payload["messages"][1]["content"][0]["type"] == "tool_use"
    assert payload["messages"][2]["content"][0]["type"] == "tool_result"
    turn = adapter._anthropic_turn({"content": [
        {"type": "tool_use", "id": "tool_1", "name": "calculator", "input": {"value": 2}}],
        "stop_reason": "tool_use"}, request)
    assert turn.tool_calls[0].arguments == {"value": 2}


def test_invalid_cloud_args_are_rejected():
    adapter = CloudAPIAdapter("openai", ModelConfig(model="test"))
    with pytest.raises(ModelProtocolError):
        adapter._openai_turn({"choices": [{"message": {
            "content": "", "tool_calls": [{"type": "function", "function": {
                "name": "x", "arguments": "{invalid"}}]}}]}, ChatRequest(messages=()))
