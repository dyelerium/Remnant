"""GET /health — Redis ping + version (watchdog endpoint)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from core.version import VERSION

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    redis_ok = False
    try:
        loop = asyncio.get_event_loop()
        redis_ok = await asyncio.wait_for(
            loop.run_in_executor(None, request.app.state.redis.ping),
            timeout=3.0,
        )
    except Exception:
        pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "up" if redis_ok else "down",
        "version": VERSION,
    }
