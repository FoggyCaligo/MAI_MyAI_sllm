"""Experimental llama.cpp slot checkpoint manager.

One physical inference slot can hold only one active cache at a time. This
module saves it to server-owned files and restores a role-specific checkpoint.
It does NOT intercept chat requests, own a slot, or promise cache hits.
Enable only with a dedicated llama.cpp server running --slot-save-path.
Ollama does not expose this endpoint.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

import httpx


class CacheRole(str, Enum):
    MAIN = "main"
    NUMERIC = "numeric"
    FINAL = "final"


class SlotCacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlotCheckpoint:
    role: CacheRole
    filename: str
    tokens: int
    bytes_written: int


class LlamaCppSlotCacheManager:
    """Fail-closed, per-run, serialized slot cache save/restore primitive.

    A caller MUST hold the same exclusive lock during the associated inference
    request, ensure that the selected slot is not processing other requests,
    and preserve the exact model, tokenizer, chat template and prompt prefix.
    """

    def __init__(self, *, base_url: str, slot_id: int = 0,
                 run_id: str, client: httpx.AsyncClient | None = None):
        if slot_id < 0:
            raise ValueError("slot_id must be nonnegative")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("run_id must be a safe nonempty identifier")
        self.base_url = base_url.rstrip("/")
        self.slot_id = slot_id
        self.run_id = run_id
        self._client = client
        self._lock = asyncio.Lock()
        self._checkpoints: dict[CacheRole, SlotCheckpoint] = {}

    def checkpoint(self, role: CacheRole) -> SlotCheckpoint | None:
        return self._checkpoints.get(role)

    async def save(self, role: CacheRole) -> SlotCheckpoint:
        """Save a fully idle slot; does not alter previously saved roles."""
        role = CacheRole(role)
        async with self._lock:
            filename = f"mai-{self.run_id}-{role.value}.bin"
            data = await self._post("save", {"filename": filename})
            if data.get("filename") != filename or data.get("id_slot") != self.slot_id:
                raise SlotCacheError("slot save returned a mismatched checkpoint")
            tokens, written = data.get("n_saved"), data.get("n_written")
            if not isinstance(tokens, int) or not isinstance(written, int) or tokens < 0 or written < 0:
                raise SlotCacheError("slot save returned invalid cache accounting")
            result = SlotCheckpoint(role, filename, tokens, written)
            self._checkpoints[role] = result
            return result

    async def restore(self, role: CacheRole) -> int:
        """Restore only an already saved checkpoint; missing states fail."""
        role = CacheRole(role)
        async with self._lock:
            checkpoint = self._checkpoints.get(role)
            if checkpoint is None:
                raise SlotCacheError(f"no saved KV cache for role {role.value}")
            data = await self._post("restore", {"filename": checkpoint.filename})
            if data.get("id_slot") != self.slot_id or data.get("filename") != checkpoint.filename:
                raise SlotCacheError("slot restore returned a mismatched checkpoint")
            tokens = data.get("n_restored")
            if not isinstance(tokens, int) or tokens != checkpoint.tokens:
                raise SlotCacheError("slot restore token count differs from saved checkpoint")
            return tokens

    async def erase_active(self) -> int:
        """Erase active slot contents, without deleting the saved files."""
        async with self._lock:
            data = await self._post("erase", None)
            if data.get("id_slot") != self.slot_id or not isinstance(data.get("n_erased"), int):
                raise SlotCacheError("slot erase returned invalid response")
            return data["n_erased"]

    async def _post(self, action: str, body: Mapping[str, Any] | None) -> dict[str, Any]:
        url = f"{self.base_url}/slots/{self.slot_id}?action={action}"
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(url, json=body or {})
            else:
                response = await self._client.post(url, json=body or {})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SlotCacheError(f"llama.cpp slot {action} failed") from exc
        if not isinstance(data, dict):
            raise SlotCacheError(f"llama.cpp slot {action} returned non-object response")
        return data
