"""Durable owner-selected model identity for the MAI runtime.

Absence of the selection file means the configured default model applies.
Invalid or unreadable persisted state is an explicit error, not a silent reset.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SelectedModelRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1)


class SelectedModelStore:
    def __init__(self, memory_db_path: str | Path) -> None:
        self.path = Path(memory_db_path).expanduser().resolve().parent / "selected_model.json"

    def read(self, *, configured_default: str) -> str:
        if not self.path.exists():
            return configured_default
        record = SelectedModelRecord.model_validate_json(self.path.read_text(encoding="utf-8"))
        return record.model

    def write(self, model: str) -> None:
        record = SelectedModelRecord(model=model)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
