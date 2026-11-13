"""Standalone loopback-only portfolio backend using MAI's real agent and original UI.

This module deliberately does not import mai.app.server, auth, hosting or remote
worker modules. Do not bind this unauthenticated demo outside loopback.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from mai.agent.runtime import AgentRuntime
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ModelConfig
from mai.tools.registry import ToolRegistry
from mai.tools.calculator import register_calculator_tools
from mai.tools.time import register_time_tools
from mcp_memorizer.manager import Memorizer, MemorySearchInput
from mcp_memorizer.extraction.service import ModelFactExtractor
from mcp_memorizer.integrations.mai import MAIExtractionAdapter
from mcp_memorizer.policy import MEMORY_MODEL_CONTEXT, MEMORY_MODEL_CONTEXT_KEY, MEMORY_SEARCH_DESCRIPTION

DATA_DIR = ROOT / "mai/mcp_tools/mcp_memorizer/MAI_MEMORY"
WEB = ROOT / "mai/app/static"
LLAMA_URL = "http://127.0.0.1:8080"
USER_ID = "portfolio-local"
SYSTEM_PROMPT = (
    "You are MAI, a local personal AI agent. This portfolio package uses the "
    "same MAI agent loop and llama.cpp model adapter. Explain clearly and "
    "ground claims in evidence. Tools must be activated with tool_manual. "
    "Available tools: memorizer__memory_search (search only), calculator, "
    "current_time. Memory extraction/storage is performed by the host after "
    "the final answer, never by the model. Remote access, account login, "
    "Tailscale, hosting, file editing and web searches are not installed. "
    "Do not claim to have used unavailable tools."
)

app = FastAPI(title="MAI Light Instant Runner", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
memory: Memorizer | None = None
history: dict[str, list[dict[str, str]]] = {}
current_model: str | None = None
chat_lock = asyncio.Lock()


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Original MAI chat UI supplies optional fields
    message: str = Field(min_length=1, max_length=12000)
    session_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)


class ModelSelect(BaseModel):
    model: str


@app.middleware("http")
async def restrict_local(request: Request, call_next):
    # Host validation matters even on a loopback-bound unauthenticated app:
    # prevent DNS rebinding and cross-origin POSTs.
    host = request.headers.get("host", "").split(":")[0].lower()
    if host not in {"localhost", "127.0.0.1"}:
        raise HTTPException(status_code=403, detail="Loopback host required")
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
        raise HTTPException(status_code=403, detail="Cross-origin request denied")
    return await call_next(request)


@app.on_event("startup")
async def initialize():
    global memory
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    memory = Memorizer(DATA_DIR, user_id=USER_ID)


@app.on_event("shutdown")
async def close():
    global memory
    if memory:
        memory.close()
        memory = None


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "verifier_default_enabled": False, "local_only": True,
            "remote_hosting_provider": "none"}


async def _model_ids() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            reply = await client.get(LLAMA_URL + "/v1/models")
            reply.raise_for_status()
        data = reply.json().get("data", [])
        return [record["id"] for record in data if isinstance(record, dict)
                and isinstance(record.get("id"), str)]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, detail=f"Local llama.cpp unavailable: {exc}") from exc


@app.get("/models")
async def models():
    names = await _model_ids()
    return {"models": [{"id": name, "name": name} for name in names],
            "selected": current_model or (names[0] if names else None),
            "data": [{"id": name} for name in names]}


@app.post("/models/select")
async def select_model(request: ModelSelect):
    global current_model
    names = await _model_ids()
    if request.model not in names:
        raise HTTPException(400, "Unknown GGUF model")
    current_model = request.model
    return {"ok": True, "model": current_model}


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_calculator_tools(registry)
    register_time_tools(registry)

    def search(node_id=None, keyword=None, limit=None):
        if memory is None:
            raise RuntimeError("Memorizer has not started")
        # This read-only call is the same public contract as MCP_memorizer.
        return memory.search(node_id=node_id, keyword=keyword, limit=limit)

    registry.add(name="memorizer__memory_search", description=MEMORY_SEARCH_DESCRIPTION,
                 input_model=MemorySearchInput, handler=search,
                 category="memory", timeout_seconds=120)
    registry.add_model_context(
        key=MEMORY_MODEL_CONTEXT_KEY,
        context=MEMORY_MODEL_CONTEXT,
        tools=["memorizer__memory_search"],
    )
    return registry


@app.post("/chat")
async def chat(body: ChatInput):
    global current_model
    async with chat_lock:
        available = await _model_ids()
        if not available:
            raise HTTPException(503, "No configured GGUF models")
        selected = body.model or current_model or available[0]
        if selected not in available:
            raise HTTPException(400, "Invalid model")
        session = body.session_id or uuid4().hex
        if session not in history and len(history) >= 20:
            raise HTTPException(429, "Demo session limit reached")
        earlier = history.get(session, [])[-16:]
        adapter = LlamaCppAdapter(ModelConfig(model=selected, host=LLAMA_URL))
        agent = AgentRuntime(adapter, _registry())
        try:
            result = await agent.run_user_message(
                body.message,
                prior_messages=[{"role": "system", "content": SYSTEM_PROMPT}, *earlier],
            )
        except Exception as exc:
            raise HTTPException(502, detail=f"MAI agent: {type(exc).__name__}: {exc}") from exc
        answer = result.content
        if not answer.strip():
            raise HTTPException(502, "No final answer")
        history.setdefault(session, []).extend((
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": answer},
        ))
        memory_status = "saved"
        try:
            assert memory is not None
            memory.runtime.fact_extractor = ModelFactExtractor(MAIExtractionAdapter(adapter))
            await memory.after_response(
                user_text=body.message,
                final_answer=answer,
                previous_assistant_message=earlier[-1]["content"] if earlier else None,
                turn_id=uuid4().hex,
            )
        except Exception as exc:
            memory_status = f"extraction_failed: {type(exc).__name__}"
        return {"answer": answer, "session_id": session, "model": selected,
                "model_rounds": result.model_rounds,
                "tools": [{"name": item.name, "ok": item.ok}
                          for item in result.tool_executions],
                "memory_status": memory_status}


def main():
    # Only loopback. No Tailscale, Cloudflare, login, remote listeners.
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
