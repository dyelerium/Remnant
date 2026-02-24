"""POST /admin/secrets, GET /admin/security — secret CRUD, security tests, scheduler, backup."""
from __future__ import annotations

import io
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["admin"])

_AGENTS_YAML = Path("/app/config/agents.yaml")


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


# ---------------------------------------------------------------------------
# Agent config CRUD
# ---------------------------------------------------------------------------

@router.get("/admin/agents")
async def get_agents(request: Request) -> dict:
    """Return the full agents.yaml as JSON."""
    try:
        with open(_AGENTS_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except FileNotFoundError:
        return {"agents": {}, "routing": {}}


@router.put("/admin/agents/{agent_name}")
async def update_agent(agent_name: str, body: dict, request: Request) -> dict:
    """Update a single agent config and persist to agents.yaml."""
    try:
        with open(_AGENTS_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {"agents": {}, "routing": {}}

    if "agents" not in data:
        data["agents"] = {}
    data["agents"][agent_name] = body

    with open(_AGENTS_YAML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    # Hot-reload agent configs in the live orchestrator
    request.app.state.config["agents"] = data.get("agents", {})

    return {"status": "updated", "agent": agent_name}


@router.delete("/admin/agents/{agent_name}")
async def delete_agent(agent_name: str, request: Request) -> dict:
    """Remove an agent from agents.yaml."""
    if agent_name == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the 'default' agent")
    try:
        with open(_AGENTS_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="agents.yaml not found")

    if agent_name not in data.get("agents", {}):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    del data["agents"][agent_name]
    with open(_AGENTS_YAML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    request.app.state.config["agents"] = data.get("agents", {})
    return {"status": "deleted", "agent": agent_name}


@router.get("/admin/schedule")
async def get_schedule(request: Request) -> dict:
    scheduler = request.app.state.scheduler
    if not scheduler or not scheduler._scheduler:
        return {"jobs": [], "running": False}
    jobs = []
    for job in scheduler._scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.id.replace("_", " ").title(),
            "trigger": str(job.trigger),
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"jobs": jobs, "running": bool(scheduler._scheduler.running)}


@router.post("/admin/schedule/{job_id}/run")
async def run_job_now(job_id: str, request: Request) -> dict:
    scheduler = request.app.state.scheduler
    if not scheduler or not scheduler._scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not running")
    job = scheduler._scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    job.modify(next_run_time=datetime.now(timezone.utc))
    return {"status": "triggered", "job_id": job_id}


_APP_ROOT = Path("/app") if Path("/app").exists() else Path(".")


@router.get("/admin/backup")
async def download_backup(request: Request) -> StreamingResponse:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for glob_pat in ["memory/*.md", "config/*.yaml"]:
            for f in _APP_ROOT.glob(glob_pat):
                if f.is_file():
                    tar.add(f, arcname=str(f.relative_to(_APP_ROOT)))
    buf.seek(0)
    ts = int(time.time())
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="remnant-backup-{ts}.tar.gz"'},
    )


@router.post("/admin/restore")
async def restore_backup(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                raise HTTPException(status_code=400, detail="Invalid archive path")
        tar.extractall(str(_APP_ROOT))
    return {"status": "restored", "filename": file.filename}


@router.post("/admin/diagnose")
async def run_diagnose(request: Request) -> dict:
    from tools.diagnose_tool import run_diagnostics
    results = await run_diagnostics(request.app.state.redis, request.app.state.registry)
    return {"results": results}


@router.put("/admin/routing")
async def update_routing(body: dict, request: Request) -> dict:
    """Update channel routing in agents.yaml."""
    try:
        with open(_AGENTS_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {"agents": {}, "routing": {}}

    data["routing"] = body
    with open(_AGENTS_YAML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    return {"status": "updated", "routing": body}


@router.get("/admin/audit")
async def get_audit_log(
    request: Request,
    limit: int = 100,
    date: Optional[str] = None,
    event_type: Optional[str] = None,
) -> dict:
    """Return recent audit log entries, optionally filtered by date and event_type."""
    audit = request.app.state.audit
    if not audit:
        return {"entries": [], "error": "Audit logger not initialised"}
    entries = audit.get_recent(limit=limit, date_str=date)
    if event_type:
        entries = [e for e in entries if e.get("event_type") == event_type]
    return {"entries": entries, "count": len(entries)}
