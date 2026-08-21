from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from uuid import uuid4

from .graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from .memory_completion import MandatoryMemoryCompletion
from .memory_discovery import MandatoryMemoryDiscovery
from .memory_write import MemoryTurnScope
from .model import ModelContractError, StructuredModel
from .progress import phase, tool_completed, tool_started, turn_completed, turn_failed, turn_started


class PathProvenanceError(PermissionError):
    """Raised when a file action targets a path not established by this turn."""


@dataclass(slots=True)
class PathProvenance:
    paths: set[str] = field(default_factory=set)

    @staticmethod
    def normalize(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    def add(self, path: str | Path) -> None:
        self.paths.add(self.normalize(path))

    def add_many(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.add(path)

    def remove_many(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.paths.discard(self.normalize(path))

    def require(self, path: str | Path) -> None:
        normalized = self.normalize(path)
        if normalized not in self.paths:
            raise PathProvenanceError(f"path is outside current-turn discovered scope: {normalized}")


class WorkTool(Protocol):
    name: str
    description: str

    def schema(self) -> dict[str, Any]: ...

    def execute(self, *, arguments: dict[str, Any], context: "WorkContext") -> Any: ...


@dataclass(frozen=True, slots=True)
class WorkContext:
    user_id: str
    turn_id: str
    user_text: str
    path_provenance: PathProvenance = field(default_factory=PathProvenance)


@dataclass(slots=True)
class FunctionWorkTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], WorkContext], Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": self.input_schema,
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> Any:
        return self.handler(arguments, context)


def _answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "content"],
        "properties": {
            "action": {"const": "answer"},
            "content": {"type": "string", "minLength": 1},
        },
    }


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


def _recall_schema(candidate_ids: set[int]) -> dict[str, Any]:
    return {
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
                "properties": {
                    "focus_node_id": {
                        "type": "integer",
                        "enum": sorted(candidate_ids),
                    }
                },
            },
        },
    }


def _combined_schema(variants: list[dict[str, Any]]) -> dict[str, Any]:
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


def _progress_keys(tool: WorkTool, result: Any) -> set[str] | None:
    extractor = getattr(tool, "progress_keys", None)
    if not callable(extractor):
        return None
    keys = extractor(result)
    if keys is None:
        return None
    return {str(key) for key in keys}


def _discovered_paths(tool: WorkTool, result: Any) -> set[str]:
    extractor = getattr(tool, "discovered_paths", None)
    if not callable(extractor):
        return set()
    return {PathProvenance.normalize(path) for path in extractor(result)}


def _removed_paths(tool: WorkTool, result: Any) -> set[str]:
    extractor = getattr(tool, "removed_paths", None)
    if not callable(extractor):
        return set()
    return {PathProvenance.normalize(path) for path in extractor(result)}


def _required_paths(tool: WorkTool, arguments: dict[str, Any]) -> set[str]:
    extractor = getattr(tool, "required_paths", None)
    if not callable(extractor):
        return set()
    return {PathProvenance.normalize(path) for path in extractor(arguments)}


def _schema_for_context(tool: WorkTool, context: WorkContext) -> dict[str, Any] | None:
    builder = getattr(tool, "schema_for_paths", None)
    if callable(builder):
        return builder(set(context.path_provenance.paths))
    return tool.schema()


