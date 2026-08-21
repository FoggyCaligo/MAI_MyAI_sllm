from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .memory.service import MemoryService
from .model import ModelContractError, OllamaModel


SYSTEM = (
    "You are MAI, a local personal assistant. Follow the structured response schema exactly. "
    "Memory recall is mandatory before answering. The answer draft is fixed before memory commit. "
    "During memory commit, store durable semantic information from this turn and do not rewrite the answer."
)


@dataclass(slots=True)
class AgentResult:
    text: str
    used_tools: list[str]
    tool_events: list[dict[str, Any]]
    memory_writes: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


@dataclass(slots=True)
class Agent:
    model: OllamaModel
    memory: MemoryService
    max_memory_writes: int = 4

    async def run(self, *, user_id: str, message: str, model: str | None = None) -> AgentResult:
        used_tools: list[str] = []
        tool_events: list[dict[str, Any]] = []
        memory_writes: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []

        recall_action = await self.model.structured(
            system=SYSTEM + "\nPhase: recall. Choose exactly one recall_memory tool action.",
            user={"message": message},
            schema=_recall_schema(),
            model=model,
        )
        diagnostics.append({"layer": "model", "phase": "recall", "elapsed_ms": self.model.last_elapsed_ms})
        _require_action(recall_action, "tool")
        if recall_action.get("tool") != "recall_memory":
            raise ModelContractError("recall phase must call recall_memory")
        arguments = recall_action.get("arguments") or {}
        limit = int(arguments.get("limit", 8))
        recall_started = time.perf_counter()
        recalled = self.memory.recall(user_id=user_id, limit=limit)
        recall_ms = round((time.perf_counter() - recall_started) * 1000, 3)
        diagnostics.append({"layer": "sqlite", "phase": "recall", "elapsed_ms": recall_ms})
        used_tools.append("recall_memory")
        tool_events.append({
            "tool": "recall_memory",
            "arguments": {"limit": limit},
            "result": {"ok": True, "results": recalled, "db_elapsed_ms": recall_ms},
        })

        answer_action = await self.model.structured(
            system=SYSTEM + "\nPhase: answer draft. Produce the final user-visible answer now.",
            user={"message": message, "recalled_memory": recalled},
            schema=_answer_schema(),
            model=model,
        )
        diagnostics.append({"layer": "model", "phase": "answer", "elapsed_ms": self.model.last_elapsed_ms})
        _require_action(answer_action, "answer")
        draft = str(answer_action.get("content") or "").strip()
        if not draft:
            raise ModelContractError("answer action requires non-empty content")

        segment_started = time.perf_counter()
        writable_terms = _writable_terms(self.memory, user_text=message, assistant_text=draft)
        segment_ms = round((time.perf_counter() - segment_started) * 1000, 3)
        diagnostics.append({
            "layer": "sentence_breaker",
            "phase": "writable_terms",
            "elapsed_ms": segment_ms,
            "term_count": len(writable_terms),
        })

        mutation_succeeded = False
        for write_index in range(self.max_memory_writes + 1):
            action = await self.model.structured(
                system=(
                    SYSTEM
                    + "\nPhase: memory commit. The answer is already fixed. "
                    + (
                        "At least one mutation succeeded; either write another durable relation or choose done."
                        if mutation_succeeded
                        else "No mutation succeeded yet; write one durable relation. done is not available."
                    )
                ),
                user={
                    "message": message,
                    "answer_draft": draft,
                    "writable_terms": writable_terms,
                    "memory_writes": memory_writes,
                },
                schema=_memory_schema(writable_terms, allow_done=mutation_succeeded),
                model=model,
            )
            diagnostics.append({
                "layer": "model",
                "phase": "memory_commit",
                "round": write_index + 1,
                "elapsed_ms": self.model.last_elapsed_ms,
            })
            if action.get("action") == "done":
                if not mutation_succeeded:
                    raise ModelContractError("done is invalid before a successful memory mutation")
                return AgentResult(
                    text=draft,
                    used_tools=used_tools,
                    tool_events=tool_events,
                    memory_writes=memory_writes,
                    diagnostics=diagnostics,
                )
            _require_action(action, "tool")
            if action.get("tool") != "write_memory":
                raise ModelContractError("memory phase permits only write_memory or done")
            args = action.get("arguments")
            if not isinstance(args, dict):
                raise ModelContractError("write_memory arguments must be an object")
            subject = _resolve_endpoint(args.get("subject"), writable_terms, user_id=user_id)
            object_ = _resolve_endpoint(args.get("object"), writable_terms, user_id=user_id)
            relation = str(args.get("relation") or "").strip()
            result = self.memory.write_relation(
                user_id=user_id,
                subject=subject,
                relation=relation,
                object_=object_,
                source_text=message,
            )
            diagnostics.append({
                "layer": "sqlite",
                "phase": "write_memory",
                "round": write_index + 1,
                "elapsed_ms": result.get("db_elapsed_ms"),
                "transaction_elapsed_ms": result.get("transaction_elapsed_ms"),
            })
            mutation_succeeded = True
            used_tools.append("write_memory")
            memory_writes.append(result)
            tool_events.append({"tool": "write_memory", "arguments": args, "result": result})

        raise RuntimeError("memory commit exceeded the configured write limit without choosing done")


