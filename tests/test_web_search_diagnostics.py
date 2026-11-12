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
def test_later_empty_page_returns_existing_results_and_stops(monkeypatch, caplog, empty_page):
    calls = []

    class EmptyLaterDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            calls.append(page)
            if page == empty_page:
                return []
            return [{"href": f"https://example.com/{page}", "title": f"Page {page}", "body": "Observed"}]

    monkeypatch.setattr(web_module, "DDGS", EmptyLaterDDGS)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    result = web_module.web_search("test news")
    assert calls == list(range(1, empty_page + 1))
    assert result["provider_pages"] == empty_page - 1
    assert result["stopped_on_empty_page"] == empty_page
    assert result["status"] == "results"
    assert result["suggested_next_action"] is None
    assert [item["url"] for item in result["results"]] == [
        f"https://example.com/{page}" for page in range(1, empty_page)
    ]
    assert "pagination ended" in caplog.text


def test_ambiguous_provider_exception_on_later_page_is_failure(monkeypatch):
    class AmbiguousDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            if page == 2:
                raise DDGSException("No results found.")
            return [{"href": "https://example.com/1", "title": "Result"}]

    monkeypatch.setattr(web_module, "DDGS", AmbiguousDDGS)
    with pytest.raises(web_module.WebSearchError) as raised:
        web_module.web_search("test news")
    assert raised.value.page == 2
    assert raised.value.completed_pages == 1
    assert isinstance(raised.value.__cause__, DDGSException)

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
    assert result["message"] == "이 검색어로 확인된 결과가 없습니다."
    assert result["results"] == []
    assert result["stopped_on_empty_page"] == 1
    assert result["search_scope"] == "requested_query"
    assert result["suggested_next_action"]["type"] == "reformulate_query"
    assert result["suggested_next_action"]["mandatory"] is False
    assert "동의어" in result["suggested_next_action"]["guidance"]


def test_filtered_out_provider_records_are_no_results(monkeypatch):
    class InvalidRowsDDGS(FakeDDGS):
        def text(self, query, *, page, **kwargs):
            return [{"title": "no URL"}]

    monkeypatch.setattr(web_module, "DDGS", InvalidRowsDDGS)
    result = web_module.web_search("test news")
    assert result["results"] == []
    assert result["status"] == "no_results"
    assert result["message"] == "이 검색어로 확인된 결과가 없습니다."



def test_timeout_falls_back_once_to_google_and_reports_backend(monkeypatch, caplog):
    requests = []

    class TimeoutThenGoogle(FakeDDGS):
        def text(self, query, *, page, backend=None, **kwargs):
            requests.append((page, backend))
            if page == 2 and backend is None:
                raise TimeoutException("provider timed out")
            return [{"href": f"https://example.org/{page}",
                     "title": f"Result {page}", "body": "Evidence"}]

    monkeypatch.setattr(web_module, "DDGS", TimeoutThenGoogle)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    result = web_module.web_search("test news")
    assert requests == [(1, None), (2, None), (2, "google"), (3, None)]
    assert result["provider_pages"] == 3
    assert result["fallback_backend"] == "google"
    assert result["fallback_pages"] == [2]
    assert [row["url"] for row in result["results"]] == [
        "https://example.org/1", "https://example.org/2", "https://example.org/3"
    ]
    assert "primary timeout" in caplog.text
    assert "fallback succeeded" in caplog.text


def test_wrapped_structural_timeout_triggers_google(monkeypatch):
    requests = []

    class WrappedTimeout(FakeDDGS):
        def text(self, query, *, page, backend=None, **kwargs):
            requests.append((page, backend))
            if backend is None:
                try:
                    raise TimeoutError("low-level socket timed out")
                except TimeoutError as cause:
                    raise DDGSException("upstream unavailable") from cause
            return [{"href": "https://example.org/google", "title": "Google result"}]

    monkeypatch.setattr(web_module, "DDGS", WrappedTimeout)
    result = web_module.web_search("test news")
    assert requests == [(1, None), (1, "google"), (2, None), (2, "google"),
                        (3, None), (3, "google")]
    assert result["fallback_pages"] == [1, 2, 3]
    assert len(result["results"]) == 1


@pytest.mark.parametrize("original", [
    DDGSException("error sending request > os error 10060"),
    DDGSException("No results found."),
    RatelimitException("search rate limited"),
])
def test_untyped_provider_errors_do_not_trigger_fallback(monkeypatch, original):
    backends = []

    class UntypedFailure(FakeDDGS):
        def text(self, query, *, backend=None, **kwargs):
            backends.append(backend)
            raise original

    monkeypatch.setattr(web_module, "DDGS", UntypedFailure)
    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")
    assert backends == [None]
    assert caught.value.__cause__ is original


def test_google_fallback_failure_propagates_explicitly(monkeypatch):
    requests = []
    fallback_error = DDGSException("Google unavailable")

    class BothFail(FakeDDGS):
        def text(self, query, *, backend=None, **kwargs):
            requests.append(backend)
            if backend is None:
                raise TimeoutException("primary timeout")
            raise fallback_error

    monkeypatch.setattr(web_module, "DDGS", BothFail)
    with pytest.raises(web_module.WebSearchError) as caught:
        web_module.web_search("test news")
    assert requests == [None, "google"]
    assert caught.value.__cause__ is fallback_error
