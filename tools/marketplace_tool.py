"""Marketplace tool — search and install skills/MCP servers from the curated index."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import httpx
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
        "Use when you lack a needed skill or the user gives you a URL/link to install. "
        "Actions: search (query=...), install (id=...), list (type=skill|mcp), "
        "analyze_url (url=...) to fetch+classify+security-scan any URL, "
        "install_from_content (content=..., approved=true) to install raw YAML after user approval."
    )
    safety_flags = ["filesystem"]

    def __init__(
        self,
        index_path: Optional[Path] = None,
        imported_dir: Optional[Path] = None,
    ) -> None:
        self._index_path = Path(index_path) if index_path else _INDEX_PATH
        self._imported_dir = Path(imported_dir) if imported_dir else _IMPORTED_DIR
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
        elif action == "analyze_url":
            return await self._analyze_url(args.get("url", ""))
        elif action == "install_from_content":
            return self._install_from_content(
                content=args.get("content", ""),
                approved=args.get("approved", False),
                name_override=args.get("name"),
            )
        else:
            return ToolResult(
                self.name, False,
                error=f"Unknown action {action!r}. Use: search, install, list, analyze_url, install_from_content",
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
    # URL analysis & content install
    # ------------------------------------------------------------------

    async def _analyze_url(self, url: str) -> ToolResult:
        if not url:
            return ToolResult(self.name, False, error="url is required for action=analyze_url")

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Remnant-Agent/1.0"})
            raw = resp.text[:60_000]
        except Exception as exc:
            return ToolResult(self.name, False, error=f"Failed to fetch {url!r}: {exc}")

        url_lower = url.lower()
        detected_type = "unknown"
        skill_content: Optional[str] = None
        title: Optional[str] = None
        description: Optional[str] = None
        install_instructions: Optional[str] = None

        # 1. Try parsing as bare YAML skill
        try:
            parsed = yaml.safe_load(raw)
            if isinstance(parsed, dict) and {"name", "description", "tool"} <= set(parsed.keys()):
                detected_type = "remnant_skill"
                skill_content = raw
                title = parsed.get("name", "Unknown Skill")
                description = parsed.get("description", "")
        except Exception:
            pass

        # 2. Extract YAML code blocks from HTML/Markdown
        if detected_type == "unknown":
            for block in re.findall(r"```ya?ml\s*(.*?)\s*```", raw, re.DOTALL):
                try:
                    parsed = yaml.safe_load(block)
                    if isinstance(parsed, dict) and {"name", "description", "tool"} <= set(parsed.keys()):
                        detected_type = "remnant_skill"
                        skill_content = block
                        title = parsed.get("name", "Embedded Skill")
                        description = parsed.get("description", "")
                        break
                except Exception:
                    pass

        # 3. GitHub repo — check for MCP server or README description
        if detected_type == "unknown" and "github.com" in url_lower:
            raw_lower = raw.lower()
            if "modelcontextprotocol" in raw_lower or "@modelcontextprotocol" in raw_lower:
                detected_type = "github_mcp"
                title = url.rstrip("/").split("/")[-1]
                description = f"MCP server repository at {url}"
                npm_match = re.search(r"npm install[^`\n]*?(@[\w\-@/]+)", raw)
                pkg = npm_match.group(1).strip() if npm_match else title
                install_instructions = (
                    f'Add to ~/.claude/claude_desktop_config.json:\n'
                    f'{{"mcpServers":{{"{ title }":{{"command":"npx","args":["-y","{pkg}"]}}}}}}'
                )
            else:
                detected_type = "github_repo"
                title = url.rstrip("/").split("/")[-1]
                page_title = re.search(r"<title>(.*?)</title>", raw, re.IGNORECASE)
                description = page_title.group(1).strip() if page_title else f"GitHub repository: {title}"

        # 4. npm package
        if detected_type == "unknown" and "npmjs.com" in url_lower:
            detected_type = "npm_package"
            title = url.rstrip("/").split("/")[-1]
            description = f"npm package: {title}"
            install_instructions = f"npm install {title}"

        # 5. Claude.com or other web pages
        if detected_type == "unknown":
            page_title = re.search(r"<title>(.*?)</title>", raw, re.IGNORECASE)
            title = page_title.group(1).strip() if page_title else url.split("/")[-1] or url
            detected_type = "web_page"
            description = f"Web page — no installable skill or MCP detected automatically."

        # Security scan
        security = self._security_scan(skill_content or raw[:10_000])
        install_ready = (skill_content is not None) and security["safe"]

        if not security["safe"]:
            hint = (
                f"SECURITY RISKS DETECTED ({security['high_risk_count']} HIGH). "
                "Do NOT install. Report risks to user."
            )
        elif install_ready:
            hint = (
                "Security check passed. Present this analysis to the user. "
                "On their approval call: marketplace(action=install_from_content, "
                "content=<install_content value>, approved=true)"
            )
        elif detected_type == "github_mcp":
            hint = "MCP server — present install_instructions to the user for manual setup in Claude Code config."
        else:
            hint = (
                "No auto-installable skill found. Describe what was found at the URL "
                "and suggest the user provide a direct YAML skill file URL."
            )

        return ToolResult(
            self.name, True,
            output={
                "status": "analysis_complete",
                "url": url,
                "detected_type": detected_type,
                "title": title,
                "description": description,
                "security": security,
                "install_ready": install_ready,
                "install_content": skill_content,
                "install_instructions": install_instructions,
                "approval_required": True,
                "hint": hint,
            },
        )

    def _install_from_content(
        self,
        content: str,
        approved: bool,
        name_override: Optional[str] = None,
    ) -> ToolResult:
        if not approved:
            return ToolResult(
                self.name, False,
                error=(
                    "Installation requires approved=true. "
                    "Present the analysis to the user first and wait for explicit approval."
                ),
            )
        if not content:
            return ToolResult(self.name, False, error="content is required for action=install_from_content")

        # Security scan (defense-in-depth — always scan before writing)
        security = self._security_scan(content)
        if not security["safe"]:
            high = [r["reason"] for r in security["risks"] if r["level"] == "HIGH"]
            return ToolResult(
                self.name, False,
                error=f"Security scan failed. HIGH risks: {', '.join(high)}",
            )

        try:
            skill = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            return ToolResult(self.name, False, error=f"Invalid YAML: {exc}")

        if not isinstance(skill, dict):
            return ToolResult(self.name, False, error="Content is not a valid skill dict")

        missing = {"name", "description", "tool"} - set(skill.keys())
        if missing:
            return ToolResult(self.name, False, error=f"Skill YAML missing required fields: {missing}")

        if name_override:
            skill["name"] = name_override

        skill_name = skill["name"]
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", skill_name)
        self._imported_dir.mkdir(parents=True, exist_ok=True)
        dest = self._imported_dir / f"{safe_name}.yml"

        try:
            with open(dest, "w", encoding="utf-8") as fh:
                yaml.dump(skill, fh, default_flow_style=False, allow_unicode=True)
        except OSError as exc:
            return ToolResult(self.name, False, error=f"Failed to write skill file: {exc}")

        count = None
        if self._skill_registry:
            try:
                count = self._skill_registry.load()
            except Exception as exc:
                logger.warning("[MARKETPLACE] Registry reload failed: %s", exc)

        return ToolResult(
            self.name, True,
            output={
                "status": "installed",
                "skill_name": skill_name,
                "path": str(dest),
                "skills_loaded": count,
                "message": f"Skill '{skill_name}' installed and ready to use.",
            },
        )

    def _security_scan(self, content: str) -> dict:
        """Scan skill YAML or raw content for security risks before install."""
        risks = []

        high_risk = [
            (r"rm\s+-rf", "Recursive file deletion"),
            (r"dd\s+if=", "Raw disk write"),
            (r"mkfs\.", "Filesystem format"),
            (r"curl\s+[^\n]+\|\s*(ba)?sh", "Remote code execution via curl|sh"),
            (r"wget\s+[^\n]+\|\s*(ba)?sh", "Remote code execution via wget|sh"),
            (r"base64\s+-d\s+[^\n]+\|", "Obfuscated command via base64"),
            (r"eval\(", "Dynamic code evaluation"),
            (r"exec\(", "Dynamic code execution"),
            (r"__import__\(", "Dynamic import"),
            (r"os\.system\(", "OS command execution"),
            (r"subprocess\.call\(", "Subprocess call"),
            (r"subprocess\.run\(", "Subprocess run"),
            (r"Popen\(", "Process spawn"),
            (r"(webhook\.site|requestbin\.|burpcollaborator|canarytokens)", "Known exfiltration endpoint"),
            (r"\.onion", "Tor hidden service"),
            (r"(?i)ignore.{0,30}previous.{0,30}instruction", "Prompt injection attempt"),
            (r"(?i)disregard.{0,30}system.{0,30}prompt", "System prompt override"),
        ]
        medium_risk = [
            (r"shutil\.rmtree", "Directory removal"),
            (r"os\.remove\(", "File deletion"),
            (r"open\([^)]+['\"]w['\"]", "File write"),
            (r"socket\.connect\(", "Direct socket connection"),
        ]

        for pattern, reason in high_risk:
            if re.search(pattern, content, re.IGNORECASE):
                risks.append({"level": "HIGH", "reason": reason})

        for pattern, reason in medium_risk:
            if re.search(pattern, content, re.IGNORECASE):
                risks.append({"level": "MEDIUM", "reason": reason})

        high_count = sum(1 for r in risks if r["level"] == "HIGH")
        return {
            "safe": high_count == 0,
            "risks": risks,
            "high_risk_count": high_count,
            "medium_risk_count": len(risks) - high_count,
        }

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
                        "enum": ["search", "install", "list", "analyze_url", "install_from_content"],
                        "description": (
                            "search=find by keyword; install=install by index id; list=browse all; "
                            "analyze_url=fetch+classify+security-scan a URL; "
                            "install_from_content=install raw YAML skill (requires approved=true)"
                        ),
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
                    "url": {
                        "type": "string",
                        "description": "URL to fetch and analyze (for action=analyze_url)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Raw YAML skill content to install (for action=install_from_content)",
                    },
                    "approved": {
                        "type": "boolean",
                        "description": "Must be true — set only after user explicitly approves (for action=install_from_content)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional skill name override (for action=install_from_content)",
                    },
                },
                "required": ["action"],
            },
        }
