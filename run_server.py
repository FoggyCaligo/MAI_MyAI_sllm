"""Run the MAI local web UI."""
from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv

from mai.app.resumable_chat import install as install_resumable_chat
from mai.app.server import app


if __name__ == "__main__":
    load_dotenv()
    install_resumable_chat()
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, workers=1)
