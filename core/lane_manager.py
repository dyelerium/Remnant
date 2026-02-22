"""Lane manager — asyncio.Queue per lane, foreground + background workers."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


class LanePriority(str, Enum):
    FOREGROUND = "foreground"   # Interactive, user-facing
    BACKGROUND = "background"   # Long-running, non-blocking


@dataclass
class LaneMessage:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: Any = None
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    channel: str = "websocket"
    metadata: dict = field(default_factory=dict)


@dataclass
class Lane:
    lane_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: LanePriority = LanePriority.FOREGROUND
    project_id: Optional[str] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker_task: Optional[asyncio.Task] = None
    active: bool = True
    processed: int = 0


class LaneManager:
    """
    Manages asyncio lanes for the Remnant agent runtime.

    Each lane has its own message queue and worker coroutine.
    The conductor creates lanes per session/project.
    """

    def __init__(self, handler: Callable[[LaneMessage, str], Coroutine]) -> None:
        """
        Args:
            handler: Coroutine called with (message, lane_id) for each message.
        """
        self._handler = handler
        self._lanes: dict[str, Lane] = {}

    # ------------------------------------------------------------------
    # Lane lifecycle
    # ------------------------------------------------------------------

    def create_lane(
        self,
        priority: LanePriority = LanePriority.FOREGROUND,
        project_id: Optional[str] = None,
        lane_id: Optional[str] = None,
    ) -> Lane:
        """Create and register a new lane. Worker is started via start_workers()."""
        lane = Lane(
            lane_id=lane_id or str(uuid.uuid4()),
            priority=priority,
            project_id=project_id,
        )
        self._lanes[lane.lane_id] = lane
        logger.debug("Created lane %s (priority=%s)", lane.lane_id, priority.value)
        return lane

    async def start_workers(self) -> None:
        """Start worker tasks for all registered lanes."""
        for lane in self._lanes.values():
            if lane.worker_task is None or lane.worker_task.done():
                lane.worker_task = asyncio.create_task(
                    self._worker(lane), name=f"lane-{lane.lane_id[:8]}"
                )

    async def start_lane_worker(self, lane_id: str) -> None:
        """Start worker for a specific lane."""
        lane = self._lanes.get(lane_id)
        if lane and (lane.worker_task is None or lane.worker_task.done()):
            lane.worker_task = asyncio.create_task(
                self._worker(lane), name=f"lane-{lane.lane_id[:8]}"
            )

    async def stop_lane(self, lane_id: str) -> None:
        """Stop a lane gracefully."""
        lane = self._lanes.get(lane_id)
        if not lane:
            return
        lane.active = False
        if lane.worker_task and not lane.worker_task.done():
            lane.worker_task.cancel()
            try:
                await lane.worker_task
            except asyncio.CancelledError:
                pass
        del self._lanes[lane_id]
        logger.debug("Stopped lane %s", lane_id)

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, lane_id: str, message: LaneMessage) -> None:
        """Enqueue a message to a specific lane."""
        lane = self._lanes.get(lane_id)
        if not lane:
            raise KeyError(f"Lane {lane_id!r} not found")
        await lane.queue.put(message)

    def dispatch_nowait(self, lane_id: str, message: LaneMessage) -> None:
        """Non-blocking enqueue (raises QueueFull if queue is full)."""
        lane = self._lanes.get(lane_id)
        if not lane:
            raise KeyError(f"Lane {lane_id!r} not found")
        lane.queue.put_nowait(message)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            lid: {
                "priority": lane.priority.value,
                "project_id": lane.project_id,
                "queue_size": lane.queue.qsize(),
                "active": lane.active,
                "processed": lane.processed,
                "worker_alive": (
                    lane.worker_task is not None and not lane.worker_task.done()
                ),
            }
            for lid, lane in self._lanes.items()
        }

    # ------------------------------------------------------------------
    # Worker coroutine
    # ------------------------------------------------------------------

    async def _worker(self, lane: Lane) -> None:
        """Process messages from lane queue indefinitely."""
        logger.info("Lane worker started: %s", lane.lane_id)
        while lane.active:
            try:
                message = await asyncio.wait_for(lane.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._handler(message, lane.lane_id)
                lane.processed += 1
            except Exception as exc:
                logger.error(
                    "Lane %s handler error: %s", lane.lane_id, exc, exc_info=True
                )
            finally:
                lane.queue.task_done()

        logger.info("Lane worker stopped: %s", lane.lane_id)
