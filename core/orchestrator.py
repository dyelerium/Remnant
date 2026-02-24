"""Conductor/Orchestrator — receives message → invokes Planner → spawns lanes."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from .agent_graph import AgentGraph, AgentNode, AgentEdge, EdgeType, NodeStatus
from .lane_manager import LaneManager, LaneMessage, LanePriority
from .planner import SubTask

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Conductor: entry point for all incoming messages.

    For conversational chat, always runs as a single-task direct pass.
    LLM-based decomposition is available via decompose() for explicit use.
    """

    def __init__(
        self,
        planner,            # core.planner.Planner
        lane_manager: LaneManager,
        agent_graph: AgentGraph,
        runtime,            # core.runtime.AgentRuntime
        config: dict,
    ) -> None:
        self.planner = planner
        self.lanes = lane_manager
        self.graph = agent_graph
        self.runtime = runtime
        self.config = config
        self._agents_cfg: dict = config.get("agents", {})

    # ------------------------------------------------------------------
    # Main entry — always single-task for chat (no blocking LLM planning)
    # ------------------------------------------------------------------

    async def handle(
        self,
        message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "websocket",
        memory_context: str = "",
        cancel_event: Optional[asyncio.Event] = None,
        images: Optional[list] = None,
        budget_mode: bool = False,
    ) -> AsyncIterator[str]:
        """
        Process an incoming message, streaming response chunks.
        Runs as a single task directly — no blocking LLM planning call.
        """
        session_id = session_id or str(uuid.uuid4())

        conductor = AgentNode(
            name="conductor",
            agent_type="conductor",
            project_id=project_id,
            depth=0,
        )
        self.graph.add_node(conductor)

        task = SubTask(task_id="t1", description=message, agent_type="default")
        agent_node = self._create_agent_node(task, conductor, project_id)
        self.graph.add_node(agent_node)
        self.graph.add_edge(
            AgentEdge(
                source_id=conductor.agent_id,
                target_id=agent_node.agent_id,
                edge_type=EdgeType.DELEGATION,
                task=message,
            )
        )

        lane = self.lanes.create_lane(
            priority=LanePriority.FOREGROUND,
            project_id=project_id,
        )
        await self.lanes.start_lane_worker(lane.lane_id)

        logger.info(
            "[ORCHESTRATOR] session=%s project=%s",
            session_id[:8],
            project_id,
        )

        async for chunk in self.runtime.run_stream(
            message=message,
            agent_node=agent_node,
            project_id=project_id,
            session_id=session_id,
            channel=channel,
            cancel_event=cancel_event,
            images=images,
            budget_mode=budget_mode,
        ):
            yield chunk

        self.graph.update_status(conductor.agent_id, NodeStatus.DONE)

    # ------------------------------------------------------------------
    # Multi-task decomposition (explicit, non-blocking)
    # ------------------------------------------------------------------

    async def decompose_and_run(
        self,
        message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """LLM-based decomposition + parallel execution. Non-blocking."""
        session_id = session_id or str(uuid.uuid4())

        conductor = AgentNode(
            name="conductor",
            agent_type="conductor",
            project_id=project_id,
            depth=0,
        )
        self.graph.add_node(conductor)

        # Run sync planner in executor so we don't block the event loop
        loop = asyncio.get_event_loop()
        plan = await loop.run_in_executor(
            None,
            lambda: self.planner.decompose(message, project_id=project_id),
        )

        tasks = plan.tasks or [SubTask(task_id="t1", description=message, agent_type="default")]

        if len(tasks) == 1:
            async for chunk in self.handle(message, project_id, session_id):
                yield chunk
            return

        yield f"[Planning {len(tasks)} parallel tasks…]\n\n"
        results: dict[str, str] = {}

        async def run_task(sub_task):
            node = self._create_agent_node(sub_task, conductor, project_id)
            self.graph.add_node(node)
            parts = []
            async for chunk in self.runtime.run_stream(
                message=sub_task.description,
                agent_node=node,
                project_id=project_id,
                session_id=session_id,
                channel="api",
            ):
                parts.append(chunk)
            results[sub_task.task_id] = "".join(parts)

        await asyncio.gather(*[run_task(t) for t in tasks])

        for task in tasks:
            result = results.get(task.task_id, "")
            if result:
                yield f"**{task.description}**\n{result}\n\n"

        self.graph.update_status(conductor.agent_id, NodeStatus.DONE)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_agent_node(self, sub_task, parent: AgentNode, project_id: Optional[str]) -> AgentNode:
        agent_cfg = self._agents_cfg.get(sub_task.agent_type, self._agents_cfg.get("default", {}))
        return AgentNode(
            name=agent_cfg.get("name", sub_task.agent_type),
            agent_type=sub_task.agent_type,
            project_id=project_id,
            depth=parent.depth + 1,
            max_depth=agent_cfg.get("max_depth", 3),
        )
