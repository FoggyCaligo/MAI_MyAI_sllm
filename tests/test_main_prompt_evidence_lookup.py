"""Semantic contracts for consolidated main-agent prompt."""
from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_evidence_and_uncertainty():
    assert "Distinguish user-provided facts, observed evidence" in AGENT_SYSTEM_PROMPT
    assert "Verify unsupported factual claims" in AGENT_SYSTEM_PROMPT


def test_main_prompt_omits_retired_tool_names_and_tool_specific_descriptions():
    assert "file_list" not in AGENT_SYSTEM_PROMPT
    assert "document_read" not in AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_complete_answers_without_brevity_directives():
    assert "Answer the current request completely" in AGENT_SYSTEM_PROMPT
    assert "Report material failures and uncertainty" in AGENT_SYSTEM_PROMPT
    assert "Keep final answers concise" not in AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_workspace_and_scratch_cleanup():
    assert 'switch_workspace(target="client")' in AGENT_SYSTEM_PROMPT
    assert "after memory postprocessing" in AGENT_SYSTEM_PROMPT
