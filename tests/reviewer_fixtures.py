"""Test fixture: factual reviewers return the current claim-only response schema."""
from __future__ import annotations

from typing import Any


def reviewer_payload(review: dict[str, Any], request: Any) -> dict[str, Any]:
    if "evidence_verdict" not in review:
        return review
    assert "unit_reviews" not in review
    assert all("evidence_refs" not in claim for claim in review.get("claims", ()))
    return review
