"""OpenClaw adapter — parse OpenClaw skill patterns → Remnant BaseTool."""
from __future__ import annotations

import logging

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class OpenClawAdapter(BaseTool):
    """
    Wraps an OpenClaw skill definition as a Remnant BaseTool.

    OpenClaw skill format (YAML-based):
    name: skill_name
    description: ...
    trigger_patterns: [...]
    actions:
      - type: http | shell | python
        ...
    """

    def __init__(self, skill_def: dict) -> None:
        self.name = skill_def.get("name", "openclaw_skill")
        self.description = skill_def.get("description", "")
        self._actions: list[dict] = skill_def.get("actions", [])
        self._triggers: list[str] = skill_def.get("trigger_patterns", [])

    async def run(self, args: dict, **context) -> ToolResult:
        results = []

        for action in self._actions:
            action_type = action.get("type", "")
            try:
                if action_type == "http":
                    result = await self._run_http(action, args)
                elif action_type == "python":
                    result = await self._run_python(action, args)
                elif action_type == "shell":
                    result = await self._run_shell(action, args)
                else:
                    result = {"error": f"Unknown action type: {action_type}"}
                results.append(result)
            except Exception as exc:
                results.append({"error": str(exc), "action": action_type})

        return ToolResult(self.name, True, output=results)

    async def _run_http(self, action: dict, args: dict) -> dict:
        import httpx
        url = action.get("url", "").format(**args)
        method = action.get("method", "GET")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.request(method, url, json=action.get("body"))
            return {"status": r.status_code, "content": r.text[:2000]}

    async def _run_python(self, action: dict, args: dict) -> dict:
        code = action.get("code", "")
        namespace = {"args": args, "result": None}
        exec(compile(code, "<openclaw>", "exec"), namespace)  # noqa: S102
        return {"result": namespace.get("result")}

    async def _run_shell(self, action: dict, args: dict) -> dict:
        import asyncio
        cmd = action.get("command", "").format(**args)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "stdout": stdout.decode(errors="replace"),
            "returncode": proc.returncode,
        }

    @classmethod
    def from_yaml(cls, yaml_dict: dict) -> "OpenClawAdapter":
        return cls(yaml_dict)
