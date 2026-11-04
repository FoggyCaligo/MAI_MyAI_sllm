"""The main-agent prompt must connect external factual claims to observed evidence."""
from __future__ import annotations

from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_main_prompt_requires_evidence_before_external_factual_claims() -> None:
    assert "Pretrained knowledge supports reasoning, not factual evidence." in AGENT_SYSTEM_PROMPT
    assert "Before asserting an external factual claim without sufficient observed evidence" in AGENT_SYSTEM_PROMPT
    assert "activate and use an appropriate tool to verify it" in AGENT_SYSTEM_PROMPT


def test_main_prompt_avoids_redundant_lookups_and_unsupported_fallbacks() -> None:
    assert "Do not repeat a lookup when evidence is already sufficient" in AGENT_SYSTEM_PROMPT
    assert "if verification is unavailable, state the limitation instead of inventing facts" in AGENT_SYSTEM_PROMPT