@dataclass(slots=True)
class AgentLifecycle:
    repository: GraphRepository
    model: StructuredModel
    discovery_phase: MandatoryMemoryDiscovery
    discovery: GraphDiscoveryService
    recall: GraphRecallService
    memory_completion: MandatoryMemoryCompletion
    work_tools: list[WorkTool] = field(default_factory=list)

    def run(
        self,
        *,
        user_id: str,
        user_text: str,
        turn_id: str | None = None,
        attachment_paths: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        clean_user = str(user_text).strip()
        if not clean_user:
            raise ValueError("user_text must be non-empty")
        resolved_turn_id = str(turn_id or uuid4())
        path_provenance = PathProvenance()
        path_provenance.add_many(attachment_paths)
        turn_started(resolved_turn_id)

        try:
            with phase(resolved_turn_id, "turn_initialization"):
                self.repository.ensure_user_anchor(
                    user_id=user_id,
                    turn_id=resolved_turn_id,
                    source_text="turn initialization",
                )

            with phase(resolved_turn_id, "memory_discovery"):
                discovery_result = self.discovery_phase.run(user_id=user_id, user_text=clean_user)

            recall_results: list[dict[str, Any]] = []
            initial_recall = discovery_result.get("recall")
            if initial_recall:
                recall_results.append(initial_recall)

            candidate_ids = {
                int(node["node_id"])
                for node in (discovery_result.get("lookup") or {}).get("matches", [])
            }

            with phase(resolved_turn_id, "work"):
                fixed_answer, work_events = self._run_work_phase(
                    context=WorkContext(
                        user_id=user_id,
                        turn_id=resolved_turn_id,
                        user_text=clean_user,
                        path_provenance=path_provenance,
                    ),
                    candidate_ids=candidate_ids,
                    recall_results=recall_results,
                )

            aggregate_recall = self._aggregate_recall(recall_results)
            turn = MemoryTurnScope.from_recall(
                user_id=user_id,
                turn_id=resolved_turn_id,
                user_text=clean_user,
                assistant_text=fixed_answer,
                recall_result=aggregate_recall,
            )
            with phase(resolved_turn_id, "memory_mutation"):
                memory_result = self.memory_completion.run(turn=turn, recall_result=aggregate_recall)
            if memory_result.get("status") != "done":
                raise RuntimeError("memory completion did not reach done")

            result = {
                "status": "completed",
                "turn_id": resolved_turn_id,
                "answer": fixed_answer,
                "discovery": discovery_result,
                "work_events": work_events,
                "memory": memory_result,
            }
        except Exception:
            turn_failed(resolved_turn_id)
            raise

        turn_completed(resolved_turn_id)
        return result

    def _run_work_phase(
        self,
        *,
        context: WorkContext,
        candidate_ids: set[int],
        recall_results: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        tools = {tool.name: tool for tool in self.work_tools}
        if len(tools) != len(self.work_tools):
            raise ValueError("work tool names must be unique")
        if {"node_lookup", "recall_memory"} & set(tools):
            raise ValueError("work tools may not shadow built-in memory tools")

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Memory discovery is complete. Perform normal work using exactly one structured "
                    "action per round. You may use available tools, inspect more memory with node_lookup "
                    "and recall_memory, or produce one final answer. Existing-file actions may only use paths "
                    "established by current-turn attachments, file_create, or file/code discovery tool results."
                ),
            },
            {"role": "user", "content": context.user_text},
        ]
        events: list[dict[str, Any]] = []
        allow_lookup = True
        available_tools = set(tools)
        seen_progress: dict[str, set[str]] = {}

        while True:
            variants = [_answer_schema()]
            if allow_lookup:
                variants.append(_lookup_schema())
            if candidate_ids:
                variants.append(_recall_schema(candidate_ids))
            exposed_tools: set[str] = set()
            for name, tool in tools.items():
                if name not in available_tools:
                    continue
                tool_schema = _schema_for_context(tool, context)
                if tool_schema is None:
                    continue
                variants.append(tool_schema)
                exposed_tools.add(name)
            action = self.model.structured(messages=messages, schema=_combined_schema(variants))

            if action.get("action") == "answer":
                content = str(action.get("content", "")).strip()
                if not content:
                    raise ModelContractError("answer content must be non-empty")
                return content, events

            if action.get("action") != "tool" or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("work phase requires one tool action or one answer")

            tool_name = action.get("tool")
            arguments = action["arguments"]
            if not isinstance(tool_name, str):
                raise ModelContractError("work tool name must be a string")

            tool_started(tool_name)
            if tool_name == "node_lookup":
                if not allow_lookup:
                    raise ModelContractError("node_lookup is unavailable after a no-progress lookup")
                previous_candidate_ids = set(candidate_ids)
                result = self.discovery.node_lookup(user_id=context.user_id, queries=arguments["queries"])
                for node in result.get("matches", []):
                    candidate_ids.add(int(node["node_id"]))
                allow_lookup = candidate_ids != previous_candidate_ids
            elif tool_name == "recall_memory":
                focus = int(arguments["focus_node_id"])
                if focus not in candidate_ids:
                    raise ModelContractError("focus_node_id is outside actual lookup candidate scope")
                result = self.recall.recall_one_depth(user_id=context.user_id, focus_node_id=focus)
                recall_results.append(result)
            elif tool_name in tools:
                if tool_name not in exposed_tools:
                    raise ModelContractError(f"{tool_name} is unavailable in the current work scope")
                tool = tools[tool_name]
                for required_path in _required_paths(tool, arguments):
                    context.path_provenance.require(required_path)
                result = tool.execute(arguments=arguments, context=context)
                context.path_provenance.add_many(_discovered_paths(tool, result))
                context.path_provenance.remove_many(_removed_paths(tool, result))
                keys = _progress_keys(tool, result)
                if keys is not None:
                    prior = seen_progress.setdefault(tool_name, set())
                    new_keys = keys - prior
                    prior.update(keys)
                    if not new_keys:
                        available_tools.discard(tool_name)
            else:
                raise ModelContractError("unexpected tool in work phase")
            tool_completed(tool_name)

            event = {"tool": tool_name, "arguments": arguments, "result": result}
            events.append(event)
            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "tool", "content": str(event)})

    @staticmethod
    def _aggregate_recall(results: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not results:
            return None

        nodes: dict[int, dict[str, Any]] = {}
        edges: dict[int, dict[str, Any]] = {}
        origin_nodes: dict[int, dict[str, Any]] = {}
        origin_edges: dict[int, dict[str, Any]] = {}

        for result in results:
            for node in result.get("nodes", []):
                nodes[int(node["node_id"])] = node
            for edge in result.get("edges", []):
                edges[int(edge["edge_id"])] = edge
            origin = result.get("origin_path") or {}
            for node in origin.get("nodes", []):
                origin_nodes[int(node["node_id"])] = node
            for edge in origin.get("edges", []):
                origin_edges[int(edge["edge_id"])] = edge

        return {
            "nodes": [nodes[key] for key in sorted(nodes)],
            "edges": [edges[key] for key in sorted(edges)],
            "origin_path": {
                "nodes": [origin_nodes[key] for key in sorted(origin_nodes)],
                "edges": [origin_edges[key] for key in sorted(origin_edges)],
            },
        }
