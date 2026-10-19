"""Contract tests for opt-in browser tools (no installed Chromium required)."""
import asyncio

import pytest
from pydantic import ValidationError

from mai.tools.browser import (
    BrowserClickInput,
    BrowserNavigationError,
    BrowserOpenInput,
    BrowserScrollInput,
    BrowserSessionManager,
    register_browser_tools,
)
from mai.tools.registry import ToolRegistry


def test_browser_contract_registered_as_distinct_tools():
    registry = ToolRegistry()
    manager = BrowserSessionManager()
    register_browser_tools(registry, manager)
    assert set(registry.names()) == {
        "browser_open", "browser_snapshot", "browser_click", "browser_scroll", "browser_close"
    }
    assert registry.get("browser_click").input_model is BrowserClickInput


def test_browser_inputs_are_structurally_validated():
    with pytest.raises(ValidationError):
        BrowserOpenInput.model_validate({"url": ""})
    with pytest.raises(ValidationError):
        BrowserClickInput.model_validate({"session_id": "abc", "element_ref": ""})
    with pytest.raises(ValidationError):
        BrowserScrollInput.model_validate({"session_id": "abc", "pixels": 10001})
    with pytest.raises(ValidationError):
        BrowserScrollInput.model_validate({"session_id": "abc", "pixels": "200"}, strict=True)


def test_unknown_session_fails_visibly():
    async def check():
        manager = BrowserSessionManager()
        with pytest.raises(BrowserNavigationError, match="Unknown or closed"):
            await manager.snapshot("nonexistent")
        with pytest.raises(BrowserNavigationError, match="Unknown or closed"):
            await manager.close("nonexistent")
        await manager.close_all()
    asyncio.run(check())
