"""The restored Native memory subsystem owns its Sentence_Breaker dependency."""
from pathlib import Path
import tomllib

from mai.memory.segmenter import SentenceBreakerSegmenter
from mai.memory.tools import MemorySearchInput


ROOT = Path(__file__).resolve().parents[1]


def test_sentence_breaker_is_a_direct_mai_dependency_not_an_mcp_extra():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = config["project"]["dependencies"]
    assert any(entry.startswith("sentence-breaker @ git+https://github.com/FoggyCaligo/Sentence_Breaker.git@")
               for entry in dependencies)
    assert not any("mcp-memorizer" in entry.lower() for entry in dependencies)
    assert SentenceBreakerSegmenter.__module__ == "mai.memory.segmenter"


def test_portable_setup_validates_restored_sentence_breaker_imports():
    linux = (ROOT / "mai.sh").read_text(encoding="utf-8")
    windows = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    for script in (linux, windows):
        assert "from sentence_breaker import LanguageGraph" in script
        assert "from mai.memory.segmenter import SentenceBreakerSegmenter" in script


def test_native_search_schema_explains_boolean_and_legacy_keyword():
    schema = MemorySearchInput.model_json_schema()
    assert schema["additionalProperties"] is False
    query = schema["properties"]["query"]["description"]
    assert "AND/OR/NOT" in query
    assert "NOT > AND > OR" in query
    assert "토마토" in query
    assert "quoted multiword" in query
    assert "keyword" in schema["properties"]
    assert "recent overview" in schema["properties"]["limit"]["description"]
