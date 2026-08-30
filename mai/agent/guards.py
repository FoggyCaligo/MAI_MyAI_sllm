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
    """The same native tool call kept reproducing one identical failure outcome."""


class NoProgressError(AgentGuardError):
    """Consecutive tool rounds produced the same structural execution signature."""


@dataclass(frozen=True, slots=True)
class GuardConfig:
    max_rounds: int = 30
    max_identical_calls: int = 10
    warn_identical_failures: int = 3
    max_identical_failures: int = 5
    max_no_progress_rounds: int = 5

    def __post_init__(self) -> None:
        for name, value in (
            ("max_rounds", self.max_rounds),
            ("max_identical_calls", self.max_identical_calls),
            ("warn_identical_failures", self.warn_identical_failures),
            ("max_identical_failures", self.max_identical_failures),
            ("max_no_progress_rounds", self.max_no_progress_rounds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.warn_identical_failures > self.max_identical_failures:
            raise ValueError("warn_identical_failures must be <= max_identical_failures")


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

    Recovery policy:
    - changed calls or changed outcomes are treated as structural progress;
    - an identical failure streak is surfaced to the model before it is stopped;
    - after the model has observed the configured number of identical failures,
      only another unchanged call is blocked;
    - global round/call ceilings remain as final safety bounds.
    """

    def __init__(self, config: GuardConfig | None = None) -> None:
        self.config = config or GuardConfig()
        self._call_counts: dict[str, int] = {}
        self._failure_streak_key: tuple[str, str | None, str] | None = None
        self._failure_streak_count = 0
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
        if (
            self._failure_streak_key is not None
            and self._failure_streak_key[0] == fingerprint
            and self._failure_streak_count >= self.config.max_identical_failures
        ):
            raise RepeatedToolFailureError(
                "same native tool call already produced the same failure outcome "
                f"{self._failure_streak_count} consecutive times; refusing another unchanged execution"
            )

        count = self._call_counts.get(fingerprint, 0) + 1
        self._call_counts[fingerprint] = count
        if count > self.config.max_identical_calls:
            raise RepeatedToolCallError(
                f"native tool call repeated more than {self.config.max_identical_calls} times"
            )
        return fingerprint

    def after_tool_execution(self, observation: ExecutionObservation) -> str | None:
        if observation.ok:
            self._failure_streak_key = None
            self._failure_streak_count = 0
            return None

        key = (
            observation.call_fingerprint,
            observation.error_type,
            observation.content_fingerprint,
        )
        if key == self._failure_streak_key:
            self._failure_streak_count += 1
        else:
            self._failure_streak_key = key
            self._failure_streak_count = 1

        if self._failure_streak_count == self.config.warn_identical_failures:
            return (
                "Structural retry warning: the same native tool call has produced the exact same failure outcome "
                f"{self._failure_streak_count} consecutive times. The tool failures above are real. "
                "Do not repeat the identical call unchanged; change the call arguments, choose another available "
                "tool or approach, or finish by reporting the failure if no valid recovery remains."
            )
        return None

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
