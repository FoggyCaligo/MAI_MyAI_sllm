"""Lightweight evidence-grounding review for MAI final answers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter


_LOG = logging.getLogger("uvicorn.error")

_FINAL_REVIEW_SYSTEM = """
You are a grounding-only reviewer. You cannot call tools, choose tools, rewrite the answer, or judge writing quality.

Your only task is to check whether the candidate final answer contains material factual claims that are not supported by the supplied user messages or successful tool results.

Return exactly one JSON object with:
- ok: true or false
- issues: an array of objects with exactly two string fields: claim and reason

Rules:
- Set ok=false only when you can identify a specific material factual claim that lacks support in the supplied evidence, contradicts it, or extends beyond the scope actually observed.
- Each issue must quote or closely identify the unsupported claim and explain why the supplied evidence does not support it.
- Do not judge task alignment, completeness, style, usefulness, or whether a better answer could be written.
- Do not add requirements the user did not ask for.
- Do not reject opinions, uncertainty statements, conversational language, or clearly marked hypotheses merely because they are not factual evidence claims.
- Prior assistant messages are context only, not factual evidence. User messages and successful tool results are factual evidence.
- If a factual claim requires arithmetic, the supporting calculation should appear in the supplied evidence or tool results.
- If evidence only shows part of a list, table, screenshot, search result, or dataset, do not allow claims about the unseen whole unless the evidence establishes completeness.
- If no concrete unsupported material factual claim is identifiable, return ok=true and issues=[].
""".strip()


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    claim: str
    reason: str


@dataclass(frozen=True, slots=True)
class FinalVerificationResult:
    ok: bool
    issues: tuple[VerificationIssue, ...] = ()

    def feedback_message(self) -> str:
        if self.ok:
            return ""
        lines = [
            "The previous answer contained factual claims that were not grounded in the available evidence.",
            "Correct only those unsupported claims. Keep supported parts intact and use tools if more evidence or calculation is needed.",
        ]
        for issue in self.issues:
            lines.append(f'- Claim: {issue.claim}\n  Reason: {issue.reason}')
        return "\n".join(lines)


class FinalGroundingVerifier:
    """Run one lightweight model review for unsupported factual claims."""

    def __init__(
        self,
        reviewer_adapter: OllamaAdapter | None = None,
        *,
        reviewer_timeout_seconds: float = 15.0,
    ) -> None:
        if reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds

    async def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> FinalVerificationResult:
        if self.reviewer_adapter is None:
            return FinalVerificationResult(ok=True)

        user_evidence = [
            _clip_text(str(message.get("content") or ""), 2200)
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ][-12:]
        tool_evidence = [
            {"tool": name, "result": _clip_text(content, 3500)}
            for name, content in successful_tool_results[-12:]
        ]
        payload = {
            "user_evidence": user_evidence,
            "successful_tool_results": tool_evidence,
            "candidate_final": _clip_text(candidate, 7000),
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FINAL_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
        )
        _LOG.info(
            "MAI grounding reviewer start timeout=%.1fs user_messages=%d tool_results=%d candidate_chars=%d",
            self.reviewer_timeout_seconds,
            len(user_evidence),
            len(tool_evidence),
            len(candidate),
        )
        try:
            turn = await asyncio.wait_for(
                self.reviewer_adapter.chat(request),
                timeout=self.reviewer_timeout_seconds,
            )
            data = json.loads(turn.content)
            raw_issues = data.get("issues")
            issues: list[VerificationIssue] = []
            if isinstance(raw_issues, list):
                for item in raw_issues:
                    if not isinstance(item, Mapping):
                        continue
                    claim = str(item.get("claim") or "").strip()
                    reason = str(item.get("reason") or "").strip()
                    if claim and reason:
                        issues.append(VerificationIssue(claim=claim, reason=reason))
            requested_ok = data.get("ok") is True
            ok = requested_ok and not issues
            if not requested_ok and not issues:
                ok = True
            _LOG.info(
                "MAI grounding reviewer result ok=%s issues=%d",
                str(ok).lower(),
                len(issues),
            )
            return FinalVerificationResult(ok=ok, issues=tuple(issues))
        except TimeoutError:
            _LOG.warning(
                "MAI grounding reviewer timed out after %.1fs; failing open",
                self.reviewer_timeout_seconds,
            )
            return FinalVerificationResult(ok=True)
        except Exception as exc:
            _LOG.warning(
                "MAI grounding reviewer failed error_type=%s; failing open",
                type(exc).__name__,
            )
            return FinalVerificationResult(ok=True)


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 80:
        return text[:limit]
    head = (limit * 2) // 3
    tail = limit - head - 24
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]
