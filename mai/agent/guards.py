"""Structural Agent guards independent from model semantics and tool routing."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class AgentGuardError(RuntimeError):
    """Base class for structural guard stops."""


class RepeatedToolCallError(AgentGuardError):
    """Legacy error type retained for compatibility; fixed call-count caps are no longer enforced."""


class RepeatedToolFailureError(AgentGuardError):
    """The same native tool call kept reproducing one identical failure outcome."""


class NoProgressError(AgentGuardError):
    """The agent repeated the same structural state without making progress."""


@dataclass(frozen=True, slots=True)
class GuardConfig:
    warn_identical_failures: int = 3
    max_identical_failures: int = 5
    max_no_progress_rounds: int = 5

    def __post_init__(self) -> None:
        for name, value in (
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
    compares canonical native-call arguments, exact execution outcomes, and the
    exact frozen set of missing required tools.

    Recovery policy:
    - there is no fixed per-run cap on model rounds or successful tool calls;
    - changed calls or changed outcomes are treated as structural progress;
    - an identical failure streak is surfaced to the model before it is stopped;
    - after the model has observed the configured number of identical failures,
      only another unchanged failing call is blocked;
    - repeated tool-round outcomes and repeated final attempts with the same
      missing requirements are structural no-progress signals.
    """

    def __init__(self, config: GuardConfig | None = None) -> None:
        self.config = config or GuardConfig()
        self._failure_streak_key: tuple[str, str | None, str] | None = None
        self._failure_streak_count = 0
        self._previous_round_signature: str | None = None
        self._identical_rounds = 0
        self._previous_verification_signature: str | None = None
        self._identical_verification_rejections = 0
        self._similar_failures: dict[tuple[str, str, str], int] = {}

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

    def after_similar_failure(
        self, name: str, arguments: Mapping[str, Any], *, ok: bool,
        error_type: str | None, threshold: int = 2,
    ) -> str | None:
        """Warn on a related *failure family*, never block a changed action.

        No inference or model calls: grouping is based only on tool name,
        reported error class and a conservative execution target. Different
        outcomes and commands remain available as recovery attempts.
        """
        if ok or not error_type or threshold < 2:
            return None
        target = str(arguments.get("at_remote_pc", False))
        if name == "terminal_run":
            command = str(arguments.get("command", "")).strip().lower()
            # Strip common wrappers and use a command family, not entire
            # arguments; avoid claims about semantic equivalence.
            executable = re.search(r"(?:^|[\s|;&])([\w.-]+(?:\.exe)?)(?=\s|$|[|;&])", command)
            family = executable.group(1) if executable else ""
            family = {"cmd.exe": "cmd", "powershell.exe": "powershell"}.get(family, family)
            target += ":" + family
        elif name in {"web_fetch", "file_read", "code_read"}:
            target += ":" + str(arguments.get("url") or arguments.get("path") or arguments.get("target") or "")
        elif name == "web_search":
            # A repeated provider-level error across different queries warrants
            # a warning, but not a block: distinct searches may be valuable.
            target += ":search-provider" if error_type == "WebSearchError" else ":" + str(arguments.get("query") or arguments.get("queries") or "")
        elif name == "calculator":
            # Invalid arithmetic expressions in different calls are also a
            # recovery signal; warn only, never impose a call cap.
            target += ":arithmetic"
        else:
            return None
        key = (name, error_type, target)
        count = self._similar_failures.get(key, 0) + 1
        self._similar_failures[key] = count
        if count == threshold:
            return (
                f"Related tool failures: {name} returned {error_type} "
                f"{count} times for the same conservative target family. "
                "This does not prove the attempted commands are equivalent. "
                "Inspect earlier failures and prefer a genuinely different "
                "approach rather than superficial argument or shell rewrites. "
                "Do not claim unavailable evidence is verified."
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

    def after_verification_rejection(self, issues: Sequence[Mapping[str, Any]]) -> None:
        """Detect repeated verifier defects independently of tool-round progress.

        Comparisons use the verifier's structured issue/claim fields, not prose
        matching. A changed finding is a new structural state.
        """
        if not issues:
            raise ValueError("verification rejection requires at least one issue")
        signature = _fingerprint(list(issues))
        if signature == self._previous_verification_signature:
            self._identical_verification_rejections += 1
        else:
            self._previous_verification_signature = signature
            self._identical_verification_rejections = 1
        if self._identical_verification_rejections > self.config.max_no_progress_rounds:
            raise NoProgressError(
                "verifier rejected the same structured defects more than "
                f"{self.config.max_no_progress_rounds} consecutive times"
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
