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
    root = str(_APP_ROOT.resolve())
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        for member in tar.getmembers():
            dest = os.path.realpath(os.path.join(root, member.name))
            if not dest.startswith(root + os.sep) and dest != root:
                raise HTTPException(status_code=400, detail=f"Invalid archive path: {member.name}")
        tar.extractall(root)
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


_SECURITY_YAML = Path("/app/config/security.yaml") if Path("/app/config").exists() else Path("config/security.yaml")


@router.get("/admin/security/config")
async def get_security_config(request: Request) -> dict:
    """Return the full security.yaml as JSON."""
    path = _SECURITY_YAML
    if not path.exists():
        path = Path("config/security.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@router.put("/admin/security/config")
async def update_security_config(body: dict, request: Request) -> dict:
    """Persist security config to security.yaml and hot-reload the running SecurityManager."""
    path = _SECURITY_YAML
    if not path.exists():
        path = Path("config/security.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True)
    request.app.state.security.reload(body)
    return {"status": "updated"}


_SNAPSHOTS_DIR = Path("/app/snapshots") if Path("/app").exists() else Path("snapshots")


@router.get("/admin/snapshots")
async def list_snapshots() -> dict:
    """List available config snapshots."""
    snap_dir = _SNAPSHOTS_DIR
    snap_dir.mkdir(exist_ok=True)
    snaps = []
    for f in sorted(snap_dir.glob("config-*.tar.gz"), reverse=True):
        stat = f.stat()
        snaps.append({
            "name": f.name,
            "ts": int(stat.st_mtime),
            "size_kb": round(stat.st_size / 1024, 1),
        })
    return {"snapshots": snaps}


@router.post("/admin/snapshots/{name}/restore")
async def restore_snapshot(name: str, request: Request) -> dict:
    """Extract a snapshot back to /app/config/ then restart the container."""
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid snapshot name")
    snap_path = _SNAPSHOTS_DIR / name
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot {name!r} not found")

    config_dir = Path("/app/config") if Path("/app/config").exists() else Path("config")
    config_root = str(config_dir.resolve())
    with tarfile.open(snap_path, "r:gz") as tar:
        for member in tar.getmembers():
            dest = os.path.realpath(os.path.join(config_root, member.name))
            if not dest.startswith(config_root + os.sep) and dest != config_root:
                raise HTTPException(status_code=400, detail=f"Invalid archive path: {member.name}")
        tar.extractall(config_root)

    # Trigger container restart via Docker socket (best-effort)
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(os.environ.get("HOSTNAME", "remnant"))
        # Restart in background so response is sent first
        import asyncio
        asyncio.get_event_loop().call_later(1.0, lambda: container.restart())
    except Exception:
        pass  # Docker SDK not available or container not found — config still restored

    return {"status": "restoring", "snapshot": name}


@router.delete("/admin/snapshots/{name}")
async def delete_snapshot(name: str) -> dict:
    """Delete a config snapshot."""
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid snapshot name")
    snap_path = _SNAPSHOTS_DIR / name
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot {name!r} not found")
    snap_path.unlink()
    return {"status": "deleted", "name": name}


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


# ---------------------------------------------------------------------------
# Skills management
# ---------------------------------------------------------------------------

class SkillImport(BaseModel):
    yaml_text: str


class SkillTest(BaseModel):
    skill_name: str
    args: dict = {}


@router.get("/admin/skills")
async def list_skills(request: Request, tag: Optional[str] = None) -> dict:
    """Return all registered skills with full detail (including safety level and arg_map)."""
    registry = request.app.state.skill_registry
    raw = list(registry._skills.values())
    if tag:
        raw = [s for s in raw if tag in s.get("tags", [])]
    skills = []
    for s in raw:
        path = s.get("_path", "")
        skills.append({
            "name": s["name"],
            "description": s["description"],
            "tool": s["tool"],
            "tags": s.get("tags", []),
            "safety_level": s.get("safety_level", "safe"),
            "arg_map": s.get("arg_map", {}),
            "requires": s.get("requires", []),
            "input_schema": s.get("input_schema"),
            "builtin": "builtin" in path,
        })
    return {"skills": skills, "count": len(skills)}


@router.post("/admin/skills/reload")
async def reload_skills(request: Request) -> dict:
    """Reload all skill YAML files from disk."""
    registry = request.app.state.skill_registry
    count = registry.load()
    return {"status": "reloaded", "count": count}


@router.post("/admin/skills/test")
async def test_skill(body: SkillTest, request: Request) -> dict:
    """Execute a skill and return the result."""
    registry = request.app.state.skill_registry
    tool_registry = request.app.state.tool_registry
    try:
        result = await registry.invoke(body.skill_name, body.args, tool_registry)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/admin/skills/import")
async def import_skill(body: SkillImport, request: Request) -> dict:
    """Import a new skill from YAML text. Saves to skills/imported/<name>.yml."""
    import re
    try:
        skill = yaml.safe_load(body.yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

    if not skill or not isinstance(skill, dict):
        raise HTTPException(status_code=400, detail="YAML must be a mapping")

    missing = {"name", "description", "tool"} - set(skill.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    # Sanitise name for filename
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", skill["name"])
    skills_dir = Path("/app/skills") if Path("/app/skills").exists() else Path("skills")
    imported_dir = skills_dir / "imported"
    imported_dir.mkdir(exist_ok=True)
    dest = imported_dir / f"{safe_name}.yml"

    with open(dest, "w", encoding="utf-8") as f:
        yaml.dump(skill, f, default_flow_style=False, allow_unicode=True)

    # Hot-reload registry
    registry = request.app.state.skill_registry
    registry.load()
    return {"status": "imported", "name": skill["name"], "path": str(dest)}


@router.delete("/admin/skills/{name}")
async def delete_skill(name: str, request: Request) -> dict:
    """Delete an imported skill. Built-in skills cannot be deleted."""
    registry = request.app.state.skill_registry
    skill = registry.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    path = skill.get("_path", "")
    if "builtin" in path:
        raise HTTPException(status_code=400, detail="Cannot delete built-in skills")
    try:
        Path(path).unlink()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Skill file not found on disk")
    registry.load()
    return {"status": "deleted", "name": name}


# ---------------------------------------------------------------------------
# MCP tools info + test
# ---------------------------------------------------------------------------

class MCPTestRequest(BaseModel):
    tool_name: str
    arguments: dict = {}


@router.get("/admin/mcp/tools")
async def list_mcp_tools(request: Request) -> dict:
    """Return the list of tools exposed by Remnant's MCP server."""
    from api.mcp_endpoints import _MCP_TOOLS
    return {"tools": _MCP_TOOLS, "endpoint": "/mcp", "protocol": "JSON-RPC 2.0"}


@router.post("/admin/mcp/test")
async def test_mcp_tool(body: MCPTestRequest, request: Request) -> dict:
    """Execute an MCP tool call internally and return the result."""
    from api.mcp_endpoints import _dispatch_tool
    import uuid
    fake_rid = str(uuid.uuid4())[:8]
    try:
        result = await _dispatch_tool(fake_rid, body.tool_name, body.arguments, request)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
