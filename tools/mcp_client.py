"""MCP protocol client — SSE transport, connects to Claude Code MCP endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Generic MCP (Model Context Protocol) client over HTTP/SSE.

    Connects to a Remnant or Claude Code MCP server and invokes tools.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        """
        Invoke a remote MCP tool via HTTP POST.

        Returns parsed tool result dict.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }

        try:
            response = await self._client.post(
                f"{self._base_url}/mcp",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")

            return data.get("result", {})

        except httpx.HTTPError as exc:
            logger.error("[MCP] HTTP error calling %s: %s", tool_name, exc)
            raise

    async def list_tools(self) -> list[dict]:
        """List available tools on the MCP server."""
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
        response = await self._client.post(f"{self._base_url}/mcp", json=payload)
        response.raise_for_status()
        return response.json().get("result", {}).get("tools", [])

    # ------------------------------------------------------------------
    # SSE streaming
    # ------------------------------------------------------------------

    async def stream_tool(
        self, tool_name: str, args: dict
    ) -> AsyncIterator[dict]:
        """Stream tool results via SSE."""
        url = f"{self._base_url}/mcp/stream"
        payload = {"name": tool_name, "arguments": args}

        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        return
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        yield {"text": raw}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            r = await self._client.get(f"{self._base_url}/health", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
