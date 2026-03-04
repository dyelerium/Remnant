"""Web search tool — DuckDuckGo instant answer API + image search."""
from __future__ import annotations

import logging
import re
import urllib.parse

import httpx

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DDG_API = "https://api.duckduckgo.com/"
_DDG_BASE = "https://duckduckgo.com"

# Stock photo / paywalled sites that block hotlinking — images from these domains
# show as broken when embedded in markdown
_BLOCKED_IMAGE_DOMAINS = {
    "alamy.com",
    "gettyimages.com",
    "istockphoto.com",
    "shutterstock.com",
    "stock.adobe.com",
    "dreamstime.com",
    "depositphotos.com",
    "123rf.com",
    "bigstockphoto.com",
    "pond5.com",
}

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for information, images, and facts. "
        "Returns abstract, multiple image URLs, and related links."
    )
    safety_flags = ["network"]

    schema_hint = {
        "description": (
            "Search the web. Returns summary text, a list of direct image URLs "
            "(embed first as markdown ![](url)), and related links."
        ),
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

    async def _fetch_images(self, client: httpx.AsyncClient, query: str) -> list[str]:
        """Bing image search. Returns up to 5 direct image URLs."""
        try:
            q_enc = urllib.parse.quote_plus(query)
            r = await client.get(
                f"https://www.bing.com/images/search?q={q_enc}&form=HDRSC2&first=1",
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            # Bing encodes full image URLs as murl&quot;:&quot;{url}&quot;
            all_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', r.text)
            # Filter out stock photo sites that block hotlinking
            urls = [
                u for u in all_urls
                if not any(blocked in u for blocked in _BLOCKED_IMAGE_DOMAINS)
            ]
            return urls[:5] or all_urls[:5]  # fall back to all if everything filtered
        except Exception as exc:
            logger.debug("[WEB_SEARCH] Image search failed: %s", exc)
            return []

    async def run(self, args: dict, **context) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(self.name, False, error="No query provided")

        api_url = (
            f"{_DDG_API}?q={urllib.parse.quote_plus(query)}"
            "&format=json&no_html=1&skip_disambig=1"
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(api_url, headers={"User-Agent": "Remnant/1.0"})
                resp.raise_for_status()
                data = resp.json()
                images = await self._fetch_images(client, query)
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

        # Images — prefer DDG image search results; fall back to instant-answer thumbnail
        if images:
            result["images"] = images
            result["image"] = images[0]
        else:
            thumb = data.get("Image", "")
            if thumb:
                if thumb.startswith("/"):
                    thumb = _DDG_BASE + thumb
                result["image"] = thumb
                result["images"] = [thumb]

        # Related topics (top 5 text results)
        related = []
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                entry = {"text": item["Text"][:200]}
                if item.get("FirstURL"):
                    entry["url"] = item["FirstURL"]
                related.append(entry)
        if related:
            result["related"] = related

        if not result:
            result = {"message": f"No results found for: {query}"}

        return ToolResult(self.name, True, output=result)
