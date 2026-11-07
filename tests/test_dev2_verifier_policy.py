"""Regression coverage for critical-only final verification and toggle wiring."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mai.agent.verification import (
    ClaimReview, FinalGroundingVerifier, FinalReview,
)
from mai.app.settings import load_settings
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ModelConfig
from mai.llm.role_cache_runtime import RoleCacheRun


def run(coro):
    return asyncio.run(coro)


def verifier_with_review(monkeypatch, review, mode="critical"):
    verifier = FinalGroundingVerifier(
        reviewer_adapter=object(), blocking_mode=mode, quality_review_enabled=False,
    )
    async def fake_review(**kwargs):
        return review
    monkeypatch.setattr(verifier, "_review_final", fake_review)
    monkeypatch.setattr(verifier, "_unsupported_numeric_facts", lambda **kwargs: ())
    return verifier


def test_missing_evidence_is_warning_in_critical_mode(monkeypatch):
    review = FinalReview(
        evidence_verdict="unsupported", alignment_verdict="aligned",
        claims=(ClaimReview("Not sourced", "unsupported", "missing_evidence", "No tool source"),),
    )
    result = run(verifier_with_review(monkeypatch, review).verify(
        candidate="Not sourced", messages=[], tool_results=[],
    ))
    assert result.ok
    assert not result.issues
    assert result.review_details["nonblocking_warnings"][0]["code"] == "claim_grounding_failed"


def test_explicit_contradiction_blocks_in_critical_mode(monkeypatch):
    review = FinalReview(
        evidence_verdict="unsupported", alignment_verdict="aligned",
        claims=(ClaimReview("A is true", "unsupported", "contradiction", "Tool says false"),),
    )
    result = run(verifier_with_review(monkeypatch, review).verify(
        candidate="A is true", messages=[], tool_results=[],
    ))
    assert not result.ok
    assert any(issue.code == "claim_grounding_failed" for issue in result.issues)


@pytest.mark.parametrize("action,blocking", [
    ("contradicted", True), ("unverified", True), ("verified", False), ("not_applicable", False),
])
def test_false_completion_blocks(monkeypatch, action, blocking):
    review = FinalReview(
        evidence_verdict="supported", alignment_verdict="aligned", action_verdict=action,
    )
    result = run(verifier_with_review(monkeypatch, review).verify(
        candidate="Action completed", messages=[], tool_results=[],
    ))
    assert (not result.ok) is blocking


def test_strict_mode_keeps_existing_missing_evidence_rejection(monkeypatch):
    review = FinalReview(
        evidence_verdict="unsupported", alignment_verdict="aligned",
        claims=(ClaimReview("Not sourced", "unsupported", "missing_evidence", "No tool source"),),
    )
    result = run(verifier_with_review(monkeypatch, review, mode="strict").verify(
        candidate="Not sourced", messages=[], tool_results=[],
    ))
    assert not result.ok


def test_verifier_settings_are_boolean_and_env_precedence(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("MAI_VERIFIER_ENABLED: false\nMAI_VERIFIER_BLOCKING_MODE: critical\n", encoding="utf-8")
    env = {"MAI_VERIFIER_ENABLED": "true"}
    assert load_settings(path, environ=env)["MAI_VERIFIER_ENABLED"] == "false"
    assert env["MAI_VERIFIER_ENABLED"] == "true"
    assert env["MAI_VERIFIER_BLOCKING_MODE"] == "critical"


def test_cache_namespaces_differ_by_model_identity(tmp_path):
    adapter = LlamaCppAdapter(ModelConfig(model="mai-model"), slot_id=0)
    a = RoleCacheRun(adapter=adapter, cache_directory=tmp_path, model_identity="ornith:sha1")
    c = RoleCacheRun(adapter=adapter, cache_directory=tmp_path, model_identity="gemma:sha2")
    assert a.model_cache_key != c.model_cache_key
    assert a.manager.run_id.startswith(a.model_cache_key)
    assert c.manager.run_id.startswith(c.model_cache_key)


def test_verifier_disabled_branch_skips_role_cache_runtime():
    code = (Path(__file__).resolve().parents[1] / "mai/app/runtime.py").read_text(encoding="utf-8")
    assert "if self.verifier_enabled:" in code
    assert "if role_cache_run is None:" in code
    assert "if self.verifier_enabled else None" in code
