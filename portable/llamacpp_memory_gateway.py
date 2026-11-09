"""Shared localhost gateway for all llama.cpp router clients (MAI, OpenClaw, etc.).

The public endpoint remains 127.0.0.1:8080; llama.cpp itself binds only to
127.0.0.1:8081. Safety checks run here, never in individual clients.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mai.llm.llamacpp_router import RouterModelTransitionError, SingleResidentRouter

_LOG = logging.getLogger(__name__)
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-connection", "transfer-encoding",
    "upgrade", "te", "trailer", "proxy-authenticate", "proxy-authorization",
    "host", "content-length",
})


def create_app(*, upstream: str = "http://127.0.0.1:8081",
               min_free_vram_mib: int = 1024,
               min_available_ram_mib: int = 6144,
               telemetry_path: Path | None = None) -> FastAPI:
    if upstream.rstrip("/") == "http://127.0.0.1:8080":
        raise ValueError("Gateway upstream must not point to its own public port")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard = SingleResidentRouter(
        upstream, min_free_vram_mib=min_free_vram_mib,
        min_available_ram_mib=min_available_ram_mib,
        telemetry_path=telemetry_path,
    )
    lock = asyncio.Lock()
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await client.aclose()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def proxy(path: str, request: Request):
        method = request.method
        route = "/" + path
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
        url = upstream.rstrip("/") + route
        if request.url.query:
            url += "?" + request.url.query

        # All possible model-triggering POSTs are guarded, including router
        # explicit loads, OpenAI-compatible endpoints, and native completions.
        # Only unloading a model is exempt; status GETs are read-only.
        guarded = method in {"POST", "PUT", "PATCH"} and route != "/models/unload"
        if guarded:
            await lock.acquire()
        sampler = None
        upstream_response = None
        handed_off = False
        try:
            if guarded:
                try:
                    payload = json.loads(body) if body else {}
                except ValueError:
                    return JSONResponse({"error": "Invalid model request JSON"}, status_code=400)
                if not isinstance(payload, dict):
                    return JSONResponse({"error": "Model request must be a JSON object"}, status_code=400)
                model = payload.get("model")
                if not isinstance(model, str) or not model:
                    # Strictly reject ambiguous, potentially auto-loading calls.
                    return JSONResponse({"error": "Specify a model ID for guarded requests"}, status_code=400)
                guard._check_available_ram(event="before_request")
                states = await guard._models(client)
                if model not in states:
                    return JSONResponse({"error": f"Unknown router model: {model}"}, status_code=404)
                if states[model] not in {"loaded", "sleeping"}:
                    await guard._verify_memory_budget(client, model)
                sampler = asyncio.create_task(guard._sample_during_inference())

            req = client.build_request(method, url, content=body, headers=headers)
            upstream_response = await client.send(req, stream=True)
            forwarded_headers = {
                k: v for k, v in upstream_response.headers.items()
                if k.lower() not in _HOP_HEADERS
            }

            async def stream():
                try:
                    async for chunk in upstream_response.aiter_raw():
                        yield chunk
                finally:
                    await upstream_response.aclose()
                    if sampler is not None:
                        sampler.cancel()
                        await asyncio.gather(sampler, return_exceptions=True)
                    if guarded:
                        lock.release()

            response = StreamingResponse(
                stream(), status_code=upstream_response.status_code,
                headers=forwarded_headers,
            )
            upstream_response = None  # stream owns closure
            sampler = None  # stream owns cancellation
            handed_off = True
            return response
        except RouterModelTransitionError as exc:
            guard._record_safety("gateway_rejected", path=route, reason=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=503)
        except httpx.HTTPError as exc:
            _LOG.warning("llama.cpp upstream failed: %s", exc)
            return JSONResponse({"error": "llama.cpp upstream unavailable"}, status_code=502)
        finally:
            if upstream_response is not None:
                await upstream_response.aclose()
            if sampler is not None:
                sampler.cancel()
                await asyncio.gather(sampler, return_exceptions=True)
            # A streaming response owns the lock until response completion.
            if guarded and not handed_off:
                lock.release()

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", default="http://127.0.0.1:8081")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--min-free-vram-mib", type=int, default=1024)
    parser.add_argument("--min-available-ram-mib", type=int, default=6144)
    parser.add_argument("--telemetry-path", type=Path, default=Path("MAI_MEMORY/gpu_ram_safety.jsonl"))
    args = parser.parse_args()
    uvicorn.run(create_app(
        upstream=args.upstream,
        min_free_vram_mib=args.min_free_vram_mib,
        min_available_ram_mib=args.min_available_ram_mib,
        telemetry_path=args.telemetry_path,
    ), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
