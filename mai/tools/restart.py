"""Owner-only native tool for handing a restart to an independent coordinator."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from portable.restart_mai import detach
from .registry import ToolRegistry


class RestartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    update: bool


def register_restart_tool(registry: ToolRegistry) -> None:
    registry.add(
        name="restart",
        description=(
            "Request an MAI HOST restart by an independent process. "
            "update=true runs git pull --ff-only origin dev after stopping MAI; "
            "update=false restarts without pulling. "
            "This disrupts the current server, chat, model and tool executions. "
            "Invoke ONLY when the user's current text/voice request explicitly "
            "authorizes restart or its established task scope explicitly includes "
            "applying changes to the running MAI. Do not treat code analysis or "
            "an unrequested self-improvement as authorization. "
            "Do not request a second UI confirmation for an authorized request. "
            "Finish any file writes and tests before calling restart, and call "
            "restart as the final operation. The result reports only that an "
            "independent coordinator accepted the request, NOT successful restart."
        ),
        input_model=RestartInput,
        handler=lambda update: detach(update=update, await_handoff=True),
        timeout_seconds=15,
        category="host-lifecycle",
        metadata={"model_context": {
            "authorization": "The owner's current natural-language or spoken instruction is approval if it expressly requests the restart or applying finished changes to the running MAI. Otherwise ask in chat before calling restart. Never use substring matching as proof of consent.",
            "scope": "MAI host only; not the connected remote worker.",
            "execution": "Restart ends the current agent process. Accepted != completed. Read MAI_MEMORY/restart.log after reconnecting to verify the result.",
        }},
    )
