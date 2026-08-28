"""Registration bundle for external read-only information tools."""
from __future__ import annotations

from .market import register_market_tools
from .registry import ToolRegistry
from .web import register_web_tools


def register_external_information_tools(
    registry: ToolRegistry,
    *,
    web_timeout_seconds: float | None = 30,
    market_timeout_seconds: float | None = 30,
) -> None:
    register_web_tools(registry, timeout_seconds=web_timeout_seconds)
    register_market_tools(registry, timeout_seconds=market_timeout_seconds)
