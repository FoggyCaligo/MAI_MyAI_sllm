from pathlib import Path
import pytest

from mai.tools.tmp_cleanup import clean_tmp_contents
from mai.mcp_tools.native_groups import GROUPS, build_registry
from mai.tools.local import register_local_pc_tools
from mai.tools.registry import ToolRegistry


RETIRED = {"file_list", "file_search", "file_delete", "file_move", "file_copy",
           "code_read", "code_search", "calculator", "current_time", "csv_aggregate"}


def test_retired_tools_absent_from_functional_groups(tmp_path):
    assert "mai_utilities" not in GROUPS
    names = set()
    for group in GROUPS:
        registry, _ = build_registry(group, cwd=tmp_path)
        names.update(registry.names())
    assert not RETIRED.intersection(names)
    assert {"document_read", "file_write", "code_edit", "code_symbols",
            "document_edit", "terminal_run"}.issubset(names)


def test_retired_tools_absent_from_native_registry(tmp_path):
    registry = ToolRegistry()
    register_local_pc_tools(registry, cwd=tmp_path)
    assert not RETIRED.intersection(registry.names())
    assert registry.has("terminal_run")
    assert registry.has("document_read")


def test_tmp_cleanup_keeps_root_and_removes_nested_contents(tmp_path):
    root = tmp_path / "tmp"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "code.py").write_text("print(1)")
    (root / "out.json").write_text("{}")
    clean_tmp_contents(root)
    assert root.is_dir()
    assert list(root.iterdir()) == []
    clean_tmp_contents(root)
    assert root.is_dir()


def test_tmp_cleanup_does_not_follow_root_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("keep")
    root = tmp_path / "tmp"
    try:
        root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        clean_tmp_contents(root)
    assert (outside / "important.txt").read_text() == "keep"

def test_server_startup_has_no_mcp_discovery():
    from pathlib import Path
    from mai.app import server
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "ensure_default_mcp_config(" not in source
    assert "MCPConnectionManager(" not in source
    assert 'os.environ["MAI_MCP_CONFIG"]' not in source
