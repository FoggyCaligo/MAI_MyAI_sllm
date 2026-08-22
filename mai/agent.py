from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from uuid import uuid4

from .model import ModelContractError, StructuredModel
from .progress import phase, tool_completed, tool_started, turn_completed, turn_failed, turn_started
from .tool_routes import ToolRouteRegistry, tool_route_schema


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
    work_kind: str

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
    work_kind: str = "action"

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
        "required": ["action", "outcome", "content"],
        "properties": {
            "action": {"const": "answer"},
            "outcome": {"type": "string", "enum": ["completed", "blocked"]},
            "content": {"type": "string", "minLength": 1},
        },
    }


def _combined_schema(variants: list[dict[str, Any]]) -> dict[str, Any]:
    if not variants:
        raise RuntimeError("agent has no available action schema")
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


def _tool_kind(tool: WorkTool) -> str:
    explicit = getattr(tool, "work_kind", None)
    if explicit is not None:
        kind = str(explicit)
        if kind not in {"inspection", "action"}:
            raise ValueError(f"work tool {tool.name} has invalid work_kind: {kind}")
        return kind
    if callable(getattr(tool, "progress_keys", None)):
        return "inspection"
    raise ValueError(f"work tool {tool.name} must declare work_kind")


def _validate_work_tool_contracts(tools: dict[str, WorkTool]) -> None:
    for tool in tools.values():
        kind = _tool_kind(tool)
        if kind == "inspection" and not callable(getattr(tool, "progress_keys", None)):
            raise ValueError(f"inspection work tool {tool.name} must implement progress_keys")


def _progress_keys(tool: WorkTool, result: Any) -> set[str] | None:
    if _tool_kind(tool) != "inspection":
        return None
    extractor = getattr(tool, "progress_keys", None)
    if not callable(extractor):
        raise ValueError(f"inspection work tool {tool.name} must implement progress_keys")
    keys = extractor(result)
    if keys is None:
        raise ValueError(f"inspection work tool {tool.name} progress_keys must return a collection")
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


def _working_root(tool: WorkTool, result: Any) -> str | None:
    extractor = getattr(tool, "working_root", None)
    if not callable(extractor):
        return None
    value = extractor(result)
    if value is None:
        return None
    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(root)
    return str(root)


def _schema_for_context(tool: WorkTool, context: WorkContext) -> dict[str, Any] | None:
    builder = getattr(tool, "schema_for_paths", None)
    if callable(builder):
        return builder(set(context.path_provenance.paths))
    return tool.schema()


