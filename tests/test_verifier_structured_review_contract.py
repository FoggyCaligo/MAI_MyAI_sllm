"""Protect the review helper signature from being shadowed by duplicate definitions."""
import ast
from pathlib import Path


def test_structured_review_helper_has_single_definition_and_labels():
    tree = ast.parse(Path("mai/agent/verification.py").read_text(encoding="utf-8"))
    verifier = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FinalGroundingVerifier"
    )
    definitions = [
        node for node in verifier.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_request_structured_review"
    ]
    assert len(definitions) == 1
    calls = [
        node for node in ast.walk(verifier)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_request_structured_review"
    ]
    assert len(calls) >= 4
    assert all(any(keyword.arg == "label" for keyword in call.keywords) for call in calls)
