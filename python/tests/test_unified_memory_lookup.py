import pytest
from pydantic import ValidationError

from mai.memory.tools import MemorySearchInput
from mai.memory.admission import MEMORY_RECALL_TOOL_NAMES


def test_lookup_query_with_limit():
    value = MemorySearchInput.model_validate({"query": "사용자의 취미", "limit": 10})
    assert value.query == "사용자의 취미"
    assert value.node_id is None
    assert value.limit == 10


def test_lookup_node_id():
    value = MemorySearchInput.model_validate({"node_id": 584})
    assert value.node_id == 584
    assert value.query is None


@pytest.mark.parametrize("payload", [
    {}, {"query": "취미", "node_id": 584},
    {"limit": 51},
    {"node_id": 584, "limit": 3},
    {"query": ""}, {"node_id": -1},
    {"keyword": "취미", "query": "other"},
])
def test_lookup_rejects_invalid_contract(payload):
    with pytest.raises(ValidationError):
        MemorySearchInput.model_validate(payload)


def test_overview_requires_bounded_limit():
    value = MemorySearchInput.model_validate({"limit": 8})
    assert value.node_id is None
    assert value.query is None
    assert value.limit == 8


def test_unified_lookup_excluded_from_new_fact_evidence():
    assert "memory_search" in MEMORY_RECALL_TOOL_NAMES
    assert "memory_recall" not in MEMORY_RECALL_TOOL_NAMES
