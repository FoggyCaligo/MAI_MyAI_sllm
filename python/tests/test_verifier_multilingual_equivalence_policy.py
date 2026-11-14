"""Cross-language reference equivalence is a semantic reviewer responsibility."""
from mai.agent.verification import _FACTUAL_REVIEW_SYSTEM, _QUALITY_REVIEW_SYSTEM


def test_factual_grounding_defines_language_independent_entity_equivalence():
    assert "Across any languages" in _FACTUAL_REVIEW_SYSTEM
    assert "nouns, proper names, and transliterations" in _FACTUAL_REVIEW_SYSTEM
    assert "same entity or meaning" in _FACTUAL_REVIEW_SYSTEM
    assert "different scripts or spellings alone are not grounding defects" in _FACTUAL_REVIEW_SYSTEM


def test_equivalence_policy_does_not_override_source_fidelity():
    assert "invented details and unsupported counts are not" in _FACTUAL_REVIEW_SYSTEM
    assert "does not support it, or supports only a narrower statement" in _FACTUAL_REVIEW_SYSTEM


def test_equivalence_is_not_answer_quality_responsibility():
    assert "Across any languages" not in _QUALITY_REVIEW_SYSTEM
