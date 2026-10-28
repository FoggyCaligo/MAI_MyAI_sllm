import asyncio

import pytest

from mai.remote.python_worker import build_registry, execute_native
from mai.tools.filesystem import FileReadInput, FileWriteInput


def test_remote_python_worker_reuses_local_native_schema():
    registry = build_registry()
    assert registry.get("file_read").input_model is FileReadInput
    assert registry.get("file_write").input_model is FileWriteInput
    assert registry.has("terminal_run")
    assert registry.has("document_edit")
    assert registry.has("code_search")
    assert registry.has("code_symbols")


def test_remote_python_worker_executes_existing_file_read(tmp_path):
    target = tmp_path / "example.txt"
    target.write_text("remote parity", encoding="utf-8")

    async def exercise():
        result = await execute_native("file_read", {"path": str(target)})
        assert result["content"] == "remote parity"
        with pytest.raises(Exception):
            await execute_native("nonexistent_tool", {})

    asyncio.run(exercise())
