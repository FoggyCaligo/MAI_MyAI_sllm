from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ToolRoute:
    segments: tuple[str, ...]
    tool_name: str
    source_kind: str

    @property
    def path(self) -> str:
        return "/" + "/".join(self.segments)


# These are protocol registrations, not semantic inference rules. Runtime routing
# only accepts exact registered paths and never guesses from substrings.
_REGISTERED_ROUTES: tuple[ToolRoute, ...] = (
    ToolRoute(("file", "tree"), "file_tree", "file_evidence"),
    ToolRoute(("file", "search"), "file_search", "file_evidence"),
    ToolRoute(("file", "text-search"), "file_text_search", "file_evidence"),
    ToolRoute(("file", "read"), "file_read", "file_evidence"),
    ToolRoute(("file", "create"), "file_create", "file_evidence"),
    ToolRoute(("file", "update"), "file_update", "file_evidence"),
    ToolRoute(("file", "delete"), "file_delete", "file_evidence"),
    ToolRoute(("file", "download"), "file_download_link", "file_evidence"),
    ToolRoute(("file", "document"), "document_read", "file_evidence"),
    ToolRoute(("file", "image"), "image_analyze", "file_evidence"),
    ToolRoute(("file", "terminal"), "terminal_command", "tool_operation"),
    ToolRoute(("file", "code", "index"), "code_index", "file_evidence"),
    ToolRoute(("file", "code", "search"), "code_search", "file_evidence"),
    ToolRoute(("web", "search"), "web_research", "web_evidence"),
    ToolRoute(("web", "market"), "market_snapshot", "web_evidence"),
    ToolRoute(("web", "current"), "latest_search", "web_evidence"),
)


def tool_route_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "tool_route"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {"path": {"type": "string", "minLength": 1}},
            },
        },
    }


@dataclass(slots=True)
class ToolRouteRegistry:
    routes_by_tool: dict[str, ToolRoute]
    routes_by_path: dict[str, ToolRoute]

    @classmethod
    def for_tools(cls, tools: Iterable[Any]) -> "ToolRouteRegistry":
        names = {str(tool.name) for tool in tools}
        registered = {route.tool_name: route for route in _REGISTERED_ROUTES if route.tool_name in names}

        # Custom/injected tools used by tests or extensions still receive a structural
        # route, without inferring semantics from their names.
        for name in sorted(names - set(registered)):
            registered[name] = ToolRoute(("file", "extension", name), name, "tool_operation")

        by_path = {route.path: route for route in registered.values()}
        if len(by_path) != len(registered):
            raise ValueError("tool routes must be unique")
        return cls(routes_by_tool=registered, routes_by_path=by_path)

    def route_for_tool(self, tool_name: str) -> ToolRoute:
        try:
            return self.routes_by_tool[str(tool_name)]
        except KeyError as exc:
            raise KeyError(f"tool has no registered route: {tool_name}") from exc

    def top_level(self, *, available_tools: set[str]) -> list[str]:
        roots = {
            route.segments[0]
            for name, route in self.routes_by_tool.items()
            if name in available_tools and route.segments
        }
        return [f"/{root}" for root in sorted(roots)]

    def _namespace_children(self, prefix: tuple[str, ...], *, available_tools: set[str]) -> list[str]:
        children: set[str] = set()
        for name, route in self.routes_by_tool.items():
            if name not in available_tools:
                continue
            segments = route.segments
            if len(segments) <= len(prefix) or segments[: len(prefix)] != prefix:
                continue
            child = segments[: len(prefix) + 1]
            children.add("/" + "/".join(child))
        return sorted(children)

    @staticmethod
    def _parse(path: str) -> tuple[str, ...] | None:
        value = str(path)
        if not value.startswith("/") or value.endswith("/"):
            return None
        segments = tuple(value[1:].split("/"))
        if not segments or any(not segment for segment in segments):
            return None
        return segments

    def resolve(self, *, path: str, available_tools: set[str]) -> dict[str, Any]:
        segments = self._parse(path)
        if segments is None:
            return {
                "status": "error",
                "reason": "unknown_tool_path",
                "requested_path": str(path),
                "available": self.top_level(available_tools=available_tools),
            }

        if segments[-1] in {"manual", "use"}:
            leaf_path = "/" + "/".join(segments[:-1])
            route = self.routes_by_path.get(leaf_path)
            if route is None or route.tool_name not in available_tools:
                return {
                    "status": "error",
                    "reason": "unknown_tool_path",
                    "requested_path": str(path),
                    "available": self.top_level(available_tools=available_tools),
                }
            return {
                "status": "leaf_action",
                "operation": segments[-1],
                "path": leaf_path,
                "tool": route.tool_name,
                "source_kind": route.source_kind,
            }

        exact = self.routes_by_path.get("/" + "/".join(segments))
        if exact is not None and exact.tool_name in available_tools:
            return {
                "status": "leaf",
                "path": exact.path,
                "tool": exact.tool_name,
                "children": [f"{exact.path}/manual", f"{exact.path}/use"],
            }

        children = self._namespace_children(segments, available_tools=available_tools)
        if children:
            return {
                "status": "namespace",
                "path": "/" + "/".join(segments),
                "children": children,
            }

        return {
            "status": "error",
            "reason": "unknown_tool_path",
            "requested_path": str(path),
            "available": self.top_level(available_tools=available_tools),
        }
