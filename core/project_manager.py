"""Project manager — project CRUD + Dev Orchestrator (supervises Claude Code via MCP)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECTS_KEY = "remnant:projects"


class ProjectManager:
    """Create, read, update and delete Remnant projects."""

    def __init__(self, redis_client, config: dict) -> None:
        self.redis = redis_client.r
        self.config = config
        self._memory_root = Path(config.get("memory_root", "./memory"))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, project: dict) -> dict:
        """Create a new project. Returns project dict with generated id."""
        project_id = project.get("project_id") or _slugify(project.get("name", "project"))
        project["project_id"] = project_id
        project["created_at"] = int(time.time())
        project.setdefault("status", "active")

        self.redis.hset(_PROJECTS_KEY, project_id, json.dumps(project))

        # Create project Markdown file
        proj_file = self._memory_root / "projects" / f"{project_id}.md"
        proj_file.parent.mkdir(parents=True, exist_ok=True)
        if not proj_file.exists():
            proj_file.write_text(
                f"# {project.get('name', project_id)}\n\n"
                f"{project.get('description', '')}\n"
            )

        logger.info("[PROJECTS] Created project: %s", project_id)
        return project

    def get(self, project_id: str) -> Optional[dict]:
        raw = self.redis.hget(_PROJECTS_KEY, project_id)
        if raw is None:
            return None
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    def list_all(self) -> list[dict]:
        raw = self.redis.hgetall(_PROJECTS_KEY)
        projects = []
        for v in raw.values():
            try:
                projects.append(json.loads(v.decode() if isinstance(v, bytes) else v))
            except Exception:
                pass
        return sorted(projects, key=lambda p: p.get("created_at", 0), reverse=True)

    def update(self, project_id: str, updates: dict) -> Optional[dict]:
        project = self.get(project_id)
        if not project:
            return None
        project.update(updates)
        project["updated_at"] = int(time.time())
        self.redis.hset(_PROJECTS_KEY, project_id, json.dumps(project))
        return project

    def delete(self, project_id: str) -> bool:
        deleted = self.redis.hdel(_PROJECTS_KEY, project_id)
        return bool(deleted)

    # ------------------------------------------------------------------
    # Dev Orchestrator
    # ------------------------------------------------------------------

    async def send_to_claude_code(
        self,
        project_id: str,
        task: str,
        mcp_client,             # tools.mcp_client.MCPClient
    ) -> dict:
        """
        Send a development task to Claude Code via the MCP protocol.

        Reads the project's CLAUDE.md for context, sends task via MCP,
        then polls for result.
        """
        project = self.get(project_id)
        if not project:
            raise ValueError(f"Project {project_id!r} not found")

        # Read CLAUDE.md if present
        claude_md = ""
        claude_md_path = Path(project.get("working_dir", ".")) / "CLAUDE.md"
        if claude_md_path.exists():
            claude_md = claude_md_path.read_text(encoding="utf-8")

        context = f"# Project: {project.get('name', project_id)}\n\n"
        if claude_md:
            context += f"## CLAUDE.md\n{claude_md}\n\n"
        context += f"## Task\n{task}\n"

        logger.info("[DEV ORCHESTRATOR] Sending task to Claude Code for project %s", project_id)

        try:
            result = await mcp_client.call_tool(
                "agent_run",
                {"message": context, "project_id": project_id},
            )
            return {
                "status": "sent",
                "project_id": project_id,
                "task": task,
                "result": result,
            }
        except Exception as exc:
            logger.error("[DEV ORCHESTRATOR] MCP call failed: %s", exc)
            return {
                "status": "error",
                "project_id": project_id,
                "error": str(exc),
            }

    def generate_claude_md(self, project: dict) -> str:
        """Generate a CLAUDE.md template for a new project."""
        name = project.get("name", project.get("project_id", "Project"))
        desc = project.get("description", "")
        working_dir = project.get("working_dir", ".")

        return (
            f"# {name} — Claude Code Instructions\n\n"
            f"## Context\n{desc}\n\n"
            f"## Working Directory\n{working_dir}\n\n"
            "## Conventions\n"
            "- Follow existing code style\n"
            "- Write tests for new features\n"
            "- Update documentation when APIs change\n"
            "- Use the Remnant memory tools to store important decisions\n"
        )


def _slugify(text: str) -> str:
    """Convert project name to a safe identifier."""
    import re
    return re.sub(r"[^a-z0-9_]", "_", text.lower().strip())[:64]
