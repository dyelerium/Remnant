"""Planning wizard — interactive Q&A project setup + per-request task decomposition."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SubTask:
    """A decomposed sub-task for parallel/sequential execution."""
    task_id: str
    description: str
    agent_type: str = "default"     # From agents.yaml
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class Plan:
    """A decomposed plan for a user request."""
    request: str
    project_id: Optional[str]
    tasks: list[SubTask] = field(default_factory=list)
    reasoning: str = ""
    estimated_tokens: int = 0


class Planner:
    """
    Planning wizard: generates project definitions and per-request task plans.
    """

    def __init__(self, llm_client, config: dict) -> None:
        self.llm = llm_client
        self.config = config
        self._projects_cfg = config.get("planning_wizard", {})

    # ------------------------------------------------------------------
    # Per-request task decomposition
    # ------------------------------------------------------------------

    def decompose(
        self,
        message: str,
        memory_context: str = "",
        project_id: Optional[str] = None,
        available_agents: Optional[list[str]] = None,
    ) -> Plan:
        """
        Decompose a user message into sub-tasks via LLM reasoning.

        Returns a Plan with ordered SubTask list.
        """
        agents = available_agents or ["default", "researcher", "coder"]
        agents_str = ", ".join(agents)

        prompt = (
            f"Decompose into subtasks. JSON array only — no explanation.\n"
            f"Request: {message}\n"
            f"Agents: {agents_str}\n"
            f"Context: {memory_context[:300] if memory_context else 'none'}\n"
            f'Schema: [{{"task_id":"t1","description":"...","agent_type":"default","tools":[],"depends_on":[]}}]\n'
            f"One task if simple."
        )

        try:
            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                use_case="planning",
                project_id=project_id,
                max_tokens=1000,
                temperature=0.3,
            )
            tasks = self._parse_tasks(response.get("content", ""))
            return Plan(
                request=message,
                project_id=project_id,
                tasks=tasks,
                reasoning=response.get("content", ""),
                estimated_tokens=response.get("tokens_out", 0),
            )
        except Exception as exc:
            logger.warning("[PLANNER] LLM decomposition failed (%s) — single task fallback", exc)
            return Plan(
                request=message,
                project_id=project_id,
                tasks=[
                    SubTask(
                        task_id="t1",
                        description=message,
                        agent_type=self._pick_agent_type(message),
                    )
                ],
            )

    # ------------------------------------------------------------------
    # Interactive project planning wizard
    # ------------------------------------------------------------------

    async def run_wizard(
        self, questions: list[dict], answer_callback
    ) -> dict:
        """
        Run the interactive planning wizard.

        Args:
            questions:       List of question dicts from projects.yaml.
            answer_callback: Async function(question_id, prompt) → str answer.

        Returns:
            Dict of { question_id: answer }.
        """
        answers: dict[str, Any] = {}

        for q in questions:
            qid = q["id"]
            prompt = q["prompt"]
            default = q.get("default")
            qtype = q.get("type", "string")
            choices = q.get("choices")

            raw = await answer_callback(qid, prompt, choices, default)

            if not raw and default is not None:
                raw = str(default)

            # Type coercion
            if qtype == "float":
                try:
                    answers[qid] = float(raw)
                except ValueError:
                    answers[qid] = float(default or 0.0)
            elif qtype == "bool":
                answers[qid] = str(raw).lower() in ("y", "yes", "true", "1")
            elif qtype == "choice" and choices:
                answers[qid] = raw if raw in choices else (default or choices[0])
            else:
                answers[qid] = raw or ""

        return answers

    def build_project_from_wizard(
        self, wizard_answers: dict, template_cfg: dict
    ) -> dict:
        """Convert wizard answers into a project definition dict."""
        project_name = wizard_answers.get("project_name", "Unnamed Project")
        template = wizard_answers.get("template", "default")
        tmpl = template_cfg.get(template, template_cfg.get("default", {}))

        project = {
            "project_id": project_name.lower().replace(" ", "_"),
            "name": project_name,
            "description": wizard_answers.get("description", ""),
            "template": template,
            "memory_scopes": tmpl.get("memory_scopes", ["project", "global"]),
            "default_agent": tmpl.get("default_agent", "default"),
            "working_dir": wizard_answers.get("working_dir", ""),
            "budget_usd_daily": wizard_answers.get("budget_usd_daily", 2.0),
            "enable_mcp": wizard_answers.get("enable_mcp", False),
        }
        return project

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_agent_type(description: str) -> str:
        """Keyword-based agent type selection from description text."""
        d = description.lower()
        coder_kw = ("code", "script", "implement", "debug", "write function", "refactor", "fix bug")
        researcher_kw = ("search", "find", "research", "summarize", "look up", "browse", "fetch")
        if any(k in d for k in coder_kw):
            return "coder"
        if any(k in d for k in researcher_kw):
            return "researcher"
        return "default"

    @staticmethod
    def _parse_tasks(content: str) -> list[SubTask]:
        """Parse LLM JSON output into SubTask list."""
        import json, re

        # Strip <think>...</think> blocks (reasoning models like stepfun, qwen)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        # Extract JSON array from potentially wrapped response
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return []

        try:
            raw_tasks = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("[PLANNER] Could not parse task JSON")
            return []

        tasks = []
        for i, t in enumerate(raw_tasks):
            tasks.append(
                SubTask(
                    task_id=str(t.get("task_id", f"t{i+1}")),
                    description=str(t.get("description", "")),
                    agent_type=str(t.get("agent_type", "default")),
                    tools=list(t.get("tools", [])),
                    depends_on=list(t.get("depends_on", [])),
                )
            )
        return tasks
