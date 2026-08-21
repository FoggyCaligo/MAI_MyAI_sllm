from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .graph import GraphDiscoveryService, GraphRecallService
from .model import StructuredModel, ModelContractError


def _lookup_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "node_lookup"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["queries"],
                "properties": {
                    "queries": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {"type": "string", "minLength": 1},
                    }
                },
            },
        },
    }


def _selection_schema(candidate_ids: list[int], *, allow_lookup: bool) -> dict[str, Any]:
    variants: list[dict[str, Any]] = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": "recall_memory"},
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["focus_node_id"],
                    "properties": {"focus_node_id": {"type": "integer", "enum": candidate_ids}},
                },
            },
        }
    ]
    if allow_lookup:
        variants.append(_lookup_schema())
    return {"oneOf": variants}


@dataclass(slots=True)
class MandatoryMemoryDiscovery:
    model: StructuredModel
    discovery: GraphDiscoveryService
    recall: GraphRecallService

    def run(self, *, user_id: str, user_text: str) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Before normal work, discover relevant graph memory. "
                    "Use exactly one structured action per round. "
                    "First call node_lookup with one to three lexical node-name queries you choose."
                ),
            },
            {"role": "user", "content": user_text},
        ]

        first = self.model.structured(messages=messages, schema=_lookup_schema())
        self._require_tool(first, "node_lookup")
        lookup = self.discovery.node_lookup(user_id=user_id, queries=first["arguments"]["queries"])
        messages.append({"role": "assistant", "content": str(first)})
        messages.append({"role": "tool", "content": str({"tool": "node_lookup", "result": lookup})})

        candidates = {int(node["node_id"]): node for node in lookup.get("matches", [])}
        if not candidates:
            return {"status": "no_match", "lookup": lookup, "recall": None}

        while True:
            action = self.model.structured(
                messages=messages,
                schema=_selection_schema(sorted(candidates), allow_lookup=True),
            )
            tool = action.get("tool")
            if tool == "recall_memory":
                focus = int(action["arguments"]["focus_node_id"])
                if focus not in candidates:
                    raise ModelContractError("focus_node_id is outside lookup candidate scope")
                recalled = self.recall.recall_one_depth(user_id=user_id, focus_node_id=focus)
                return {"status": "recalled", "lookup": lookup, "recall": recalled}
            if tool == "node_lookup":
                self._require_tool(action, "node_lookup")
                lookup = self.discovery.node_lookup(user_id=user_id, queries=action["arguments"]["queries"])
                messages.append({"role": "assistant", "content": str(action)})
                messages.append({"role": "tool", "content": str({"tool": "node_lookup", "result": lookup})})
                for node in lookup.get("matches", []):
                    candidates[int(node["node_id"])] = node
                if not candidates:
                    return {"status": "no_match", "lookup": lookup, "recall": None}
                continue
            raise ModelContractError("unexpected discovery action")

    @staticmethod
    def _require_tool(action: dict[str, Any], tool: str) -> None:
        if action.get("action") != "tool" or action.get("tool") != tool or not isinstance(action.get("arguments"), dict):
            raise ModelContractError(f"expected structured {tool} tool action")
