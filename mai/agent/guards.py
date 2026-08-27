"""Structural Agent guards independent from model semantics and tool routing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class AgentGuardError(RuntimeError):
    """Base class for structural guard stops."""


class AgentRoundLimitExceeded(AgentGuardError):
    """The model requested another tool round beyond the configured limit."""


class RepeatedToolCallError(AgentGuardError):
    """The same native tool call was requested too many times in one run."""


class RepeatedToolFailureError(AgentGuardError):
    """The same native tool call failed in the same way too many times."""


class NoProgressError(AgentGuardError):
    """Consecutive tool rounds produced the same structural execution signature."""


@dataclass(frozen=True, slots=True)
class GuardConfig:
    max_rounds: int = 30
    max_identical_calls: int = 3
    max_identical_failures: int = 2
    max_no_progress_rounds: int = 2

    def __post_init__(self) -> None:
        for name, value in (
            ("max_rounds", self.max_rounds),
            ("max_identical_calls", self.max_identical_calls),
            ("max_identical_failures", self.max_identical_failures),
            ("max_no_progress_rounds", self.max_no_progress_rounds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    call_fingerprint: str
    ok: bool
    content_fingerprint: str
    error_type: str | None = None


class AgentGuard:
    """Per-run structural guard state.

    The guard never interprets user text, tool names, or result meaning. It only
    compares canonical native-call arguments and exact execution outcomes.
    """

    def __init__(self, config: GuardConfig | None = None) -> None:
        self.config = config or GuardConfig()
        self._call_counts: dict[str, int] = {}
        self._failure_counts: dict[tuple[str, str | None], int] = {}
        self._previous_round_signature: str | None = None
        self._identical_rounds = 0

    def before_model_round(self, round_number: int) -> None:
        if round_number > self.config.max_rounds:
            raise AgentRoundLimitExceeded(
                f"agent exceeded max_rounds={self.config.max_rounds}"
            )

    def before_tool_round(self, round_number: int) -> None:
        """Reject new side effects when no later model round can consume them."""

        if round_number >= self.config.max_rounds:
            raise AgentRoundLimitExceeded(
                f"agent reached max_rounds={self.config.max_rounds} while the model still requested tools"
            )

    def before_tool_call(self, name: str, arguments: Mapping[str, Any]) -> str:
        fingerprint = call_fingerprint(name, arguments)
        count = self._call_counts.get(fingerprint, 0) + 1
        self._call_counts[fingerprint] = count
        if count > self.config.max_identical_calls:
            raise RepeatedToolCallError(
                f"native tool call repeated more than {self.config.max_identical_calls} times"
            )
        return fingerprint

    def after_tool_execution(self, observation: ExecutionObservation) -> None:
        if observation.ok:
            return
        key = (observation.call_fingerprint, observation.error_type)
        count = self._failure_counts.get(key, 0) + 1
        self._failure_counts[key] = count
        if count > self.config.max_identical_failures:
            raise RepeatedToolFailureError(
                "same native tool call repeated the same failure more than "
                f"{self.config.max_identical_failures} times"
            )

    def after_tool_round(self, observations: Sequence[ExecutionObservation]) -> None:
        signature = _fingerprint([
            {
                "call": item.call_fingerprint,
                "ok": item.ok,
                "content": item.content_fingerprint,
                "error_type": item.error_type,
            }
            for item in observations
        ])
        if signature == self._previous_round_signature:
            self._identical_rounds += 1
        else:
            self._previous_round_signature = signature
            self._identical_rounds = 1
        if self._identical_rounds > self.config.max_no_progress_rounds:
            raise NoProgressError(
                "consecutive tool rounds produced the same structural result more than "
                f"{self.config.max_no_progress_rounds} times"
            )


def call_fingerprint(name: str, arguments: Mapping[str, Any]) -> str:
    return _fingerprint({"name": name, "arguments": dict(arguments)})


def content_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _fingerprint(value: Any) -> str:
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("guard fingerprint input must be JSON serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