def _require_action(action: dict[str, Any], expected: str) -> None:
    if action.get("action") != expected:
        raise ModelContractError(f"expected action={expected}, got {action.get('action')!r}")


def _writable_terms(memory: MemoryService, *, user_text: str, assistant_text: str) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, text in (("user", user_text), ("assistant", assistant_text)):
        index = 0
        for raw in memory.segment(text):
            token = raw.strip()
            if not token or token in seen or token.isspace():
                continue
            seen.add(token)
            terms.append({"term_id": f"{source}:{index}", "source": source, "text": token})
            index += 1
    if not terms:
        raise RuntimeError("Sentence_Breaker produced no writable terms for memory commit")
    return terms


def _resolve_endpoint(endpoint: object, writable_terms: list[dict[str, str]], *, user_id: str) -> str:
    if not isinstance(endpoint, dict):
        raise ModelContractError("memory endpoint must be an object")
    if endpoint.get("kind") == "user":
        return f"user::{user_id}"
    term_id = str(endpoint.get("term_id") or "").strip()
    for item in writable_terms:
        if item["term_id"] == term_id:
            return item["text"]
    raise ModelContractError(f"term_id is outside the current memory scope: {term_id}")


def _recall_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["tool"]},
            "tool": {"type": "string", "enum": ["recall_memory"]},
            "arguments": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 12}},
                "additionalProperties": False,
            },
        },
        "required": ["action", "tool", "arguments"],
        "additionalProperties": False,
    }


def _answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["answer"]},
            "content": {"type": "string", "minLength": 1},
        },
        "required": ["action", "content"],
        "additionalProperties": False,
    }


def _memory_schema(writable_terms: list[dict[str, str]], *, allow_done: bool) -> dict[str, Any]:
    term_ids = [item["term_id"] for item in writable_terms]
    endpoint = {
        "oneOf": [
            {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["user"]}},
                "required": ["kind"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"term_id": {"type": "string", "enum": term_ids}},
                "required": ["term_id"],
                "additionalProperties": False,
            },
        ]
    }
    write_action = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["tool"]},
            "tool": {"type": "string", "enum": ["write_memory"]},
            "arguments": {
                "type": "object",
                "properties": {
                    "subject": endpoint,
                    "relation": {"type": "string", "minLength": 1},
                    "object": endpoint,
                },
                "required": ["subject", "relation", "object"],
                "additionalProperties": False,
            },
        },
        "required": ["action", "tool", "arguments"],
        "additionalProperties": False,
    }
    if not allow_done:
        return write_action
    return {
        "oneOf": [
            write_action,
            {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["done"]}},
                "required": ["action"],
                "additionalProperties": False,
            },
        ]
    }
