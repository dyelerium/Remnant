"""Filesystem tool — controlled read/write within allowed paths."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class FilesystemTool(BaseTool):
    name = "filesystem"
    description = "Read/write files within allowed directory paths."
    safety_flags = ["filesystem"]

    def __init__(self, allowed_paths: Optional[list[str]] = None) -> None:
        self._allowed = [
            Path(p).resolve() for p in (allowed_paths or ["./workspace", "/tmp/remnant"])
        ]

    async def run(self, args: dict, **context) -> ToolResult:
        operation = args.get("operation", "read")
        path_str = args.get("path", "")

        if not path_str:
            return ToolResult(self.name, False, error="No path provided")

        target = Path(path_str).resolve()

        if not self._is_allowed(target):
            return ToolResult(
                self.name,
                False,
                error=f"Path {path_str!r} is outside allowed directories",
            )

        if operation == "read":
            return await self._read(target)
        elif operation == "write":
            return await self._write(target, args.get("content", ""))
        elif operation == "list":
            return await self._list(target)
        elif operation == "delete":
            return await self._delete(target)
        else:
            return ToolResult(self.name, False, error=f"Unknown operation: {operation!r}")

    async def _read(self, path: Path) -> ToolResult:
        if not path.exists():
            return ToolResult(self.name, False, error=f"File not found: {path}")
        try:
            content = path.read_text(encoding="utf-8")
            return ToolResult(self.name, True, output={"path": str(path), "content": content})
        except Exception as exc:
            return ToolResult(self.name, False, error=str(exc))

    async def _write(self, path: Path, content: str) -> ToolResult:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(self.name, True, output={"path": str(path), "bytes": len(content)})
        except Exception as exc:
            return ToolResult(self.name, False, error=str(exc))

    async def _list(self, path: Path) -> ToolResult:
        if not path.is_dir():
            return ToolResult(self.name, False, error=f"Not a directory: {path}")
        entries = [str(e.relative_to(path)) for e in path.iterdir()]
        return ToolResult(self.name, True, output={"path": str(path), "entries": entries})

    async def _delete(self, path: Path) -> ToolResult:
        if not path.exists():
            return ToolResult(self.name, False, error=f"File not found: {path}")
        path.unlink()
        return ToolResult(self.name, True, output={"deleted": str(path)})

    def _is_allowed(self, target: Path) -> bool:
        return any(
            target == allowed or str(target).startswith(str(allowed) + "/")
            for allowed in self._allowed
        )

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["read", "write", "list", "delete"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["operation", "path"],
            },
        }
