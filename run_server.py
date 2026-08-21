from __future__ import annotations

import os

from dotenv import load_dotenv
import uvicorn


if __name__ == "__main__":
    load_dotenv()
    host = os.getenv("MAI_HOST", "127.0.0.1")
    port = int(os.getenv("MAI_PORT", "8000"))
    uvicorn.run("mai.web:app", host=host, port=port, reload=False)
