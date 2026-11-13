"""Model-load polling tolerates transient transport failures, not invalid router states."""
import asyncio

import httpx
import pytest

from mai.llm.llamacpp_router import RouterModelTransitionError, SingleResidentRouter


def test_load_poll_recovers_from_read_error_without_reloading():
    calls = []
    poll_count = 0

    def respond(request):
        nonlocal poll_count
        calls.append(request.url.path)
        if request.url.path == "/models/load":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/models":
            poll_count += 1
            if poll_count == 1:
                raise httpx.ReadError("temporary connection interruption")
            return httpx.Response(200, json={"data": [
                {"id": "main", "status": {"value": "loaded"}}
            ]})
        raise AssertionError("Unexpected router request")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://router", poll_interval_seconds=0.001)
            await router._confirm_load(client, "main")

    asyncio.run(run())
    assert calls == ["/models/load", "/models", "/models"]


def test_load_poll_transport_error_expires_with_cause_and_no_duplicate_load():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/models/load":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/models":
            raise httpx.ReadError("persistent connection interruption")
        raise AssertionError("Unexpected router request")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter(
                "http://router", poll_interval_seconds=0.001, load_timeout_seconds=0.02
            )
            await router._confirm_load(client, "main")

    with pytest.raises(RouterModelTransitionError, match="polling timed out") as error:
        asyncio.run(run())
    assert isinstance(error.value.__cause__, httpx.ReadError)
    assert "ReadError" in str(error.value)
    assert calls.count("/models/load") == 1


@pytest.mark.parametrize("status_code", [400, 503])
def test_load_poll_http_failure_does_not_retry(status_code):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/models/load":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/models":
            return httpx.Response(status_code)
        raise AssertionError("Unexpected router request")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://router", poll_interval_seconds=0.001)
            await router._confirm_load(client, "main")

    with pytest.raises(RouterModelTransitionError, match=f"http_status={status_code}"):
        asyncio.run(run())
    assert calls == ["/models/load", "/models"]


def test_load_poll_malformed_json_does_not_retry():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/models/load":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/models":
            return httpx.Response(200, text="not-json")
        raise AssertionError("Unexpected router request")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://router", poll_interval_seconds=0.001)
            await router._confirm_load(client, "main")

    with pytest.raises(RouterModelTransitionError, match="Invalid router load-status JSON"):
        asyncio.run(run())
    assert calls == ["/models/load", "/models"]
