from __future__ import annotations

import asyncio
from datetime import datetime

from mai.llm.models import NativeToolCall
from mai.tools.registry import ToolRegistry
from mai.tools.time import current_time, register_time_tools


def test_current_time_returns_timezone_aware_local_and_utc_values():
    result = current_time()

    local_value = datetime.fromisoformat(str(result["local_iso"]))
    utc_value = datetime.fromisoformat(str(result["utc_iso"]))

    assert local_value.tzinfo is not None
    assert utc_value.tzinfo is not None
    assert result["date"] == local_value.date().isoformat()
    assert isinstance(result["utc_offset_seconds"], int)


def test_register_time_tools_exposes_zero_argument_native_tool():
    registry = ToolRegistry()
    register_time_tools(registry)

    assert registry.names() == ("current_time",)
    result = asyncio.run(registry.invoke(NativeToolCall(name="current_time", arguments={})))
    assert "local_iso" in result
    assert "utc_iso" in result
