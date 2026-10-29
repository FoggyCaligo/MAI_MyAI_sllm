"""Web-search diagnostics retain the original provider failure contract."""
from __future__ import annotations

import logging

import pytest

from mai.tools import web as web_module


class FakeDDGS:
    def __init__(self, *, timeout: int):
        assert timeout == 10

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text(self, query: str, *, page: int, **kwargs):
        assert query == "test news"
        if page == 2:
            raise TimeoutError("provider request timed out")
        return [{"href": "https://example.com/first", "title": "First", "body": "From first page"}]


def test_web_search_fails_with_original_cause_and_page_diagnostics(monkeypatch, caplog):
    monkeypatch.setattr(web_module, "DDGS", FakeDDGS)
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")

    failure = caught.value
    assert failure.query == "test news"
    assert failure.provider == "ddgs"
    assert failure.page == 2
    assert failure.completed_pages == 1
    assert failure.cause_type == "TimeoutError"
    assert failure.cause_message == "provider request timed out"
    assert isinstance(failure.__cause__, TimeoutError)
    assert "provider request timed out" in str(failure)
    assert "page=2" in str(failure)
    assert "page=1" in caplog.text
    assert "completed_pages=1" in caplog.text


def test_web_search_provider_initialization_failure_has_no_page(monkeypatch):
    class BrokenDDGS:
        def __init__(self, *, timeout: int):
            raise ConnectionError("could not initialize")

    monkeypatch.setattr(web_module, "DDGS", BrokenDDGS)
    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")
    failure = caught.value
    assert failure.page is None
    assert failure.completed_pages == 0
    assert failure.cause_type == "ConnectionError"
    assert isinstance(failure.__cause__, ConnectionError)


def test_web_search_success_retains_ranked_results(monkeypatch):
    class SuccessfulDDGS(FakeDDGS):
        def text(self, query: str, *, page: int, **kwargs):
            return [{"href": f"https://example.com/{page}", "title": f"Page {page}", "body": "News"}]

    monkeypatch.setattr(web_module, "DDGS", SuccessfulDDGS)
    result = web_module.web_search("test news")
    assert result["provider_pages"] == 3
    assert [item["rank"] for item in result["results"]] == [1, 2, 3]
