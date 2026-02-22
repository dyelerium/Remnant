"""Agent Zero adapter — parse Agent Zero skill definitions → Remnant BaseTool."""
from __future__ import annotations

import logging
from typing import Any

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class AgentZeroAdapter(BaseTool):
    """
    Wraps an Agent Zero skill definition as a Remnant BaseTool.

    Agent Zero skill format (Python-based):
    {
        "name": "skill_name",
        "description": "...",
        "code": "async def execute(args): ...",
    }
    """

    def __init__(self, skill_def: dict) -> None:
        self.name = skill_def.get("name", "agent_zero_skill")
        self.description = skill_def.get("description", "")
        self._code = skill_def.get("code", "")
        self._fn: Any = None
        self._compile()

    def _compile(self) -> None:
        if not self._code:
            return
        try:
            namespace: dict = {}
            exec(compile(self._code, "<agent_zero_skill>", "exec"), namespace)  # noqa: S102
            self._fn = namespace.get("execute")
        except Exception as exc:
            logger.error("[AGENT_ZERO] Failed to compile skill %s: %s", self.name, exc)

    async def run(self, args: dict, **context) -> ToolResult:
        if not self._fn:
            return ToolResult(self.name, False, error="Skill not compiled")
        try:
            result = await self._fn(args)
            return ToolResult(self.name, True, output=result)
        except Exception as exc:
            return ToolResult(self.name, False, error=str(exc))

    @classmethod
    def from_yaml(cls, yaml_dict: dict) -> "AgentZeroAdapter":
        """Create from a parsed Agent Zero YAML skill."""
        return cls(yaml_dict)
