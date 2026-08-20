from __future__ import annotations

import pytest

from MK5.app import server


class RaisingFormRequest:
    async def form(self):
        raise AssertionError("The `python-multipart` library must be installed to use form parsing.")


@pytest.mark.asyncio
async def test_upload_returns_json_when_multipart_parser_is_missing() -> None:
    response = await server.upload_file(RaisingFormRequest())

    assert response.status_code == 500
    assert b"missing_dependency" in response.body
    assert b"python-multipart" in response.body
