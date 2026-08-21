from __future__ import annotations

import uvicorn

from mai.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "mai.server:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
        access_log=False,
        log_level="warning",
    )
