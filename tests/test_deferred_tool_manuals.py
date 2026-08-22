from __future__ import annotations

from dataclasses import dataclass

from mai.tool_routes import ToolRouteRegistry


@dataclass
class FakeTool:
    name: str


def test_root_catalog_exposes_only_namespaces() -> None:
    registry = ToolRouteRegistry.for_tools(
        [FakeTool("file_tree"), FakeTool("file_read"), FakeTool("web_research")]
    )

    assert registry.top_level(available_tools={"file_tree", "file_read", "web_research"}) == [
        "/file",
        "/web",
    ]


def test_file_namespace_reveals_registered_children_without_full_schemas() -> None:
    registry = ToolRouteRegistry.for_tools(
        [FakeTool("file_tree"), FakeTool("file_read"), FakeTool("web_research")]
    )

    result = registry.resolve(
        path="/file",
        available_tools={"file_tree", "file_read", "web_research"},
    )

    assert result == {
        "status": "namespace",
        "path": "/file",
        "children": ["/file/read", "/file/tree"],
    }


def test_leaf_offers_manual_and_direct_use() -> None:
    registry = ToolRouteRegistry.for_tools([FakeTool("file_tree")])

    leaf = registry.resolve(path="/file/tree", available_tools={"file_tree"})
    manual = registry.resolve(path="/file/tree/manual", available_tools={"file_tree"})
    direct = registry.resolve(path="/file/tree/use", available_tools={"file_tree"})

    assert leaf["children"] == ["/file/tree/manual", "/file/tree/use"]
    assert manual["status"] == "leaf_action"
    assert manual["operation"] == "manual"
    assert manual["tool"] == "file_tree"
    assert direct["status"] == "leaf_action"
    assert direct["operation"] == "use"
    assert direct["tool"] == "file_tree"


def test_invalid_path_is_visible_and_never_fuzzy_corrected() -> None:
    registry = ToolRouteRegistry.for_tools([FakeTool("file_tree")])

    result = registry.resolve(path="/file/tre/use", available_tools={"file_tree"})

    assert result["status"] == "error"
    assert result["reason"] == "unknown_tool_path"
    assert result["requested_path"] == "/file/tre/use"
    assert result["available"] == ["/file"]


def test_extension_tool_route_is_structural_not_semantic_guessing() -> None:
    registry = ToolRouteRegistry.for_tools([FakeTool("custom_operation")])

    result = registry.resolve(
        path="/file/extension/custom_operation/use",
        available_tools={"custom_operation"},
    )

    assert result["status"] == "leaf_action"
    assert result["tool"] == "custom_operation"
