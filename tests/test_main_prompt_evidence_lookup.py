"""The main-agent prompt requires tools to ground unsupported factual claims."""
from __future__ import annotations

from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_tool_evidence_when_user_evidence_is_insufficient() -> None:
    assert "Pretrained knowledge supports reasoning, not factual evidence." in AGENT_SYSTEM_PROMPT
    assert "distinguish observed user/tool evidence from pretrained recollection" in AGENT_SYSTEM_PROMPT
    assert "verify the claim with the appropriate tool or explicitly leave it unverified" in AGENT_SYSTEM_PROMPT


def test_main_prompt_omits_search_deterrents() -> None:
    assert "search further when justified" not in AGENT_SYSTEM_PROMPT
    assert "Do not repeat a lookup when evidence is already sufficient" not in AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_complete_answers_without_brevity_directives() -> None:
    assert "Answer the current request fully" in AGENT_SYSTEM_PROMPT
    assert "do not assume the user knows prior answers or omit information" in AGENT_SYSTEM_PROMPT
    assert "report material failures and limitations to the user" in AGENT_SYSTEM_PROMPT
    assert "Keep final answers concise and focused on the essential points" not in AGENT_SYSTEM_PROMPT
    assert "Provide more detail when the user explicitly requests it" not in AGENT_SYSTEM_PROMPT
    assert "Brevity never permits omitting material failures" not in AGENT_SYSTEM_PROMPT
