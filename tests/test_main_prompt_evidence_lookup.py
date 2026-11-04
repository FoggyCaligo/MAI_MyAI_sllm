"""The main-agent prompt requires tools to ground unsupported factual claims."""
from __future__ import annotations

from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_tool_evidence_when_user_evidence_is_insufficient() -> None:
    assert "Pretrained knowledge supports reasoning, not factual evidence." in AGENT_SYSTEM_PROMPT
    assert "Ground factual claims in user statements or observed tool evidence" in AGENT_SYSTEM_PROMPT
    assert "when neither supports a claim, use an appropriate tool before asserting it" in AGENT_SYSTEM_PROMPT
    assert "If sufficient evidence cannot be obtained, state the uncertainty" in AGENT_SYSTEM_PROMPT


def test_main_prompt_omits_search_deterrents() -> None:
    assert "search further when justified" not in AGENT_SYSTEM_PROMPT
    assert "Do not repeat a lookup when evidence is already sufficient" not in AGENT_SYSTEM_PROMPT
