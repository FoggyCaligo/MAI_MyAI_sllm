"""Ensure factual grounding permits faithful cross-language literary rendering."""
from mai.agent.verification import _FACTUAL_REVIEW_SYSTEM, _QUALITY_REVIEW_SYSTEM


def test_factual_reviewer_covers_translation_and_russian_transliteration():
    prompt = _FACTUAL_REVIEW_SYSTEM
    assert "cross-language translation and transliteration as semantic equivalence" in prompt
    assert "Korean rendering of Russian narrative" in prompt
    assert "fiction within the story's fictional world" in prompt
    assert "Reject actual changes to identities, relationships" in prompt


def test_quality_reviewer_does_not_duplicate_transliteration_grounding():
    assert "Korean rendering of Russian narrative" not in _QUALITY_REVIEW_SYSTEM
