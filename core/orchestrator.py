"""Conductor/Orchestrator — receives message → invokes Planner → spawns lanes."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from .agent_graph import AgentGraph, AgentNode, AgentEdge, EdgeType, NodeStatus
from .lane_manager import LaneManager, LaneMessage, LanePriority

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Conductor: entry point for all incoming messages.

    Responsibilities:
    1. Classify the request (complexity / agent type needed).
    2. Invoke Planner to decompose into sub-tasks.
    3. Spawn/reuse lanes per sub-task.
    4. Aggregate results and stream back.
    """

    def __init__(
        self,
        planner,            # core.planner.Planner
        lane_manager: LaneManager,
        agent_graph: AgentGraph,
        runtime,            # core.runtime.AgentRuntime (injected at startup)
        config: dict,
    ) -> None:
        self.planner = planner
        self.lanes = lane_manager
        self.graph = agent_graph
        self.runtime = runtime
        self.config = config
        self._agents_cfg: dict = config.get("agents", {})
        self._routing_cfg: dict = config.get("routing", {})

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def handle(
        self,
        message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "websocket",
        memory_context: str = "",
    ) -> AsyncIterator[str]:
        """
        Process an incoming message.
        Yields response chunks as they are generated (streaming).
        """
        session_id = session_id or str(uuid.uuid4())

        # Create a conductor node
        conductor = AgentNode(
            name="conductor",
            agent_type="conductor",
            project_id=project_id,
            depth=0,
        )
        self.graph.add_node(conductor)

        # Decompose into sub-tasks
        plan = self.planner.decompose(
            message,
            memory_context=memory_context,
            project_id=project_id,
        )

        logger.info(
            "[ORCHESTRATOR] session=%s tasks=%d project=%s",
            session_id[:8],
            len(plan.tasks),
            project_id,
        )

        # For simple single-task requests — run directly
        if len(plan.tasks) == 1:
            task = plan.tasks[0]
            agent_node = self._create_agent_node(task, conductor, project_id)
            self.graph.add_node(agent_node)
            self.graph.add_edge(
                AgentEdge(
                    source_id=conductor.agent_id,
                    target_id=agent_node.agent_id,
                    edge_type=EdgeType.DELEGATION,
                    task=task.description,
                )
            )

            lane = self.lanes.create_lane(
                priority=LanePriority.FOREGROUND,
                project_id=project_id,
            )
            await self.lanes.start_lane_worker(lane.lane_id)

            async for chunk in self.runtime.run_stream(
                message=message,
                agent_node=agent_node,
                project_id=project_id,
                session_id=session_id,
                channel=channel,
            ):
                yield chunk

        else:
            # Multi-task: run in parallel background lanes, aggregate
            results: dict[str, str] = {}
            tasks_done = asyncio.Event()
            remaining = [len(plan.tasks)]

            async def run_task(sub_task):
                agent_node = self._create_agent_node(sub_task, conductor, project_id)
                self.graph.add_node(agent_node)
                output_parts = []
                async for chunk in self.runtime.run_stream(
                    message=sub_task.description,
                    agent_node=agent_node,
                    project_id=project_id,
                    session_id=session_id,
                    channel=channel,
                ):
                    output_parts.append(chunk)
                results[sub_task.task_id] = "".join(output_parts)
                remaining[0] -= 1
                if remaining[0] == 0:
                    tasks_done.set()

            # Launch all tasks concurrently
            aws = [run_task(t) for t in plan.tasks]
            asyncio.gather(*aws)

            # Yield a planning summary
            yield f"[Planning {len(plan.tasks)} parallel tasks…]\n\n"
            await tasks_done.wait()

            # Yield aggregated results
            for task in plan.tasks:
                result = results.get(task.task_id, "")
                if result:
                    yield f"**{task.description}**\n{result}\n\n"

        # Cleanup conductor node
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
