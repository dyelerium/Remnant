"""HTTP tool — fetch/post with domain allow-list + size limits."""
from __future__ import annotations

import logging
from urllib.parse import urlparse
from typing import Optional

import httpx

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 1_000_000  # 1 MB


class HTTPTool(BaseTool):
    name = "http_client"
    description = "Make HTTP GET/POST requests to allowed domains."
    safety_flags = ["network"]

    def __init__(
        self,
        allowed_domains: Optional[list[str]] = None,
        timeout: float = 30.0,
    ) -> None:
        self._allowed: Optional[list[str]] = allowed_domains  # None = all allowed
        self._timeout = timeout

    async def run(self, args: dict, **context) -> ToolResult:
        url = args.get("url", "")
        method = args.get("method", "GET").upper()
        body = args.get("body")
        headers = args.get("headers", {})

        if not url:
            return ToolResult(self.name, False, error="No URL provided")

        if self._allowed is not None:
            host = urlparse(url).hostname or ""
            if not any(host == d or host.endswith(f".{d}") for d in self._allowed):
                return ToolResult(self.name, False, error=f"Domain {host!r} not in allow-list")

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                if method == "POST":
                    response = await client.post(url, json=body, headers=headers)
                else:
                    response = await client.get(url, headers=headers)

                content = response.content[:_MAX_RESPONSE_BYTES].decode(errors="replace")
                return ToolResult(
                    self.name,
                    True,
                    output={
                        "status_code": response.status_code,
                        "content": content,
                        "headers": dict(response.headers),
                    },
                )
        except Exception as exc:
            return ToolResult(self.name, False, error=str(exc))

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "enum": ["GET", "POST"]},
                    "body": {"type": "object"},
                    "headers": {"type": "object"},
                },
                "required": ["url"],
            },
        }
