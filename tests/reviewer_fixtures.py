"""Construct complete, explicit review-unit records for existing reviewer test doubles.

This helper is test-only: it simulates the extra structured field emitted by
a compliant factual reviewer. Real runtime verdicts must supply this field.
"""
from __future__ import annotations

import json
from typing import Any


def with_claim_units(review: dict[str, Any], request: Any) -> dict[str, Any]:
    if "evidence_verdict" not in review or "unit_reviews" in review:
        return review
    payload = json.loads(request.messages[1]["content"])
    units = payload["candidate_units"]
    claims = review["claims"]
    response = dict(review)
    response["unit_reviews"] = [
        {
            "unit_index": unit["index"],
            "classification": "factual" if claims and index == 0 else "nonfactual",
            "claim_indices": list(range(len(claims))) if claims and index == 0 else [],
        }
        for index, unit in enumerate(units)
    ]
    return response
