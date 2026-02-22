"""BaseTool ABC — run(), schema_hint, safety_flags, lifecycle hooks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """Standardised tool output."""
    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """Abstract base class for all Remnant tools."""

    name: str = "base_tool"
    description: str = ""
    safety_flags: list[str] = []  # e.g. ["requires_confirmation", "network", "filesystem"]

    @abstractmethod
    async def run(self, args: dict, **context) -> ToolResult:
        """Execute the tool. Returns ToolResult."""

    @property
    def schema_hint(self) -> dict:
        """JSON schema hint for LLM tool-calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}},
        }

    async def before_run(self, args: dict, **context) -> dict:
        """Pre-execution hook. May modify args or raise to abort."""
        return args

    async def after_run(self, result: ToolResult, **context) -> ToolResult:
        """Post-execution hook. May modify or log result."""
        return result

    async def __call__(self, args: dict, **context) -> ToolResult:
        """Invoke before_run → run → after_run pipeline."""
        args = await self.before_run(args, **context)
        result = await self.run(args, **context)
        result = await self.after_run(result, **context)
        return result
