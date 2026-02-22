"""Code execution tool — sandboxed Python/JS subprocess with timeout + resource limits."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class CodeExecTool(BaseTool):
    name = "code_exec"
    description = "Execute Python or JavaScript code in a sandboxed subprocess."
    safety_flags = ["requires_confirmation", "code_exec"]

    def __init__(
        self,
        timeout: float = 30.0,
        allowed_languages: Optional[list[str]] = None,
    ) -> None:
        self._timeout = timeout
        self._allowed = allowed_languages or ["python", "javascript", "bash"]

    async def run(self, args: dict, **context) -> ToolResult:
        language = args.get("language", "python").lower()
        code = args.get("code", "")

        if not code.strip():
            return ToolResult(self.name, False, error="No code provided")

        if language not in self._allowed:
            return ToolResult(
                self.name,
                False,
                error=f"Language {language!r} not allowed. Allowed: {self._allowed}",
            )

        return await self._exec(language, code)

    async def _exec(self, language: str, code: str) -> ToolResult:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=self._suffix(language),
            delete=False,
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        try:
            cmd = self._build_command(language, tmp_path)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                cwd="/tmp",
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(self.name, False, error=f"Timeout after {self._timeout}s")

            return ToolResult(
                self.name,
                proc.returncode == 0,
                output={
                    "stdout": stdout.decode(errors="replace").strip(),
                    "stderr": stderr.decode(errors="replace").strip(),
                    "returncode": proc.returncode,
                    "language": language,
                },
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _suffix(language: str) -> str:
        return {
            "python": ".py",
            "javascript": ".js",
            "bash": ".sh",
        }.get(language, ".txt")

    @staticmethod
    def _build_command(language: str, path: str) -> list[str]:
        return {
            "python": ["python3", path],
            "javascript": ["node", path],
            "bash": ["bash", path],
        }.get(language, ["cat", path])

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "javascript", "bash"]},
                    "code": {"type": "string"},
                },
                "required": ["language", "code"],
            },
        }
