"""Shell tool — whitelisted shell commands with timeout and output capture."""
from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED = [
    "ls", "cat", "echo", "pwd", "date", "whoami",
    "find", "grep", "wc", "head", "tail", "sort", "uniq",
    "python3", "python", "node", "git",
]


class ShellTool(BaseTool):
    name = "shell"
    description = "Execute a whitelisted shell command with timeout."
    safety_flags = ["requires_confirmation"]

    def __init__(
        self,
        allowed_commands: Optional[list[str]] = None,
        timeout: float = 30.0,
        working_dir: str = "/tmp/remnant",
    ) -> None:
        self._allowed = allowed_commands or _DEFAULT_ALLOWED
        self._timeout = timeout
        self._working_dir = working_dir

    async def run(self, args: dict, **context) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult(self.name, False, error="No command provided")

        # Check first token against allowlist
        tokens = shlex.split(command)
        if not tokens or tokens[0] not in self._allowed:
            return ToolResult(
                self.name,
                False,
                error=f"Command {tokens[0]!r} not in allowlist",
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
            return ToolResult(
                self.name,
                proc.returncode == 0,
                output={
                    "stdout": stdout.decode(errors="replace").strip(),
                    "stderr": stderr.decode(errors="replace").strip(),
                    "returncode": proc.returncode,
                },
            )
        except asyncio.TimeoutError:
            return ToolResult(self.name, False, error=f"Timeout after {self._timeout}s")
        except Exception as exc:
            return ToolResult(self.name, False, error=str(exc))

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command to run"}},
                "required": ["command"],
            },
        }
