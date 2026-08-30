from __future__ import annotations

from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PLANNER_SYSTEM_PROMPT
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.tools.images import register_image_tools
from mai.tools.registry import ToolRegistry


def test_image_tool_contract_requires_confirmed_path_and_allows_corrected_retry(tmp_path):
    registry = ToolRegistry()
    register_image_tools(
        registry,
        model="vision-test",
        host="http://127.0.0.1:11434",
        cwd=tmp_path,
    )

    description = registry.get("image_analyze").description
    assert "do not invent placeholder paths" in description
    assert "filesystem search/list tool" in description
    assert "previously produced FileNotFoundError" in description
    assert "corrected evidence" in description


def test_agent_failure_contract_distinguishes_execution_failure_from_task_impossibility():
    assert "that specific execution failed" in AGENT_SYSTEM_PROMPT
    assert "does not by itself prove that the user's task is impossible" in AGENT_SYSTEM_PROMPT
    assert "corrected arguments" in AGENT_SYSTEM_PROMPT
    assert "newly supplied evidence" in AGENT_SYSTEM_PROMPT


def test_preflight_contract_requires_prerequisite_discovery_tool():
    assert "input identifier, path, or target that is not yet established" in TOOL_PLANNER_SYSTEM_PROMPT
    assert "require both the discovery tool and the operation tool" in TOOL_PLANNER_SYSTEM_PROMPT
