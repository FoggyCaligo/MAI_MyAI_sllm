"""Long-lived transport client for an owner-authorized MAI remote host.

The transport is OS-neutral. Platform-specific behavior belongs to the native
tool implementations executed by mai.remote.python_worker/build_registry.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

import httpx

from .python_worker import execute_native


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--key", required=True)
    return parser


async def run_agent(*, server: str, session: str, key: str) -> None:
    if not server.lower().startswith("https://"):
        raise ValueError("HTTPS MAI server required")
    base = server.rstrip("/")
    headers = {"X-MAI-Agent-Secret": key}
    timeout = httpx.Timeout(40.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        try:
            while True:
                response = await client.post(f"{base}/remote/agent/{session}/poll")
                response.raise_for_status()
                body = response.json()
                request = body.get("request")
                if request is None:
                    continue
                request_id = request["request_id"]
                operation = request["operation"]
                arguments = request["arguments"]
                payload: dict[str, Any] = {"request_id": request_id, "ok": False}
                try:
                    if operation != "native.invoke":
                        raise RuntimeError(f"Unsupported operation {operation}")
                    name = arguments["name"]
                    native_arguments = arguments["arguments"]
                    if not isinstance(name, str) or not isinstance(native_arguments, dict):
                        raise TypeError("native.invoke requires a tool name and object arguments")
                    payload["result"] = await execute_native(name, native_arguments)
                    payload["ok"] = True
                except Exception as exc:
                    payload["error_type"] = type(exc).__name__
                    payload["error"] = str(exc)
                result = await client.post(
                    f"{base}/remote/agent/{session}/result",
                    json=payload,
                )
                result.raise_for_status()
        finally:
            try:
                await client.delete(f"{base}/remote/agent/{session}", timeout=5.0)
            except Exception:
                pass


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(run_agent(server=args.server, session=args.session, key=args.key))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
