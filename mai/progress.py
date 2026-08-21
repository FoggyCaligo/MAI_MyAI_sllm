from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


_logger = logging.getLogger("uvicorn.error")
_turn_id: ContextVar[str | None] = ContextVar("mai_turn_id", default=None)
_phase: ContextVar[str | None] = ContextVar("mai_phase", default=None)
_round: ContextVar[int] = ContextVar("mai_model_round", default=0)


def _prefix() -> str:
    turn_id = _turn_id.get()
    phase = _phase.get()
    parts = ["[Mai]"]
    if turn_id:
        parts.append(f"[turn={turn_id}]")
    if phase:
        parts.append(f"[{phase}]")
    return "".join(parts)


def turn_started(turn_id: str) -> None:
    _logger.info("[Mai][turn=%s] turn started", turn_id)


def turn_completed(turn_id: str) -> None:
    _logger.info("[Mai][turn=%s] turn completed", turn_id)


def turn_failed(turn_id: str) -> None:
    _logger.exception("[Mai][turn=%s] turn failed", turn_id)


@contextmanager
def phase(turn_id: str, name: str) -> Iterator[None]:
    turn_token: Token[str | None] = _turn_id.set(turn_id)
    phase_token: Token[str | None] = _phase.set(name)
    round_token: Token[int] = _round.set(0)
    _logger.info("%s phase started", _prefix())
    try:
        yield
    except Exception:
        _logger.exception("%s phase failed", _prefix())
        raise
    else:
        _logger.info("%s phase completed", _prefix())
    finally:
        _round.reset(round_token)
        _phase.reset(phase_token)
        _turn_id.reset(turn_token)


def model_request_started() -> int:
    round_number = _round.get() + 1
    _round.set(round_number)
    _logger.info("%s[round=%d] model request started", _prefix(), round_number)
    return round_number


def model_request_completed(round_number: int) -> None:
    _logger.info("%s[round=%d] model request completed", _prefix(), round_number)


def model_request_failed(round_number: int) -> None:
    _logger.exception("%s[round=%d] model request failed", _prefix(), round_number)


def model_action(action: str | None, tool: str | None = None) -> None:
    if tool:
        _logger.info("%s model action=%s tool=%s", _prefix(), action, tool)
    else:
        _logger.info("%s model action=%s", _prefix(), action)


def tool_started(tool: str) -> None:
    _logger.info("%s tool=%s started", _prefix(), tool)


def tool_completed(tool: str) -> None:
    _logger.info("%s tool=%s completed", _prefix(), tool)
