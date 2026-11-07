"""Unit tests for optional llama.cpp slot cache role checkpoint experiment."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mai.llm.slot_cache import CacheRole, CacheRoleSwitcher, LlamaCppSlotCacheManager, SlotCacheError


def run(coro):
    return asyncio.run(coro)


def fake_client(events, *, fail_action=None):
    async def respond(request):
        action = request.url.params.get("action")
        data = json.loads(request.content or b"{}")
        events.append((action, data))
        if action == fail_action:
            return httpx.Response(500, json={"error": "test failure"})
        filename = data.get("filename")
        if action == "save":
            return httpx.Response(200, json={"filename": filename, "id_slot": 0, "n_saved": 17, "n_written": 8000})
        if action == "restore":
            return httpx.Response(200, json={"filename": filename, "id_slot": 0, "n_restored": 17, "n_read": 8000})
        if action == "erase":
            return httpx.Response(200, json={"id_slot": 0, "n_erased": 17})
        raise AssertionError(action)

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def test_three_roles_save_and_restore_main_after_rejection():
    async def scenario():
        events = []
        async with fake_client(events) as client:
            manager = LlamaCppSlotCacheManager(base_url="http://127.0.0.1:8080", slot_id=0, run_id="turn1", client=client)
            roles = CacheRoleSwitcher(manager)
            await roles.switch(CacheRole.MAIN)
            await roles.switch(CacheRole.NUMERIC)
            await roles.switch(CacheRole.FINAL)
            await roles.switch(CacheRole.MAIN)
            assert roles.active == CacheRole.MAIN
            assert manager.checkpoint(CacheRole.MAIN).tokens == 17
            assert [action for action, _ in events] == ["erase", "save", "erase", "save", "erase", "save", "restore"]
            assert events[-1][1]["filename"] == "mai-turn1-main.bin"
    run(scenario())


def test_unsupported_restore_without_checkpoint_fails():
    async def scenario():
        events = []
        async with fake_client(events) as client:
            manager = LlamaCppSlotCacheManager(base_url="http://127.0.0.1:8080", run_id="turn2", client=client)
            with pytest.raises(SlotCacheError, match="no saved KV cache"):
                await manager.restore(CacheRole.FINAL)
    run(scenario())


def test_save_failure_preserves_previous_active_role():
    async def scenario():
        events = []
        async with fake_client(events, fail_action="save") as client:
            manager = LlamaCppSlotCacheManager(base_url="http://127.0.0.1:8080", run_id="turn3", client=client)
            roles = CacheRoleSwitcher(manager)
            await roles.switch(CacheRole.MAIN)
            with pytest.raises(SlotCacheError):
                await roles.switch(CacheRole.NUMERIC)
            assert roles.active == CacheRole.MAIN
            assert [action for action, _ in events] == ["erase", "save"]
    run(scenario())


@pytest.mark.parametrize("bad", ["../escape", "", "white space", "a/b", ".hidden"])
def test_run_id_validation(bad):
    with pytest.raises(ValueError):
        LlamaCppSlotCacheManager(base_url="http://localhost:8080", run_id=bad)
