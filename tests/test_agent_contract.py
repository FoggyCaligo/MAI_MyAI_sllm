from __future__ import annotations

from mai.agent import _memory_schema, _resolve_endpoint
from mai.model import ModelContractError


def test_memory_schema_has_no_done_before_success() -> None:
    terms = [{"term_id": "user:0", "source": "user", "text": "robot"}]
    schema = _memory_schema(terms, allow_done=False)
    assert schema["properties"]["action"]["enum"] == ["tool"]
    endpoint = schema["properties"]["arguments"]["properties"]["object"]
    term_branch = endpoint["oneOf"][1]
    assert term_branch["properties"]["term_id"]["enum"] == ["user:0"]


def test_memory_schema_allows_done_only_after_success() -> None:
    terms = [{"term_id": "user:0", "source": "user", "text": "robot"}]
    schema = _memory_schema(terms, allow_done=True)
    assert len(schema["oneOf"]) == 2
    assert schema["oneOf"][1]["properties"]["action"]["enum"] == ["done"]


def test_term_id_resolves_only_from_current_scope() -> None:
    terms = [{"term_id": "user:0", "source": "user", "text": "robot"}]
    assert _resolve_endpoint({"term_id": "user:0"}, terms, user_id="owner") == "robot"
    try:
        _resolve_endpoint({"term_id": "user:9"}, terms, user_id="owner")
    except ModelContractError as exc:
        assert "outside the current memory scope" in str(exc)
    else:
        raise AssertionError("out-of-scope term_id must fail")
