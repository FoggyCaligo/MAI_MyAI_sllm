from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model: str | None = None
    image_model: str | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    text: str
    used_tools: list[str] = Field(default_factory=list)
    memory_writes: list[str] = Field(default_factory=list)
    tool_events: list[dict] = Field(default_factory=list)
