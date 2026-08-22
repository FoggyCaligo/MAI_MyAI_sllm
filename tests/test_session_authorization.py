from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mai.file_mutation_tools import DownloadGrantStore
from mai.graph import GraphRepository
from mai.web import _next_working_root, build_lifecycle


@dataclass
class DummyModel:
    model: str = "dummy"

    def structured(self, *, messages, schema):
        raise AssertionError("model should not be called in tool-scope construction test")


@dataclass
class DummyImageAnalyzer:
    model: str = "dummy-image"

    def analyze(self, *, path: Path, prompt: str):
        raise AssertionError("image analyzer should not be called in construction test")


def lifecycle_tool_names(tmp_path: Path, *, role: str) -> set[str]:
    repository = GraphRepository(tmp_path / f"{role}.db")
    try:
        lifecycle = build_lifecycle(
            repository=repository,
            model=DummyModel(),
            owner_id="owner",
            terminal_encoding="utf-8",
            download_grants=DownloadGrantStore(),
            image_analyzer=DummyImageAnalyzer(),
            role=role,
            default_root=tmp_path,
        )
        return {tool.name for tool in lifecycle.work_tools}
    finally:
        repository.close()


def test_trial_catalog_excludes_host_mutation_and_file_tools(tmp_path: Path) -> None:
    names = lifecycle_tool_names(tmp_path, role="trial")
    assert names == {"latest_search", "web_research", "market_snapshot", "scratchpad_put"}
    assert "file_create" not in names
    assert "terminal_command" not in names
    assert "code_search" not in names
    assert "document_read" not in names
    assert "image_analyze" not in names


def test_owner_catalog_retains_full_host_capabilities(tmp_path: Path) -> None:
    names = lifecycle_tool_names(tmp_path, role="owner")
    assert {"file_tree", "file_create", "terminal_command", "code_search", "document_read", "image_analyze"} <= names
    assert {"latest_search", "web_research", "market_snapshot", "scratchpad_put"} <= names


def test_structural_working_root_metadata_promotes_session_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    next_root = _next_working_root(
        current_root=str(tmp_path),
        work_events=[
            {
                "tool": "renamed_or_wrapped_discovery_tool",
                "arguments": {},
                "result": {"root": "not interpreted here"},
                "working_root": str(project),
            }
        ],
    )
    assert next_root == str(project.resolve())


def test_unrelated_result_root_cannot_change_session_working_root(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    next_root = _next_working_root(
        current_root=str(tmp_path),
        work_events=[
            {
                "tool": "terminal_command",
                "arguments": {"command": "pwd"},
                "result": {"root": str(other), "returncode": 0},
            }
        ],
    )
    assert next_root == str(tmp_path.resolve())
