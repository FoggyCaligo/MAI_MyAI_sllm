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
            "Restart the currently running MAI host service so that deployed code changes take effect. "
            "This is a controlled stop-and-start, NOT a permanent shutdown of MAI or its LLM. "
            "An independent manager survives the old MAI process, stops it and its model runtime, "
            "then launches MAI and the model runtime again. "
            "With update=true, the manager runs git pull --ff-only origin dev between stop and start; "
            "with update=false, it starts again without pulling code. "
            "Current in-flight chat, model and tool operations end and do not automatically resume. "
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
        activation_requires_invocation=True,
        metadata={"model_context": {
            "authorization": "The owner's current natural-language or spoken instruction is approval if it expressly requests the restart or applying finished changes to the running MAI. Otherwise ask in chat before calling restart. Never use substring matching as proof of consent.",
            "scope": "MAI host only; not the connected remote worker.",
            "execution": "A separate process performs stop -> optional dev pull -> start of MAI and its model runtime. The old agent process ends but this is not a permanent shutdown. In-flight operations are interrupted, not resumed. Accepted means handoff, not completed; inspect mai/mcp_tools/mcp_memorizer/MAI_MEMORY/restart.log after reconnecting.",
        }},
    )
