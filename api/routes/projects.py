"""GET/POST /projects — CRUD + planning wizard trigger."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    template: str = "default"
    working_dir: str = ""
    budget_usd_daily: float = 2.0
    enable_mcp: bool = False


@router.get("/projects")
async def list_projects(request: Request) -> dict:
    pm = request.app.state.project_manager
    return {"projects": pm.list_all()}


@router.post("/projects")
async def create_project(proj: ProjectCreate, request: Request) -> dict:
    pm = request.app.state.project_manager
    project_dict = proj.model_dump()
    created = pm.create(project_dict)
    return {"project": created}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request) -> dict:
    pm = request.app.state.project_manager
    project = pm.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return {"project": project}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request) -> dict:
    pm = request.app.state.project_manager
    deleted = pm.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return {"deleted": project_id}


@router.get("/projects/{project_id}/stats")
async def project_stats(project_id: str, request: Request) -> dict:
    pi = request.app.state.proj_index
    return pi.get_project_stats(project_id)


@router.post("/projects/{project_id}/claude-code-task")
async def send_claude_code_task(
    project_id: str,
    body: dict,
    request: Request,
) -> dict:
    """Send a development task to Claude Code via MCP."""
    from tools.mcp_client import MCPClient
    pm = request.app.state.project_manager
    config = request.app.state.config

    mcp_url = config.get("mcp", {}).get("url", "http://localhost:8000")
    mcp_client = MCPClient(mcp_url)

    task = body.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="task field required")

    result = await pm.send_to_claude_code(project_id, task, mcp_client)
    return result
