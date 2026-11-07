"""Web evidence is compacted only in later model history, never in audit/raw storage."""
import json

from mai.agent.tool_results import ToolResultStore


def test_web_search_stays_complete_on_first_read_and_preserves_urls_in_history():
    store = ToolResultStore(max_inline_chars=8000)
    original = json.dumps({
        "query": "release news",
        "provider": "ddgs",
        "provider_pages": 3,
        "results": [
            {"rank": n, "title": f"Article {n}", "url": f"https://example.org/{n}",
             "snippet": "source excerpt " * 10}
            for n in range(1, 13)
        ],
    }, ensure_ascii=False)
    views = store.web_model_views(original, tool_name="web_search")
    assert views.initial_content == original
    later = json.loads(views.compact_history_content)
    assert len(later["results"]) == 12
    assert later["results"][11]["url"] == "https://example.org/12"
    assert later["results"][0]["rank"] == 1
    assert later["source_excerpt_only"] is True
    assert store._results[later["full_original_result_id"]].content == original


def test_web_fetch_keeps_full_text_readable_and_later_excerpt_labeled():
    store = ToolResultStore(max_inline_chars=8000)
    body = "SOURCE-BEGIN " + "X" * 14500 + " SOURCE-END"
    original = json.dumps({
        "url": "https://example.org/article", "final_url": "https://example.org/article",
        "title": "Official article", "status_code": 200, "content_type": "text/html",
        "text": body, "truncated": False,
        "links": [{"text": "Source", "url": "https://example.org/source"}],
        "pagination": {"next": [], "prev": [], "navigation": []},
    }, ensure_ascii=False)
    views = store.web_model_views(original, tool_name="web_fetch")
    later = json.loads(views.compact_history_content)
    assert "SOURCE-BEGIN" in later["source_text_excerpt"]
    assert "SOURCE-END" not in later["source_text_excerpt"]
    assert later["excerpt_incomplete"] is True
    assert later["final_url"] == "https://example.org/article"
    assert "SOURCE-END" not in views.initial_content
    stored_id = later["full_original_result_id"]
    assert store._results[stored_id].content == original
    # The model can request the exact omitted portion via tool_result_read.
    tail_offset = original.find("SOURCE-END")
    tail = store.read(result_id=stored_id, offset=tail_offset, limit=100)
    assert "SOURCE-END" in tail


def test_other_tool_outputs_keep_previous_compaction_behavior():
    store = ToolResultStore(max_inline_chars=8000)
    original = '{"value": 42}'
    views = store.web_model_views(original, tool_name="calculator")
    assert views.initial_content == original
    assert views.compact_history_content is None


def test_unrecognized_web_payload_does_not_claim_valid_source_extract():
    store = ToolResultStore(max_inline_chars=8000)
    original = "{garbled"
    views = store.web_model_views(original, tool_name="web_fetch")
    assert views.initial_content == original
    assert views.compact_history_content is None
