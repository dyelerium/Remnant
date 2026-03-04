"""Web search tool — DuckDuckGo instant answer API with image extraction."""
from __future__ import annotations

import json
import logging
import urllib.parse

import httpx

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DDG_API = "https://api.duckduckgo.com/"
_DDG_BASE = "https://duckduckgo.com"


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for information, images, and facts. Returns abstract, image URL, and related links."
    safety_flags = ["network"]

    schema_hint = {
        "description": "Search the web. Returns summary text, a direct image URL (embed as markdown ![](url)), and related links.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
            },
            "required": ["query"],
        },
    }

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def run(self, args: dict, **context) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(self.name, False, error="No query provided")

        url = (
            f"{_DDG_API}?q={urllib.parse.quote_plus(query)}"
            "&format=json&no_html=1&skip_disambig=1"
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Remnant/1.0"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return ToolResult(self.name, False, error=f"Search failed: {exc}")

        result: dict = {}

        # Abstract / heading
        heading = data.get("Heading", "")
        abstract = data.get("AbstractText", "")
        abstract_url = data.get("AbstractURL", "")
        if heading:
            result["heading"] = heading
        if abstract:
            result["abstract"] = abstract
        if abstract_url:
            result["source"] = abstract_url

        # Image — DDG returns relative path like /i/abc123.jpg
        image = data.get("Image", "")
        if image:
            if image.startswith("/"):
                image = _DDG_BASE + image
            result["image"] = image

        # Related topics (top 5 text results)
        related = []
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                entry = {"text": item["Text"][:200]}
                if item.get("FirstURL"):
                    entry["url"] = item["FirstURL"]
                if item.get("Icon", {}).get("URL"):
                    icon = item["Icon"]["URL"]
                    if icon.startswith("/"):
                        icon = _DDG_BASE + icon
                    entry["icon"] = icon
                related.append(entry)
        if related:
            result["related"] = related

        if not result:
            result = {"message": f"No results found for: {query}"}

        return ToolResult(self.name, True, output=result)
