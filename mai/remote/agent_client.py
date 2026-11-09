"""Cross-platform long-lived HTTPS polling client for MAI remote tools.

The secret comes from the launch environment rather than process argv. A single
worker instance retains browser sessions until disconnect. No fallback to host.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import httpx

from .python_worker import RemoteWorker
from .cache import maintain_remote_worker_cache


class RemoteAgentProtocolError(RuntimeError):
    """Invalid remote-session transport response."""


async def run_agent(*, server: str, session: str, key: str,
                    worker: RemoteWorker | None = None) -> None:
    if not server.lower().startswith("https://"):
        raise ValueError("HTTPS MAI server required")
    if not session or not key:
        raise ValueError("remote session and key are required")
    base = server.rstrip("/")
    url = f"{base}/remote/agent/{session}"
    headers = {"X-MAI-Agent-Secret": key}
    active_worker = worker or RemoteWorker()
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(40.0, connect=15.0),
            follow_redirects=False,
        ) as client:
            while True:
                response = await client.post(url + "/poll")
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or "request" not in body:
                    raise RemoteAgentProtocolError("poll response missing request field")
                command = body["request"]
                if command is None:
                    continue
                if not isinstance(command, dict):
                    raise RemoteAgentProtocolError("poll request must be an object")
                request_id = command.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise RemoteAgentProtocolError("poll request_id is invalid")
                outcome: dict[str, Any] = {"request_id": request_id, "ok": False}
                try:
                    if command.get("operation") != "native.invoke":
                        raise RemoteAgentProtocolError(
                            f"unsupported remote operation: {command.get('operation')!r}"
                        )
                    args = command.get("arguments")
                    if not isinstance(args, dict):
                        raise RemoteAgentProtocolError("native.invoke arguments must be an object")
                    value = await active_worker.execute_native(
                        args.get("name"), args.get("arguments"),
                    )
                    # Serialization is part of the tool result contract. If it
                    # fails, report a failed call instead of disconnecting here.
                    try:
                        json.dumps({"value": value}, ensure_ascii=False, allow_nan=False)
                    except (TypeError, ValueError) as exc:
                        raise RemoteAgentProtocolError(
                            "remote tool result is not JSON serializable"
                        ) from exc
                    outcome.update({"ok": True, "result": {"value": value}})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    outcome.update({
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
                delivered = await client.post(url + "/result", json=outcome)
                delivered.raise_for_status()
    finally:
        try:
            await active_worker.close()
        finally:
            # A network failure must not mask the primary disconnect error.
            try:
                async with httpx.AsyncClient(headers=headers, timeout=5) as client:
                    await client.delete(url)
            except httpx.HTTPError as exc:
                print(f"MAI remote session revoke failed: {exc}", file=sys.stderr)


def main() -> int:
    try:
        from pathlib import Path

        with maintain_remote_worker_cache(
            Path(os.environ["MAI_REMOTE_CACHE_ROOT"]),
            os.environ["MAI_REMOTE_CACHE_DIGEST"],
        ):
            asyncio.run(run_agent(
                server=os.environ["MAI_REMOTE_SERVER"],
                session=os.environ["MAI_REMOTE_SESSION"],
                key=os.environ["MAI_REMOTE_KEY"],
            ))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
