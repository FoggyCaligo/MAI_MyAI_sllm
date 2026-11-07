"""llama.cpp multimodal payload and restored image-analysis contract."""
from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.main_model_images import prepare_main_image_input
from mai.app.uploads import principal_upload_directory
from mai.llm.llamacpp import _openai_message
from mai.llm.errors import ModelProtocolError
from mai.tools.images import ImageAnalyzer, register_image_tools
from mai.tools.registry import ToolRegistry
from mai.llm.models import NativeToolCall, ModelTurn


PNG = bytes.fromhex("89504e470d0a1a0a") + b"sample"


def test_llamacpp_binary_image_uses_openai_image_url_data_uri():
    result = _openai_message({"role": "user", "content": "inspect", "images": [PNG]})
    assert "images" not in result
    assert result["content"][0] == {"type": "text", "text": "inspect"}
    assert result["content"][1]["type"] == "image_url"
    assert result["content"][1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    )


def test_image_payload_does_not_accept_unverified_bytes():
    with pytest.raises(ModelProtocolError, match="unsupported image"):
        _openai_message({"role": "user", "content": "", "images": [b"not-an-image"]})


def test_main_attachment_is_confined_to_account_uploads(tmp_path):
    principal = AccessPrincipal(user_id="person", db_id="person", role=AccessRole.OWNER)
    folder = principal_upload_directory(tmp_path, principal)
    folder.mkdir(parents=True)
    (folder / "test.png").write_bytes(PNG)
    text, images = prepare_main_image_input(
        prompt="What is this?", attachments=("test.png",),
        principal=principal, upload_root=tmp_path,
    )
    assert text == "What is this?"
    assert images == (PNG,)
    with pytest.raises(ValueError, match="filename"):
        prepare_main_image_input(
            prompt="x", attachments=("../test.png",),
            principal=principal, upload_root=tmp_path,
        )


def test_image_analyzer_registers_and_calls_same_adapter(tmp_path):
    image = tmp_path / "test.png"
    image.write_bytes(PNG)

    class Adapter:
        config = SimpleNamespace(model="mai-model")
        requests = []

        async def chat(self, request):
            self.requests.append(request)
            return ModelTurn(
                content="A photographed object.", thinking="", tool_calls=(),
                assistant_message={"role": "assistant", "content": "A photographed object."},
            )

    adapter = Adapter()
    registry = ToolRegistry()
    register_image_tools(registry, adapter=adapter, cwd=tmp_path)
    assert registry.names() == ("image_analyze",)
    result = asyncio.run(registry.invoke(NativeToolCall(
        "image_analyze", {"path": "test.png", "question": "Describe it."},
    )))
    assert result["model"] == "mai-model"
    assert result["analysis"] == "A photographed object."
    assert adapter.requests[0].messages[0]["images"] == [PNG]


def test_trial_image_tool_cannot_escape_its_root(tmp_path):
    directory = tmp_path / "uploads"
    directory.mkdir()
    outside = tmp_path / "other.png"
    outside.write_bytes(PNG)
    adapter = SimpleNamespace(chat=lambda request: None)
    analyzer = ImageAnalyzer(adapter=adapter, cwd=directory, allowed_root=directory)
    with pytest.raises(PermissionError, match="outside"):
        asyncio.run(analyzer.analyze(path=str(outside), question="Inspect"))
