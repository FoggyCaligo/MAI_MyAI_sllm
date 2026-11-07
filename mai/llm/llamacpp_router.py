"""Fail-closed llama.cpp router model handoff.

Router control endpoints are local, trusted, and used only when MAI runs with
the single-resident Portable launcher. We require an observed 'unloaded'
status before issuing a chat request to a different model.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

T = TypeVar("T")
_ACTIVE_STATUSES = frozenset({"loaded", "loading", "sleeping"})


class RouterModelTransitionError(RuntimeError):
    """Model transition was not confirmed; the target must not be invoked."""


class SingleResidentRouter:
    def __init__(self, host: str, *, unload_timeout_seconds: float = 30.0,
                 poll_interval_seconds: float = 0.25) -> None:
        if unload_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("router transition timeouts must be positive")
        self.host = host.rstrip("/")
        self.unload_timeout_seconds = unload_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._lock = asyncio.Lock()

    async def _models(self, client: httpx.AsyncClient) -> dict[str, str]:
        try:
            response = await client.get(self.host + "/models", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RouterModelTransitionError("Could not query llama.cpp router model status") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise RouterModelTransitionError("Router /models did not contain a model list")
        statuses: dict[str, str] = {}
        for model in data:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                raise RouterModelTransitionError("Router /models has malformed model identity")
            raw_status = model.get("status")
            status = raw_status.get("value") if isinstance(raw_status, dict) else None
            if not isinstance(status, str):
                raise RouterModelTransitionError("Router /models did not report a model status")
            statuses[model["id"]] = status
        return statuses

    async def _confirm_unload(self, client: httpx.AsyncClient, model: str) -> None:
        try:
            response = await client.post(
                self.host + "/models/unload", json={"model": model}, timeout=10.0
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RouterModelTransitionError(f"Could not request model unload: {model}") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RouterModelTransitionError(f"Router rejected model unload: {model}")
        deadline = time.monotonic() + self.unload_timeout_seconds
        while True:
            state = (await self._models(client)).get(model)
            if state == "unloaded":
                return
            if state is None:
                raise RouterModelTransitionError(f"Unloaded model disappeared from router registry: {model}")
            if time.monotonic() >= deadline:
                raise RouterModelTransitionError(f"Model unload confirmation timed out: {model} (state={state})")
            await asyncio.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))

    async def execute(self, model: str, client: httpx.AsyncClient,
                      request: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
        async with self._lock:
            statuses = await self._models(client)
            if model not in statuses:
                raise RouterModelTransitionError(f"Target model not registered in router: {model}")
            # Refuse an ambiguous state rather than misidentify an active child.
            for name, status in statuses.items():
                if status not in _ACTIVE_STATUSES and status != "unloaded":
                    raise RouterModelTransitionError(
                        f"Router reported unsupported model state: {name}={status}"
                    )
            # Do not unload the target: this preserves main's KV between tool turns.
            for name, status in statuses.items():
                if name != model and status in _ACTIVE_STATUSES:
                    await self._confirm_unload(client, name)
            confirmed = await self._models(client)
            if any(name != model and state != "unloaded" for name, state in confirmed.items()):
                raise RouterModelTransitionError("Other model is not confirmed unloaded")
            # Keep the router lock across the whole inference, so another MAI
            # adapter cannot evict the current model while it is generating.
            return await request(client)
