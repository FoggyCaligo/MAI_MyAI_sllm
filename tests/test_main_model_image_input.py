"""llama.cpp-only attachment validation and direct image forwarding."""
from __future__ import annotations

import asyncio
import pytest

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.main_model_images import prepare_main_image_input
from mai.app.uploads import principal_upload_directory
from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


def image_fixture(tmp_path):
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    directory = principal_upload_directory(tmp_path, principal)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test.png").write_bytes(b"test-raw-image")
    return principal


def prepare(tmp_path, principal, **overrides):
    kwargs = dict(
        prompt="What is pictured?", attachments=("test.png",), principal=principal,
        upload_root=tmp_path, model="local-vision-gguf", supports_vision=False,
    )
    kwargs.update(overrides)
    return run(prepare_main_image_input(**kwargs))


def test_llamacpp_text_only_main_explains_unavailable_image_without_tool_or_exception(tmp_path, monkeypatch):
    principal = image_fixture(tmp_path)
    prompt, images = prepare(tmp_path, principal)
    assert images == ()
    assert "cannot inspect these images" in prompt
    assert "No image pixels were provided" in prompt
    assert "image_analyze is not available" in prompt
    assert "test.png" not in prompt


def test_llamacpp_image_capable_main_receives_pixels_directly(tmp_path, monkeypatch):
    principal = image_fixture(tmp_path)
    prompt, images = prepare(tmp_path, principal, supports_vision=True)
    assert prompt == "What is pictured?"
    assert images == (b"test-raw-image",)


def test_attachment_path_cannot_escape_account_directory(tmp_path):
    principal = image_fixture(tmp_path)
    with pytest.raises(ValueError, match="filename"):
        prepare(tmp_path, principal, attachments=("../test.png",))


def test_without_attachments_does_not_require_model_capability_probe(tmp_path):
    principal = image_fixture(tmp_path)
    prompt, images = prepare(tmp_path, principal, attachments=())
    assert (prompt, images) == ("What is pictured?", ())


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
