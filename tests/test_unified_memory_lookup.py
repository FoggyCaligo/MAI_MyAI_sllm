import pytest
from pydantic import ValidationError

from mai.memory.tools import MemoryLookupInput
from mai.memory.admission import MEMORY_RECALL_TOOL_NAMES


def test_lookup_keyword_with_limit():
    value = MemoryLookupInput.model_validate({"keyword": "사용자의 취미", "limit": 10})
    assert value.keyword == "사용자의 취미"
    assert value.node_id is None
    assert value.limit == 10


def test_lookup_node_id():
    value = MemoryLookupInput.model_validate({"node_id": 584})
    assert value.node_id == 584
    assert value.keyword is None


@pytest.mark.parametrize("payload", [
    {}, {"keyword": "취미", "node_id": 584},
    {"node_id": 584, "limit": 3},
    {"keyword": ""}, {"node_id": -1},
    {"query": "취미"},
])
def test_lookup_rejects_invalid_contract(payload):
    with pytest.raises(ValidationError):
        MemoryLookupInput.model_validate(payload)


def test_unified_lookup_excluded_from_new_fact_evidence():
    assert "memory_lookup" in MEMORY_RECALL_TOOL_NAMES
    assert "memory_recall" not in MEMORY_RECALL_TOOL_NAMES
    assert "memory_search" not in MEMORY_RECALL_TOOL_NAMES
