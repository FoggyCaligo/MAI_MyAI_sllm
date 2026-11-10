"""Streaming HTTP errors must retain original status and body."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from mai.llm.stream_diagnostics import stream_completion


def test_streamed_http_error_body_is_read_before_status_exception():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(b'{"error":"router model unavailable"}'),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await stream_completion(
                    client,
                    "http://localhost/v1/chat/completions",
                    {"model": "test", "messages": []},
                    timeout_seconds=5.0,
                )
            assert caught.value.response.status_code == 503
            assert caught.value.response.text == '{"error":"router model unavailable"}'
            assert caught.value.response.is_stream_consumed

    asyncio.run(run())
