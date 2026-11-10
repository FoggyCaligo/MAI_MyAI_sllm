"""Web-search diagnostics retain the original provider failure contract."""
from __future__ import annotations

import logging

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from mai.tools import web as web_module


class FakeDDGS:
    def __init__(self, *, timeout: int):
        assert timeout == 60

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
    assert result["stopped_on_empty_page"] is None


@pytest.mark.parametrize("empty_page", [2, 3])
@pytest.mark.parametrize("raises", [False, True])
def test_later_empty_page_returns_existing_results_and_stops(monkeypatch, caplog, empty_page, raises):
    calls = []

    class EmptyLaterDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            calls.append(page)
            if page == empty_page:
                if raises:
                    raise DDGSException("No results found.")
                return []
            return [{"href": f"https://example.com/{page}", "title": f"Page {page}", "body": "Observed"}]

    monkeypatch.setattr(web_module, "DDGS", EmptyLaterDDGS)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    result = web_module.web_search("test news")
    assert calls == list(range(1, empty_page + 1))
    assert result["provider_pages"] == empty_page - 1
    assert result["stopped_on_empty_page"] == empty_page
    assert [item["url"] for item in result["results"]] == [
        f"https://example.com/{page}" for page in range(1, empty_page)
    ]
    assert [item["rank"] for item in result["results"]] == list(range(1, empty_page))
    assert "pagination ended" in caplog.text


@pytest.mark.parametrize("error,failure_page", [
    (DDGSException("No results found."), 1),
    *((error, page) for error in [
        DDGSException("No results found. Provider request failed."),
        DDGSException(TimeoutError("No results found.")),
        TimeoutException("No results found."),
        RatelimitException("No results found."),
        ValueError("No results found."),
    ] for page in [1, 2]),
])
def test_first_page_and_non_empty_result_errors_remain_failures(monkeypatch, error, failure_page):
    class BrokenDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            if page == failure_page:
                raise error
            return [{"href": "https://example.com/first", "title": "First", "body": "Observed"}]

    monkeypatch.setattr(web_module, "DDGS", BrokenDDGS)
    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")
    assert caught.value.page == failure_page
    assert caught.value.completed_pages == failure_page - 1
    assert caught.value.__cause__ is error


def test_empty_later_page_without_usable_previous_results_is_not_success(monkeypatch):
    class NoUsableDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            if page == 1:
                return [{"title": "Missing URL", "body": "Cannot form a result"}]
            raise DDGSException("No results found.")

    monkeypatch.setattr(web_module, "DDGS", NoUsableDDGS)
    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")
    assert caught.value.page == 2


def test_first_empty_provider_page_is_successful_no_results(monkeypatch):
    calls = []

    class EmptyDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            calls.append(page)
            return []

    monkeypatch.setattr(web_module, "DDGS", EmptyDDGS)
    result = web_module.web_search("test news")
    assert calls == [1]
    assert result["status"] == "no_results"
    assert result["message"] == "결과없음"
    assert result["results"] == []
    assert result["stopped_on_empty_page"] == 1


def test_filtered_out_provider_records_are_no_results(monkeypatch):
    class InvalidRowsDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            return [{"title": "no URL"}]

    monkeypatch.setattr(web_module, "DDGS", InvalidRowsDDGS)
    result = web_module.web_search("test news")
    assert result["results"] == []
    assert result["status"] == "no_results"
    assert result["message"] == "결과없음"

