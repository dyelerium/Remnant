"""POST /admin/secrets, GET /admin/security — secret CRUD, security tests."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["admin"])


class SecretSet(BaseModel):
    name: str
    value: str


class SecurityTest(BaseModel):
    text: str


@router.post("/admin/secrets")
async def set_secret(body: SecretSet, request: Request) -> dict:
    secrets = request.app.state.secrets
    secrets.set_secret(body.name, body.value)
    return {"status": "stored", "name": body.name}


@router.get("/admin/secrets")
async def list_secrets(request: Request) -> dict:
    secrets = request.app.state.secrets
    return {"secrets": secrets.list_secrets()}


@router.delete("/admin/secrets/{name}")
async def delete_secret(name: str, request: Request) -> dict:
    secrets = request.app.state.secrets
    deleted = secrets.delete_secret(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Secret {name!r} not found")
    return {"deleted": name}


@router.post("/admin/security/test")
async def security_test(body: SecurityTest, request: Request) -> dict:
    security = request.app.state.security
    safe, reason = security.is_safe(body.text)
    redacted = security.redact_prompt(body.text)
    return {
        "safe": safe,
        "reason": reason,
        "redacted": redacted,
    }


@router.get("/admin/security/blocked")
async def security_blocked_log(limit: int = 20, request: Request = None) -> dict:
    security = request.app.state.security
    log = security.get_blocked_log(limit=limit)
    return {"blocked": log, "count": len(log)}


@router.get("/admin/agent-graph")
async def agent_graph(request: Request) -> dict:
    graph = request.app.state.agent_graph
    return graph.to_dict()


@router.get("/admin/lanes")
async def lane_status(request: Request) -> dict:
    lane_manager = request.app.state.lane_manager
    return lane_manager.get_status()


@router.post("/admin/compact")
async def trigger_compaction(request: Request) -> dict:
    compactor = request.app.state.compactor
    import asyncio
    n = await asyncio.get_event_loop().run_in_executor(None, compactor.compact)
    return {"compacted": n}
