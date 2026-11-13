"""Fail-closed llama.cpp router model handoff.

Router control endpoints are local, trusted, and used only when MAI runs with
the single-resident Portable launcher. We require an observed 'unloaded'
status before issuing a chat request to a different model.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

T = TypeVar("T")
_ACTIVE_STATUSES = frozenset({"loaded", "loading", "sleeping"})


class RouterModelTransitionError(RuntimeError):
    """Model transition was not confirmed; the target must not be invoked."""


def available_ram_bytes() -> int | None:
    """Available physical RAM, not total committed bytes or free page cache."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        return int(status.ullAvailPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class SingleResidentRouter:
    def __init__(self, host: str, *, unload_timeout_seconds: float = 30.0,
                 poll_interval_seconds: float = 0.25,
                 load_timeout_seconds: float = 180.0,
                 min_free_vram_mib: int | None = None,
                 min_available_ram_mib: int | None = None,
                 telemetry_path: Path | None = None) -> None:
        if unload_timeout_seconds <= 0 or poll_interval_seconds <= 0 or load_timeout_seconds <= 0:
            raise ValueError("router transition timeouts must be positive")
        if min_free_vram_mib is not None and min_free_vram_mib <= 0:
            raise ValueError("min_free_vram_mib must be positive")
        if min_available_ram_mib is not None and min_available_ram_mib <= 0:
            raise ValueError("min_available_ram_mib must be positive")
        self.min_free_vram_mib = min_free_vram_mib
        self.min_available_ram_mib = min_available_ram_mib
        self.telemetry_path = telemetry_path
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


    def _record_safety(self, event: str, **details: object) -> None:
        """Persist lightweight samples so abrupt reboots leave diagnostic evidence."""
        if self.telemetry_path is None:
            return
        try:
            self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            with self.telemetry_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(
                    {"time_unix": time.time(), "event": event, **details},
                    ensure_ascii=False, default=str,
                ) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            # Telemetry is diagnostic only. It must not break normal inference.
            pass

    def _check_available_ram(self, *, event: str) -> None:
        if self.min_available_ram_mib is None:
            return
        available = available_ram_bytes()
        if available is None:
            self._record_safety("ram_measurement_unavailable", phase=event)
            raise RouterModelTransitionError("Cannot measure system available RAM; refusing new model work")
        free_mib = available // (1024 * 1024)
        self._record_safety("ram_check", phase=event, available_mib=free_mib,
                            minimum_mib=self.min_available_ram_mib)
        if available < self.min_available_ram_mib * 1024 * 1024:
            raise RouterModelTransitionError(
                f"System available RAM below safe minimum: available_mib={free_mib} "
                f"required_mib={self.min_available_ram_mib}"
            )

    async def _sample_during_inference(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            available = available_ram_bytes()
            self._record_safety(
                "inference_ram_sample",
                available_mib=available // (1024 * 1024) if available is not None else None,
                minimum_mib=self.min_available_ram_mib,
            )
            # In-flight llama.cpp work cannot be safely preempted by monitoring:
            # samples permit diagnosis and the next request rechecks the limit.

    async def _verify_memory_budget(self, client: httpx.AsyncClient, model: str) -> None:
        """Guard against a model load using router's per-device free-memory snapshot.

        Router /models memory measurements are taken at discovery time, not
        guaranteed live. A 1 GiB overhead allowance handles unmeasured
        multimodal allocations; the configured free threshold is additional.
        """
        if self.min_free_vram_mib is None:
            return
        deadline = time.monotonic() + 30.0
        while True:
            try:
                response = await client.get(self.host + "/models", timeout=10.0)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RouterModelTransitionError("Cannot query GPU memory estimates; load blocked") from exc
            entries = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise RouterModelTransitionError("Missing router memory metadata; load blocked")
            target = next((entry for entry in entries
                           if isinstance(entry, dict) and entry.get("id") == model), None)
            if not isinstance(target, dict):
                raise RouterModelTransitionError(f"Target model missing from memory metadata: {model}")
            memory = target.get("memory")
            if isinstance(memory, dict) and memory.get("status") == "measured":
                break
            if isinstance(memory, dict) and memory.get("status") == "failed":
                raise RouterModelTransitionError(f"Model GPU memory measurement failed: {model}")
            if time.monotonic() >= deadline:
                raise RouterModelTransitionError(f"Model GPU memory measurement timed out: {model}")
            await asyncio.sleep(self.poll_interval_seconds)
        maximum = memory.get("max")
        devices = maximum.get("devices") if isinstance(maximum, dict) else None
        if not isinstance(devices, list) or not devices:
            raise RouterModelTransitionError(f"Missing per-device GPU memory estimate: {model}")
        gpu_count = 0
        for device in devices:
            if not isinstance(device, dict):
                raise RouterModelTransitionError("Malformed GPU memory measurement")
            if device.get("kind") != "device":
                continue
            gpu_count += 1
            values = [device.get(field) for field in
                      ("free", "model", "context", "compute")]
            if any(type(value) is not int or value < 0 for value in values):
                raise RouterModelTransitionError("Incomplete GPU free-memory estimate; load blocked")
            free, weights, kv_cache, compute = values
            # Projector allocations are not currently measured by llama.cpp.
            projected_free = free - weights - kv_cache - compute - 1024 * 1024 * 1024
            self._record_safety(
                "gpu_model_preflight", model=model, device=device.get("name"),
                free_mib=free // (1024 * 1024),
                model_mib=weights // (1024 * 1024),
                context_mib=kv_cache // (1024 * 1024),
                compute_mib=compute // (1024 * 1024),
                projected_free_mib=projected_free // (1024 * 1024),
                minimum_mib=self.min_free_vram_mib,
            )
            if projected_free < self.min_free_vram_mib * 1024 * 1024:
                raise RouterModelTransitionError(
                    f"GPU free memory safety margin would be exceeded: model={model} "
                    f"device={device.get('name')} "
                    f"projected_free_mib={projected_free // (1024 * 1024)} "
                    f"required_mib={self.min_free_vram_mib}"
                )
        if gpu_count == 0:
            raise RouterModelTransitionError("Router did not measure a GPU; model load blocked")

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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RouterModelTransitionError(
                    f"Model load confirmation timed out: {model}"
                )
            try:
                response = await client.get(self.host + "/models", timeout=min(5.0, remaining))
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RouterModelTransitionError(
                    f"Router load-status rejected model={model} "
                    f"http_status={exc.response.status_code}"
                ) from exc
            except httpx.TransportError as exc:
                # An in-progress llama.cpp load may briefly interrupt status polling.
                # Never resend /models/load; only retry the read until the load deadline.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RouterModelTransitionError(
                        f"Model load status polling timed out: {model} "
                        f"last_error={type(exc).__name__}: {exc}"
                    ) from exc
                await asyncio.sleep(min(self.poll_interval_seconds, remaining))
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                raise RouterModelTransitionError(
                    f"Invalid router load-status JSON: {model}"
                ) from exc
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
        self._check_available_ram(event="preload")
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
            await self._verify_memory_budget(client, model)
            await self._confirm_load(client, model)

    async def prepare(self, model: str, client: httpx.AsyncClient) -> None:
        """Preload the main model without sending a chat or creating KV state."""
        async with self._lock:
            await self._ensure_model_locked(model, client)

    async def execute(self, model: str, client: httpx.AsyncClient,
                      request: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
        async with self._lock:
            await self._ensure_model_locked(model, client)
            self._check_available_ram(event="before_inference")
            # The sampler logs low RAM but does not assume cancelling the HTTP
            # request halts an already-running GPU kernel.
            sampler = asyncio.create_task(self._sample_during_inference()) if self.telemetry_path else None
            try:
                return await request(client)
            finally:
                if sampler is not None:
                    sampler.cancel()
                    try:
                        await sampler
                    except asyncio.CancelledError:
                        pass
