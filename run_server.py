"""프로젝트 루트의 설정과 데이터로 MAI 웹 서버를 실행한다."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)

import uvicorn
from portable.restart_mai import register_server, unregister_server
from dotenv import load_dotenv

from mai.app.resumable_chat import install as install_resumable_chat
from mai.app.server import app


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    install_resumable_chat()
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    register_server()
    try:
        uvicorn.run(app, host=host, port=port, workers=1)
    finally:
        unregister_server()
