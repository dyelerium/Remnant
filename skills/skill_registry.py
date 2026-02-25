"""Skill registry — scan *.yml skill files, validate schema, expose list/invoke API."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = {"name", "description", "tool"}


class SkillRegistry:
    """Load and expose Remnant skills from YAML definition files."""

    def __init__(self, skills_dir: str | Path = "skills") -> None:
        self._dir = Path(skills_dir)
        self._skills: dict[str, dict] = {}

    def load(self) -> int:
        """Scan skills directory and load all *.yml definitions. Returns count."""
        self._skills.clear()
        count = 0
        for yml_file in self._dir.glob("**/*.yml"):
            try:
                with open(yml_file, encoding="utf-8") as fh:
                    skill = yaml.safe_load(fh)
                if not skill or not isinstance(skill, dict):
                    continue
                missing = _REQUIRED_FIELDS - set(skill.keys())
                if missing:
                    logger.warning("[SKILLS] %s missing fields: %s", yml_file.name, missing)
                    continue
                self._skills[skill["name"]] = {**skill, "_path": str(yml_file)}
                count += 1
            except Exception as exc:
                logger.error("[SKILLS] Failed to load %s: %s", yml_file, exc)
        logger.info("[SKILLS] Loaded %d skills from %s", count, self._dir)
        return count

    def list(self, tag: Optional[str] = None) -> list[dict]:
        """List all registered skills, optionally filtered by tag."""
        skills = list(self._skills.values())
        if tag:
            skills = [s for s in skills if tag in s.get("tags", [])]
        return [
            {
                "name": s["name"],
                "description": s["description"],
                "tool": s["tool"],
                "tags": s.get("tags", []),
            }
            for s in skills
        ]

    def get(self, name: str) -> Optional[dict]:
        return self._skills.get(name)

    async def invoke(
        self,
        skill_name: str,
        args: dict,
        tool_registry: dict,
        **context,
    ) -> dict:
        """
        Invoke a skill by name.

        Looks up the skill's backing tool and calls it with mapped args.
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return {"error": f"Skill {skill_name!r} not found"}

        tool_name = skill["tool"]
        tool = tool_registry.get(tool_name)
        if not tool:
            return {"error": f"Tool {tool_name!r} not found for skill {skill_name!r}"}

        # Apply arg mapping from skill definition
        mapped_args = {**args}
        for src, dst in skill.get("arg_map", {}).items():
            if src in args:
                mapped_args[dst] = mapped_args.pop(src)

        # code_template: substitute args into embedded code, then run via code_exec
        if "code_template" in skill:
            try:
                mapped_args["code"] = skill["code_template"].format(**{**args, **mapped_args})
            except KeyError as exc:
                return {"error": f"Missing required arg for code_template: {exc}"}
            mapped_args.setdefault("language", skill.get("language", "python"))

        result = await tool(mapped_args, **context)
        return result.to_dict()
