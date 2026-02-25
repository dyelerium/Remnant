"""Marketplace tool — search and install skills/MCP servers from the curated index."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_INDEX_PATH = Path("/app/skills/marketplace/index.yaml") if Path("/app/skills").exists() \
    else Path("skills/marketplace/index.yaml")
_IMPORTED_DIR = Path("/app/skills/imported") if Path("/app/skills").exists() \
    else Path("skills/imported")


class MarketplaceTool(BaseTool):
    """Search the Remnant marketplace and install skills or MCP servers.

    Actions
    -------
    search   — fuzzy search the index by keyword; returns matching entries
    install  — install a skill by id (writes YAML to skills/imported/ and reloads)
    list     — list all marketplace entries (optionally filtered by type or tag)
    """

    name = "marketplace"
    description = (
        "Search the Remnant skill/tool/MCP marketplace and install new capabilities. "
        "Use when you lack a needed skill. "
        "Actions: search (query=...), install (id=...), list (type=skill|mcp)"
    )
    safety_flags = ["filesystem"]

    def __init__(
        self,
        index_path: Optional[Path] = None,
        imported_dir: Optional[Path] = None,
    ) -> None:
        self._index_path = index_path or _INDEX_PATH
        self._imported_dir = imported_dir or _IMPORTED_DIR
        self._skill_registry = None  # wired post-construction via set_registry()
        self._index: list[dict] = []
        self._load_index()

    def set_registry(self, registry) -> None:
        """Wire the SkillRegistry after construction (avoids circular dependency)."""
        self._skill_registry = registry

    def _load_index(self) -> None:
        try:
            with open(self._index_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            self._index = data if isinstance(data, list) else []
            logger.info("[MARKETPLACE] Loaded %d entries from %s", len(self._index), self._index_path)
        except FileNotFoundError:
            logger.warning("[MARKETPLACE] Index not found at %s", self._index_path)
            self._index = []
        except Exception as exc:
            logger.error("[MARKETPLACE] Failed to load index: %s", exc)
            self._index = []

    async def run(self, args: dict, **context) -> ToolResult:
        action = args.get("action", "search")

        if action == "search":
            return self._search(args.get("query", ""))
        elif action == "install":
            return self._install(args.get("id", ""))
        elif action == "list":
            return self._list(
                type_filter=args.get("type"),
                tag_filter=args.get("tag"),
            )
        else:
            return ToolResult(
                self.name, False,
                error=f"Unknown action {action!r}. Use: search, install, list",
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _search(self, query: str) -> ToolResult:
        if not query:
            return ToolResult(self.name, False, error="query is required for action=search")

        q = query.lower()
        results = []
        for entry in self._index:
            score = 0
            text = " ".join([
                entry.get("id", ""),
                entry.get("title", ""),
                entry.get("description", ""),
                " ".join(entry.get("tags", [])),
            ]).lower()
            # Exact word match scores higher than substring
            for word in q.split():
                if word in text.split():
                    score += 2
                elif word in text:
                    score += 1
            if score > 0:
                results.append((score, entry))

        results.sort(key=lambda x: x[0], reverse=True)
        matched = [self._summarise(e) for _, e in results[:10]]

        return ToolResult(
            self.name, True,
            output={
                "query": query,
                "results": matched,
                "count": len(matched),
                "hint": "Use marketplace(action=install, id=ITEM_ID) to install a skill.",
            },
        )

    def _list(
        self,
        type_filter: Optional[str] = None,
        tag_filter: Optional[str] = None,
    ) -> ToolResult:
        entries = self._index
        if type_filter:
            entries = [e for e in entries if e.get("type") == type_filter]
        if tag_filter:
            entries = [e for e in entries if tag_filter in e.get("tags", [])]
        return ToolResult(
            self.name, True,
            output={"entries": [self._summarise(e) for e in entries], "count": len(entries)},
        )

    def _install(self, item_id: str) -> ToolResult:
        if not item_id:
            return ToolResult(self.name, False, error="id is required for action=install")

        entry = next((e for e in self._index if e.get("id") == item_id), None)
        if not entry:
            return ToolResult(
                self.name, False,
                error=f"Item {item_id!r} not found. Use marketplace(action=search, query=...) first.",
            )

        entry_type = entry.get("type", "skill")

        # Already installed — just inform
        if entry.get("status") == "installed":
            return ToolResult(
                self.name, True,
                output={
                    "status": "already_installed",
                    "id": item_id,
                    "message": f"{entry.get('title', item_id)} is already available.",
                },
            )

        # MCP / plugin — return instructions
        if entry_type in ("mcp", "plugin"):
            instructions = entry.get("install_instructions", "No instructions available.")
            return ToolResult(
                self.name, True,
                output={
                    "status": "instructions",
                    "id": item_id,
                    "type": entry_type,
                    "title": entry.get("title", item_id),
                    "instructions": instructions,
                    "requires": entry.get("requires", []),
                },
            )

        # Skill — write YAML and reload registry
        skill_yaml_text = entry.get("skill_yaml", "")
        if not skill_yaml_text:
            return ToolResult(
                self.name, False,
                error=f"No skill_yaml defined for {item_id!r}",
            )

        try:
            skill = yaml.safe_load(skill_yaml_text)
        except yaml.YAMLError as exc:
            return ToolResult(self.name, False, error=f"Invalid skill YAML: {exc}")

        import re
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", item_id)
        self._imported_dir.mkdir(parents=True, exist_ok=True)
        dest = self._imported_dir / f"{safe_name}.yml"

        try:
            with open(dest, "w", encoding="utf-8") as fh:
                yaml.dump(skill, fh, default_flow_style=False, allow_unicode=True)
        except OSError as exc:
            return ToolResult(self.name, False, error=f"Failed to write skill file: {exc}")

        # Hot-reload the skill registry if wired
        count = None
        if self._skill_registry:
            try:
                count = self._skill_registry.load()
            except Exception as exc:
                logger.warning("[MARKETPLACE] Registry reload failed: %s", exc)

        # Mark as installed in the in-memory index
        entry["status"] = "installed"

        return ToolResult(
            self.name, True,
            output={
                "status": "installed",
                "id": item_id,
                "skill_name": skill.get("name", item_id),
                "path": str(dest),
                "skills_loaded": count,
                "requires": entry.get("requires", []),
                "message": (
                    f"Skill '{skill.get('name', item_id)}' installed. "
                    + (f"Requires: {', '.join(entry.get('requires', []))}" if entry.get("requires") else "Ready to use.")
                ),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise(entry: dict) -> dict:
        return {
            "id": entry.get("id"),
            "type": entry.get("type", "skill"),
            "title": entry.get("title", entry.get("id")),
            "description": entry.get("description", ""),
            "tags": entry.get("tags", []),
            "status": entry.get("status", "available"),
            "requires": entry.get("requires", []),
        }

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "install", "list"],
                        "description": "search=find by keyword; install=install by id; list=browse all",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for action=search)",
                    },
                    "id": {
                        "type": "string",
                        "description": "Marketplace item ID (for action=install)",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["skill", "mcp", "plugin"],
                        "description": "Filter by type (for action=list)",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filter by tag (for action=list)",
                    },
                },
                "required": ["action"],
            },
        }
