"""Run the MAI local web UI."""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def configure_runtime_cwd() -> str:
    """Use MAI_CWD when configured, otherwise default local tools to the OS user home."""
    configured = os.environ.get("MAI_CWD")
    if configured is not None and configured.strip():
        return configured.strip()
    resolved = str(Path.home().resolve())
    os.environ["MAI_CWD"] = resolved
    return resolved


if __name__ == "__main__":
    load_dotenv()
    configure_runtime_cwd()

    from mai.app.resumable_chat import install as install_resumable_chat
    from mai.app.server import app

    install_resumable_chat()
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, workers=1)
