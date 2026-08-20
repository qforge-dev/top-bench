from __future__ import annotations

import uvicorn

from .config import Settings


def serve() -> None:
    settings = Settings()
    uvicorn.run(
        "top_arena_server.app:create_app",
        factory=True,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        access_log=True,
    )
