import asyncio

from mai.app.runtime import MAIRuntime


def test_pending_memory_update_waits_for_same_user_only() -> None:
    async def scenario() -> None:
        runtime = MAIRuntime.__new__(MAIRuntime)
        runtime._background_tasks = set()
        runtime._memory_tasks_by_user = {}

        gate = asyncio.Event()

        async def pending_memory_write() -> None:
            await gate.wait()

        task = asyncio.create_task(pending_memory_write())
        runtime._background_tasks.add(task)
        runtime._memory_tasks_by_user["alice"] = task

        same_user_wait = asyncio.create_task(runtime._await_pending_memory_update("alice"))
        other_user_wait = asyncio.create_task(runtime._await_pending_memory_update("bob"))

        await asyncio.sleep(0)
        assert not same_user_wait.done()
        assert other_user_wait.done()

        gate.set()
        await same_user_wait
        await task

        runtime._forget_memory_task("alice", task)
        assert task not in runtime._background_tasks
        assert "alice" not in runtime._memory_tasks_by_user

    asyncio.run(scenario())


def test_forget_memory_task_does_not_remove_newer_task_for_same_user() -> None:
    async def scenario() -> None:
        runtime = MAIRuntime.__new__(MAIRuntime)
        runtime._background_tasks = set()
        runtime._memory_tasks_by_user = {}

        async def done() -> None:
            return None

        old_task = asyncio.create_task(done())
        new_task = asyncio.create_task(done())
        runtime._background_tasks.update({old_task, new_task})
        runtime._memory_tasks_by_user["alice"] = new_task
        await asyncio.gather(old_task, new_task)

        runtime._forget_memory_task("alice", old_task)

        assert old_task not in runtime._background_tasks
        assert runtime._memory_tasks_by_user["alice"] is new_task

        runtime._forget_memory_task("alice", new_task)
        assert "alice" not in runtime._memory_tasks_by_user

    asyncio.run(scenario())
