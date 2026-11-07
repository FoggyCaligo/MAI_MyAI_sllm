"""Model identity is fixed by the single-model portable configuration."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mai.app.server import ChatRequest, app


def test_chat_request_cannot_override_model():
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", model="some-other-model")


def test_public_model_catalog_removed():
    assert not any(getattr(route, "path", None) == "/models" for route in app.routes)


def test_chat_without_model_selector_still_valid():
    request = ChatRequest(message="hello")
    assert request.message == "hello"
    assert "model" not in request.model_fields
