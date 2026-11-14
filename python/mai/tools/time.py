"""Native local date/time tool.

This tool reads the operating system's current local timezone-aware clock. It
accepts no semantic query text and performs no routing or string inference.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .registry import EmptyToolInput, ToolRegistry


def current_time() -> dict[str, object]:
    """Return the current local and UTC time as timezone-aware values."""

    local_now = datetime.now().astimezone()
    utc_now = local_now.astimezone(timezone.utc)
    offset = local_now.utcoffset()
    return {
        "local_iso": local_now.isoformat(),
        "utc_iso": utc_now.isoformat(),
        "timezone_name": local_now.tzname(),
        "utc_offset_seconds": None if offset is None else int(offset.total_seconds()),
        "date": local_now.date().isoformat(),
        "time": local_now.timetz().isoformat(),
    }


def register_time_tools(
    registry: ToolRegistry,
    *,
    timeout_seconds: float | None = 10,
) -> None:
    registry.add(
        name="current_time",
        description=(
            "Read the computer's current local date and time from the operating system clock, including the "
            "timezone offset and UTC time. Use this when the answer depends on the actual current time, date, "
            "today, tonight, elapsed time, or another time-relative fact."
        ),
        input_model=EmptyToolInput,
        handler=current_time,
        timeout_seconds=timeout_seconds,
        category="time",
    )
