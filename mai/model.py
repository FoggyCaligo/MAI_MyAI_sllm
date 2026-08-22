from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .agent_stability import (
    action_identity,
    autonomy_guard_message,
    duplicate_guard_message,
    grounding_retry_message,
    grounding_review_messages,
    grounding_review_schema,
    has_real_tool_failure,
    remove_answer_from_schema,
    remove_tool_from_schema,
    schema_has_tool_actions,
    successful_action_identities,
    web_evidence_catalog,
)
from .model_context import prepare_model_messages
from .progress import model_action, model_request_completed, model_request_failed, model_request_started


class ModelContractError(RuntimeError):
    pass


class StructuredModel(Protocol):
    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class OllamaModel:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaModel":
        return cls(
            model=os.getenv("MAI_OLLAMA_MODEL", "gemma4:e4b"),
            base_url=os.getenv("MAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(os.getenv("MAI_OLLAMA_TIMEOUT", "180")),
        )

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        prepared = prepare_model_messages(messages)
        current_messages = list(prepared)
        current_schema = schema
        successful = successful_action_identities(messages)
        removed_duplicate_tools: set[str] = set()
        autonomy_retried = False
        evidence_catalog = web_evidence_catalog(messages)

        while True:
            result = self._request_structured(messages=current_messages, schema=current_schema)

            if result.get("action") == "tool" and isinstance(result.get("arguments"), dict):
                tool_name = result.get("tool")
                if isinstance(tool_name, str):
                    identity = action_identity(tool=tool_name, arguments=result["arguments"])
                    if identity in successful:
                        if tool_name in removed_duplicate_tools:
                            raise ModelContractError(
                                f"Ollama repeated successful tool {tool_name!r} after that tool was removed from schema"
                            )
                        try:
                            current_schema = remove_tool_from_schema(current_schema, tool_name)
                        except ValueError as exc:
                            raise ModelContractError(str(exc)) from exc
                        removed_duplicate_tools.add(tool_name)
                        current_messages = [
                            *current_messages,
                            duplicate_guard_message(tool=tool_name, arguments=result["arguments"]),
                        ]
                        continue

            if (
                result.get("action") == "answer"
                and result.get("outcome") == "blocked"
                and not autonomy_retried
                and schema_has_tool_actions(current_schema)
                and not has_real_tool_failure(messages)
            ):
                autonomy_retried = True
                current_messages = [
                    *current_messages,
                    autonomy_guard_message(rejected_content=str(result.get("content") or "")),
                ]
                continue

            if (
                result.get("action") == "answer"
                and result.get("outcome") != "blocked"
                and evidence_catalog
            ):
                review = self._request_structured(
                    messages=grounding_review_messages(
                        proposed_answer=str(result.get("content") or ""),
                        evidence_catalog=evidence_catalog,
                    ),
                    schema=grounding_review_schema(set(evidence_catalog)),
                )
                decision = review.get("decision")
                if decision == "accept":
                    selected = review.get("evidence_ids")
                    if not isinstance(selected, list) or not selected:
                        raise ModelContractError("grounding accept requires evidence_ids")
                    if any(not isinstance(item, str) or item not in evidence_catalog for item in selected):
                        raise ModelContractError("grounding review selected evidence outside actual web evidence scope")
                    return result
                if decision == "needs_more_evidence":
                    try:
                        current_schema = remove_answer_from_schema(current_schema)
                    except ValueError as exc:
                        raise ModelContractError(str(exc)) from exc
                    current_messages = [
                        *current_messages,
                        grounding_retry_message(
                            proposed_answer=str(result.get("content") or ""),
                            reason=str(review.get("reason") or "grounding review requested more evidence"),
                        ),
                    ]
                    continue
                raise ModelContractError("unexpected grounding review decision")

            return result

    def _request_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        round_number = model_request_started()
        try:
            response = httpx.post(
                f"{self.base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "format": schema,
                },
                timeout=self.timeout_seconds,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = response.text.strip()
                detail = body if body else "<empty response body>"
                raise ModelContractError(
                    f"Ollama HTTP {response.status_code} for model {self.model!r}: {detail}"
                ) from exc
            payload = response.json()
            content = payload.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ModelContractError("Ollama returned empty structured content")
            try:
                result = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ModelContractError("Ollama returned invalid JSON") from exc
            if not isinstance(result, dict):
                raise ModelContractError("Ollama structured response must be an object")
        except Exception:
            model_request_failed(round_number)
            raise

        model_request_completed(round_number)
        model_action(
            str(result.get("action")) if result.get("action") is not None else None,
            str(result.get("tool")) if result.get("tool") is not None else None,
        )
        return result
