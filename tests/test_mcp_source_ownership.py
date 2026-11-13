"""Source ownership checks for external memory and shared MCP discovery."""
from pathlib import Path

import mai.mcp_tools.mcp_servers as servers
import mai.mcp_tools.tool_manual as manual
from mai.agent.loop import ToolManualInput, _tool_manual_schema, _insert_tool_catalog
from mai.mcp_tools.MCP_memorizer import server as memory_launcher
from mcp_memorizer.extraction.prompt import _FACT_EXTRACTION_SYSTEM
from mcp_memorizer.policy import MEMORY_MODEL_CONTEXT

ROOT = Path(__file__).resolve().parents[1]

def test_mcp_sources_are_only_in_common_package():
    assert servers.__file__.endswith("mai/mcp_tools/mcp_servers.py") or servers.__file__.endswith("mai\\mcp_tools\\mcp_servers.py")
    assert manual.__file__.endswith("mai/mcp_tools/tool_manual.py") or manual.__file__.endswith("mai\\mcp_tools\\tool_manual.py")
    assert ToolManualInput is manual.ToolManualInput
    assert _tool_manual_schema is manual._tool_manual_schema
    assert _insert_tool_catalog is manual._insert_tool_catalog
    assert not (ROOT / "mai" / "memory").exists()
    assert not (ROOT / "mai" / "tools" / "mcp_connection.py").exists()
    assert not (ROOT / "mcp_servers.example.json").exists()
    assert (ROOT / "mai" / "mcp_tools" / "mcp_servers.example.json").exists()

def test_memory_prompt_has_one_canonical_owner():
    assert "latest_user_message" in _FACT_EXTRACTION_SYSTEM
    assert "created_at" in MEMORY_MODEL_CONTEXT["instruction"]
    assert callable(memory_launcher.main)
