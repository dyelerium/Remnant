"""GET /health — Redis ping + version (watchdog endpoint)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from core.version import VERSION

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    redis_ok = False
    try:
        redis_ok = request.app.state.redis.ping()
    except Exception:
        pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "up" if redis_ok else "down",
        "version": VERSION,
    }