@dataclass(slots=True)
class AgentLifecycle:
    """Memory-free Agent baseline.

    This class intentionally contains no graph recall/generate/fix behavior.
    The memory subsystem is layered onto this external-tool/answer loop by a
    separate component after the baseline has been stabilized.

    The legacy constructor fields are temporarily accepted so runtime assembly
    can be migrated without reintroducing their behavior.
    """

    repository: Any
    model: StructuredModel
    discovery: Any | None = None
    recall: Any | None = None
    memory_executor: Any | None = None
    work_tools: list[WorkTool] = field(default_factory=list)
    source_store: Any | None = None

    def run(
        self,
        *,
        user_id: str,
        user_text: str,
        turn_id: str | None = None,
        attachment_paths: Iterable[str | Path] = (),
        discovered_paths: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        clean_user = str(user_text).strip()
        if not clean_user:
            raise ValueError("user_text must be non-empty")

        resolved_turn_id = str(turn_id or uuid4())
        path_provenance = PathProvenance()
        path_provenance.add_many(attachment_paths)
        path_provenance.add_many(discovered_paths)
        turn_started(resolved_turn_id)

        try:
            with phase(resolved_turn_id, "agent"):
                answer, work_events = self._run_agent_phase(
                    context=WorkContext(
                        user_id=user_id,
                        turn_id=resolved_turn_id,
                        user_text=clean_user,
                        path_provenance=path_provenance,
                    )
                )
            result = {
                "status": "completed",
                "turn_id": resolved_turn_id,
                "answer": answer,
                "work_events": work_events,
            }
        except Exception:
            turn_failed(resolved_turn_id)
            raise

        turn_completed(resolved_turn_id)
        return result

    def _run_agent_phase(self, *, context: WorkContext) -> tuple[str, list[dict[str, Any]]]:
        tools = {tool.name: tool for tool in self.work_tools}
        if len(tools) != len(self.work_tools):
            raise ValueError("work tool names must be unique")
        if "tool_route" in tools:
            raise ValueError("work tools may not shadow built-in tool_route")
        _validate_work_tool_contracts(tools)

        routes = ToolRouteRegistry.for_tools(tools.values())
        available_tools = set(tools)
        activated_tools: set[str] = set()
        seen_progress: dict[str, set[str]] = {}
        top_routes = routes.top_level(available_tools=available_tools)

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Operate as one agent loop using exactly one structured action per round. "
                    "User-facing conversational output must only be delivered through the answer action. "
                    "External tools are lazily discovered through exact registered tool_route paths. "
                    f"Initial namespaces are {top_routes}. A leaf exposes /manual and /use. "
                    "If you already know a leaf route, request its exact /use path directly. "
                    "Invalid routes are reported as errors and are never guessed or autocorrected. "
                    "Existing-file actions may only use paths established by current-turn attachments or "
                    "file/code discovery/create results."
                ),
            },
            {"role": "user", "content": context.user_text},
        ]
        events: list[dict[str, Any]] = []

        while True:
            variants = [_answer_schema()]
            if tools:
                variants.append(tool_route_schema())

            exposed_tools: set[str] = set()
            for name in sorted(activated_tools):
                if name not in available_tools:
                    continue
                tool = tools[name]
                schema = _schema_for_context(tool, context)
                if schema is None:
                    continue
                variants.append(schema)
                exposed_tools.add(name)

            action = self.model.structured(messages=messages, schema=_combined_schema(variants))

            if action.get("action") == "answer":
                content = str(action.get("content", "")).strip()
                if not content:
                    raise ModelContractError("answer content must be non-empty")
                return content, events

            if action.get("action") != "tool" or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("agent phase requires one tool action or one answer")

            tool_name = action.get("tool")
            if not isinstance(tool_name, str):
                raise ModelContractError("tool name must be a string")
            arguments = action["arguments"]

            tool_started(tool_name)
            event_metadata: dict[str, Any] = {}

            if tool_name == "tool_route":
                path = str(arguments.get("path", ""))
                resolved = routes.resolve(path=path, available_tools=available_tools)
                if resolved.get("status") == "leaf_action":
                    requested = str(resolved["tool"])
                    operation = str(resolved["operation"])
                    tool = tools[requested]
                    schema = _schema_for_context(tool, context)
                    if schema is None:
                        result = {
                            "status": "error",
                            "reason": "tool_unavailable_in_current_scope",
                            "path": path,
                            "tool": requested,
                        }
                    else:
                        activated_tools.add(requested)
                        result = {
                            "status": "activated",
                            "path": path,
                            "tool": requested,
                            "operation": operation,
                        }
                        if operation == "manual":
                            result.update(
                                {
                                    "description": tool.description,
                                    "input_schema": schema["properties"]["arguments"],
                                }
                            )
                else:
                    result = resolved

            elif tool_name in tools:
                if tool_name not in activated_tools:
                    route = routes.route_for_tool(tool_name)
                    raise ModelContractError(
                        f"{tool_name} requires activation through {route.path}/manual or {route.path}/use"
                    )
                if tool_name not in exposed_tools:
                    raise ModelContractError(f"{tool_name} is unavailable in the current work scope")

                tool = tools[tool_name]
                for required_path in _required_paths(tool, arguments):
                    context.path_provenance.require(required_path)
                result = tool.execute(arguments=arguments, context=context)
                context.path_provenance.add_many(_discovered_paths(tool, result))
                context.path_provenance.remove_many(_removed_paths(tool, result))

                root = _working_root(tool, result)
                if root is not None:
                    event_metadata["working_root"] = root

                keys = _progress_keys(tool, result)
                if keys is not None:
                    prior = seen_progress.setdefault(tool_name, set())
                    new_keys = keys - prior
                    prior.update(keys)
                    if not new_keys:
                        available_tools.discard(tool_name)
                        activated_tools.discard(tool_name)
            else:
                raise ModelContractError("unexpected tool in agent phase")

            tool_completed(tool_name)
            event = {"tool": tool_name, "arguments": arguments, "result": result, **event_metadata}
            events.append(event)
            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "tool", "content": str(event)})
