"""n8n tool — trigger n8n workflows via HTTP webhook or n8n API."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class N8nTool(BaseTool):
    name = "n8n"
    description = "Trigger n8n automation workflows via webhook."
    safety_flags = ["network"]

    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def run(self, args: dict, **context) -> ToolResult:
        workflow_id = args.get("workflow_id") or args.get("webhook_path")
        payload = args.get("payload", {})
        use_webhook = args.get("use_webhook", True)

        if not workflow_id:
            return ToolResult(self.name, False, error="workflow_id or webhook_path required")

        headers = {}
        if self._api_key:
            headers["X-N8N-API-KEY"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if use_webhook:
                    url = f"{self._base_url}/webhook/{workflow_id}"
                    response = await client.post(url, json=payload, headers=headers)
                else:
                    url = f"{self._base_url}/api/v1/workflows/{workflow_id}/execute"
                    response = await client.post(
                        url,
                        json={"workflowData": payload},
                        headers=headers,
                    )

                return ToolResult(
                    self.name,
                    response.status_code < 400,
                    output={
                        "status_code": response.status_code,
                        "response": response.text[:2000],
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
                    "workflow_id": {"type": "string", "description": "Workflow ID or webhook path"},
                    "payload": {"type": "object", "description": "Data to send"},
                    "use_webhook": {"type": "boolean", "default": True},
                },
                "required": ["workflow_id"],
            },
        }
