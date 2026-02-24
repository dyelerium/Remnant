"""DelegateTool — lets an agent spawn a subagent and wait for its response."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DELEGATE_TIMEOUT = 120  # max seconds to wait for a subagent response


class DelegateTool(BaseTool):
    """
    Spawn a subagent to handle a specific subtask and return its full response.

    The calling agent blocks until the subagent finishes (up to 120s).
    Max nesting depth is enforced by AgentNode.can_delegate() — typically 3 levels.

    Parameters:
        task        (str, required) — the task or question for the subagent
        agent_type  (str, optional) — "default" | "coder" | "researcher"
                    defaults to "default"
        context     (str, optional) — extra context to prepend to the task
    """

    name = "delegate"
    description = (
        "Delegate a subtask to a specialized subagent and wait for the result. "
        "Use this when you need to parallelize work, specialize (e.g. coding vs research), "
        "or offload a discrete sub-problem to a focused agent."
    )

    def __init__(self) -> None:
        self._runtime = None  # set via set_runtime() after AgentRuntime is constructed

    def set_runtime(self, runtime) -> None:
        """Wire the AgentRuntime reference (called from main.py after setup)."""
        self._runtime = runtime

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The specific task or question to delegate to the subagent.",
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["default", "coder", "researcher"],
                        "description": (
                            "Specialist type. 'coder' for code writing/debugging, "
                            "'researcher' for search/analysis, 'default' for general tasks."
                        ),
                        "default": "default",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional extra context to prepend to the task.",
                    },
                },
            },
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, args: dict, **context) -> ToolResult:
        if not self._runtime:
            return ToolResult(self.name, False, error="DelegateTool: runtime not wired")

        task: str = args.get("task", "").strip()
        if not task:
            return ToolResult(self.name, False, error="'task' is required")

        agent_type: str = args.get("agent_type", "default")
        extra_context: str = args.get("context", "")
        if extra_context:
            task = f"{extra_context}\n\n{task}"

        # Get parent node from call context
        parent_node = context.get("agent_node")

        # Depth guard
        if parent_node and not parent_node.can_delegate():
            return ToolResult(
                self.name, False,
                error=f"Max agent depth ({parent_node.max_depth}) reached — cannot delegate further",
            )

        # Build child AgentNode
        from core.agent_graph import AgentNode, AgentEdge, EdgeType
        child = AgentNode(
            name=agent_type,
            agent_type=agent_type,
            project_id=parent_node.project_id if parent_node else None,
            depth=(parent_node.depth + 1) if parent_node else 1,
            max_depth=(parent_node.max_depth) if parent_node else 3,
        )
        if parent_node:
            self._runtime.security  # touch to verify runtime is healthy
            # Record delegation edge in graph
            try:
                self._runtime.recorder  # another health check
            except Exception:
                pass

        logger.info(
            "[DELEGATE] Spawning subagent type=%s depth=%d task=%.80s",
            agent_type, child.depth, task,
        )

        # Run subagent with timeout
        parts: list[str] = []
        try:
            async with asyncio.timeout(_DELEGATE_TIMEOUT):
                async for chunk in self._runtime.run_stream(
                    message=task,
                    agent_node=child,
                    project_id=child.project_id,
                    session_id=f"delegate-{child.agent_id[:8]}",
                    channel="subagent",
                ):
                    # Strip internal markers for clean embedding in parent response
                    if not chunk.startswith("[GEN]") and not chunk.startswith("[EXE]"):
                        parts.append(chunk)
        except TimeoutError:
            return ToolResult(
                self.name, False,
                error=f"Subagent timed out after {_DELEGATE_TIMEOUT}s",
                output="".join(parts) or None,
            )
        except Exception as exc:
            logger.error("[DELEGATE] Subagent error: %s", exc)
            return ToolResult(self.name, False, error=str(exc), output="".join(parts) or None)

        response = "".join(parts).strip()
        logger.info("[DELEGATE] Subagent done: %d chars", len(response))
        return ToolResult(self.name, True, output=response)
