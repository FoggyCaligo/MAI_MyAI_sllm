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
                 poll_interval_seconds: float = 0.25,
                 load_timeout_seconds: float = 180.0) -> None:
        if unload_timeout_seconds <= 0 or poll_interval_seconds <= 0 or load_timeout_seconds <= 0:
            raise ValueError("router transition timeouts must be positive")
        self.host = host.rstrip("/")
        self.unload_timeout_seconds = unload_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.load_timeout_seconds = load_timeout_seconds
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

    async def _confirm_load(self, client: httpx.AsyncClient, model: str) -> None:
        """Explicitly load model and observe readiness before chat; no implicit autoload race."""
        try:
            response = await client.post(
                self.host + "/models/load", json={"model": model}, timeout=15.0
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            raise RouterModelTransitionError(
                f"Router load rejected model={model} http_status={exc.response.status_code} "
                f"body={exc.response.text[:800]!r}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RouterModelTransitionError(f"Could not request model load: {model}") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RouterModelTransitionError(f"Router refused model load: {model} response={str(result)[:800]}")
        deadline = time.monotonic() + self.load_timeout_seconds
        while True:
            try:
                response = await client.get(self.host + "/models", timeout=5.0)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RouterModelTransitionError(f"Cannot poll model load: {model}") from exc
            models = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(models, list):
                raise RouterModelTransitionError("Router load-status response lacks model list")
            state = next((entry for entry in models if isinstance(entry, dict) and entry.get("id") == model), None)
            if not isinstance(state, dict) or not isinstance(state.get("status"), dict):
                raise RouterModelTransitionError(f"Router lost target model during load: {model}")
            status = state["status"]
            value = status.get("value")
            if value == "loaded":
                return
            if status.get("failed") is True:
                raise RouterModelTransitionError(
                    f"Router model load failed: {model} exit_code={status.get('exit_code')}"
                )
            if value not in {"unloaded", "loading", "sleeping"}:
                raise RouterModelTransitionError(f"Unexpected target model load state: {model}={value!r}")
            if time.monotonic() >= deadline:
                raise RouterModelTransitionError(
                    f"Model load confirmation timed out: {model} (state={value})"
                )
            await asyncio.sleep(min(self.poll_interval_seconds, max(0, deadline - time.monotonic())))

    async def _ensure_model_locked(self, model: str, client: httpx.AsyncClient) -> None:
        """Select one resident model with explicit unload/load confirmation.

        The caller must hold _lock. No inference is required for readiness.
        """
        statuses = await self._models(client)
        if model not in statuses:
            raise RouterModelTransitionError(f"Target model not registered in router: {model}")
        for name, status in statuses.items():
            if status not in _ACTIVE_STATUSES and status != "unloaded":
                raise RouterModelTransitionError(
                    f"Router reported unsupported model state: {name}={status}"
                )
        for name, status in statuses.items():
            if name != model and status in _ACTIVE_STATUSES:
                await self._confirm_unload(client, name)
        confirmed = await self._models(client)
        if any(name != model and state != "unloaded" for name, state in confirmed.items()):
            raise RouterModelTransitionError("Other model is not confirmed unloaded")
        if confirmed.get(model) != "loaded":
            await self._confirm_load(client, model)

    async def prepare(self, model: str, client: httpx.AsyncClient) -> None:
        """Preload the main model without sending a chat or creating KV state."""
        async with self._lock:
            await self._ensure_model_locked(model, client)

    async def execute(self, model: str, client: httpx.AsyncClient,
                      request: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
        async with self._lock:
            await self._ensure_model_locked(model, client)
            # Hold the router lock through inference; preloading cannot evict
            # a model currently processing another chat request.
            return await request(client)
