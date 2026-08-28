"""Run the MAI local web UI."""
from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv


if __name__ == "__main__":
    load_dotenv()
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    uvicorn.run("mai.app.server:app", host=host, port=port, workers=1)
