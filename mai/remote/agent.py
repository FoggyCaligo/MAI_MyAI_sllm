"""One-shot remote executor. No inbound listener, persistence or privilege escalation."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import httpx

from mai.remote.session import RemoteOperation


def execute(request: RemoteOperation) -> dict:
    args = request.arguments
    if request.operation == "file.read":
        path = Path(args["path"])
        data = path.read_bytes()
        if len(data) > 2_000_000:
            raise ValueError("file exceeds 2 MB response limit")
        return {"path": str(path), "text": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()}
    if request.operation == "file.write":
        path = Path(args["path"])
        previous_hash = args.get("expected_sha256")
        if path.exists():
            if previous_hash is None:
                raise ValueError("expected_sha256 is required when overwriting a file")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != previous_hash:
                raise ValueError("file changed since it was read")
        elif previous_hash is not None:
            raise ValueError("expected_sha256 supplied for a nonexistent file")
        path.write_text(args["text"], encoding="utf-8")
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if request.operation == "file.list":
        directory = Path(args["path"])
        return {"entries": [{"name": item.name, "is_dir": item.is_dir()} for item in list(directory.iterdir())[:1000]]}
    if request.operation == "file.delete":
        path = Path(args["path"])
        if path.is_dir():
            raise IsADirectoryError(str(path))
        path.unlink()
        return {"deleted": str(path)}
    if request.operation == "process.run":
        executable = args["executable"]
        argv = args.get("argv", [])
        timeout = min(int(args.get("timeout_seconds", 60)), 120)
        if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
            raise ValueError("argv must contain strings")
        result = subprocess.run(
            [executable, *argv], cwd=args.get("cwd"), capture_output=True,
            timeout=timeout, check=False,
        )
        output = {
            "returncode": result.returncode,
            "stdout": result.stdout[:200_000].decode("utf-8", errors="replace"),
            "stderr": result.stderr[:200_000].decode("utf-8", errors="replace"),
        }
        if result.returncode != 0:
            raise RuntimeError(json.dumps(output, ensure_ascii=False))
        return output
    raise ValueError(f"unsupported remote operation: {request.operation}")


async def main(url: str, session_id: str) -> None:
    if not url.startswith("https://"):
        raise ValueError("Remote Agent requires an HTTPS MAI server")
    print("Remote PC management grants MAI broad access to files and programs.")
    print(f"Host: {platform.node()} | User: {getpass.getuser()} | Server: {url}")
    consent = input("Type YES to authorize this one-time remote session: ")
    if consent != "YES":
        raise PermissionError("Remote management was not authorized")
    secret = getpass.getpass("One-time agent secret (from MAI owner session): ")
    headers = {"X-MAI-Agent-Secret": secret}
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=30, follow_redirects=False) as client:
        try:
            await run_session(client, session_id)
        finally:
            response = await client.delete(f"/remote/agent/{session_id}")
            response.raise_for_status()


async def run_session(client: httpx.AsyncClient, session_id: str) -> None:
    while True:
        response = await client.post(f"/remote/agent/{session_id}/poll")
        response.raise_for_status()
        request = response.json().get("request")
        if request is None:
            continue
        request_id = request["request_id"]
        try:
            operation = RemoteOperation.model_validate({
                "operation": request["operation"], "arguments": request["arguments"]
            }, strict=True)
            result = await asyncio.to_thread(execute, operation)
            payload = {"request_id": request_id, "ok": True, "result": result}
        except Exception as exc:
            payload = {
                "request_id": request_id, "ok": False,
                "error_type": type(exc).__name__, "error": str(exc),
            }
        delivered = await client.post(f"/remote/agent/{session_id}/result", json=payload)
        delivered.raise_for_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--session", required=True)
    options = parser.parse_args()
    try:
        asyncio.run(main(options.server.rstrip("/"), options.session))
    except KeyboardInterrupt:
        print("Remote Agent stopped locally")
        sys.exit(0)
