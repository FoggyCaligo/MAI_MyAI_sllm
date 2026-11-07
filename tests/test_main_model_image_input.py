"""Native vision input and separate image-tool fallback remain independent."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.main_model_images import prepare_main_image_input
from mai.app.uploads import principal_upload_directory
from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class ShowClient:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.calls = []

    async def show(self, model):
        self.calls.append(model)
        return {"capabilities": self.capabilities}


def image_fixture(tmp_path):
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    directory = principal_upload_directory(tmp_path, principal)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test.png").write_bytes(b"test-raw-image")
    return principal


def prepare(tmp_path, principal, client, **overrides):
    kwargs = dict(
        prompt="What is pictured?", attachments=("test.png",), principal=principal,
        upload_root=tmp_path, model="vision:9b", backend="ollama",
        ollama_client=client, separate_vision_model="fallback:2b",
    )
    kwargs.update(overrides)
    return run(prepare_main_image_input(**kwargs))


def test_vision_capability_sends_original_bytes(tmp_path):
    principal = image_fixture(tmp_path)
    client = ShowClient(["completion", "vision"])
    prompt, images = prepare(tmp_path, principal, client)
    assert prompt == "What is pictured?"
    assert images == (b"test-raw-image",)
    assert client.calls == ["vision:9b"]


def test_text_only_model_keeps_separate_image_tool_route(tmp_path):
    principal = image_fixture(tmp_path)
    prompt, images = prepare(tmp_path, principal, ShowClient(["completion"]))
    assert images == ()
    assert "image_analyze" in prompt
    assert "test.png" in prompt


def test_llamacpp_uses_existing_image_tool_without_unsupported_payload(tmp_path):
    principal = image_fixture(tmp_path)
    client = ShowClient(["vision"])
    prompt, images = prepare(tmp_path, principal, client, backend="llamacpp")
    assert images == ()
    assert "image_analyze" in prompt
    assert client.calls == []


def test_without_any_vision_backend_raises_instead_of_discarding_image(tmp_path):
    principal = image_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="no separate VISION_MODEL"):
        prepare(tmp_path, principal, ShowClient(["completion"]), separate_vision_model=None)


def test_attachment_path_cannot_escape_account_directory(tmp_path):
    principal = image_fixture(tmp_path)
    with pytest.raises(ValueError, match="filename"):
        prepare(tmp_path, principal, ShowClient(["vision"]), attachments=("../test.png",))


def test_without_attachments_does_not_query_model_capabilities(tmp_path):
    principal = image_fixture(tmp_path)
    client = ShowClient(["vision"])
    prompt, images = prepare(tmp_path, principal, client, attachments=())
    assert (prompt, images) == ("What is pictured?", ())
    assert client.calls == []


def test_agent_runtime_preserves_user_images_during_model_request():
    class Adapter:
        def __init__(self):
            self.requests = []

        async def chat(self, request):
            self.requests.append(request)
            answer = "The image contains a test subject."
            return ModelTurn(answer, "", (), {"role": "assistant", "content": answer})

    adapter = Adapter()
    agent = AgentRuntime(adapter, ToolRegistry())
    result = run(agent.run_user_message("Describe this image.", images=(b"raw-pixels",)))
    assert result.content == "The image contains a test subject."
    user_messages = [m for m in adapter.requests[0].messages if m.get("role") == "user"]
    assert user_messages[-1]["images"] == [b"raw-pixels"]
