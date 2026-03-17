"""
Convenience entry point — start the API server.

Usage::

    python run.py
"""

import os

import uvicorn

from app.config import settings


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=_env_bool("API_RELOAD", default=False),
    )
