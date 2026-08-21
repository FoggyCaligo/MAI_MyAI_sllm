from __future__ import annotations

from typing import Any


def startup(*, sentence_mode: str, sentence_db: str, fallback_reason: str | None = None) -> None:
    detail = f"sentence={sentence_mode}"
    if sentence_db:
        detail += f" db={sentence_db}"
    if fallback_reason:
        detail += f" reason={_short(fallback_reason, 120)}"
    print(f"[MAI] ready | {detail}", flush=True)


def chat_summary(diagnostics: list[dict[str, Any]]) -> None:
    parts: list[str] = []
    for item in diagnostics:
        layer = str(item.get("layer") or "")
        phase = str(item.get("phase") or "")
        elapsed = item.get("elapsed_ms")
        if elapsed is None:
            continue
        label = _label(layer, phase, item)
        parts.append(f"{label}={_ms(elapsed)}")
    if parts:
        print("[MAI] chat | " + " | ".join(parts), flush=True)


def failure(message: str) -> None:
    print(f"[MAI] error | {_short(message, 240)}", flush=True)


def _label(layer: str, phase: str, item: dict[str, Any]) -> str:
    if layer == "model":
        if phase == "memory_commit":
            return f"model.memory{item.get('round', '')}"
        return f"model.{phase}"
    if layer == "sentence_breaker":
        mode = str(item.get("mode") or "sentence_breaker")
        return f"segment.{mode}"
    if layer == "sqlite":
        if phase == "write_memory":
            return f"db.write{item.get('round', '')}"
        return f"db.{phase}"
    return f"{layer}.{phase}".strip(".")


def _ms(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number < 1000:
        return f"{number:.1f}ms"
    return f"{number / 1000:.2f}s"


def _short(value: str, limit: int) -> str:
    one_line = " ".join(str(value).split())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."
