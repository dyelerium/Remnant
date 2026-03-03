"""Schedule tool — schedule a task to execute after a delay and push results proactively."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_DELAY_SECONDS = 86400  # 24 hours


class ScheduleTool(BaseTool):
    """Schedule a task to run after a delay and push the result back to the user.

    The agent uses this when the user says things like:
      - "tell me the weather in 5 minutes"
      - "remind me in 10 minutes to check X"
      - "in half an hour, fetch the latest news"

    After the delay, the task string is executed through the orchestrator and the
    full response is broadcast to all connected WebSocket clients as a proactive
    message — it appears as a new agent bubble without any user input.
    """

    name = "schedule"
    description = (
        "Schedule a task to run after a delay and push results back to the user proactively. "
        "Use when the user says 'in X minutes', 'tell me in N minutes', 'remind me', "
        "'check back later', 'notify me when'. "
        "The task runs automatically and the result appears in the chat without the user asking again. "
        "Args: delay_seconds (number), task (what to execute when timer fires)"
    )
    safety_flags = []

    def __init__(self, scheduler) -> None:
        self._scheduler = scheduler

    async def run(self, args: dict, **context) -> ToolResult:
        # Parse delay
        try:
            delay_seconds = float(args.get("delay_seconds", 0))
        except (TypeError, ValueError):
            return ToolResult(self.name, False, error="delay_seconds must be a number")

        task = str(args.get("task", "")).strip()
        channel = str(args.get("channel") or context.get("channel") or "websocket")
        session_id = str(context.get("session_id") or "default")

        if not task:
            return ToolResult(
                self.name, False,
                error="task is required — describe what to do when the timer fires",
            )
        if delay_seconds <= 0:
            return ToolResult(self.name, False, error="delay_seconds must be > 0")
        if delay_seconds > _MAX_DELAY_SECONDS:
            return ToolResult(
                self.name, False,
                error=f"delay_seconds cannot exceed {_MAX_DELAY_SECONDS} (24 hours)",
            )

        job_id = self._scheduler.schedule_once(delay_seconds, task, session_id, channel)
        fire_at = datetime.now() + timedelta(seconds=delay_seconds)

        minutes = int(delay_seconds // 60)
        seconds_rem = int(delay_seconds % 60)
        if minutes and seconds_rem:
            human_delay = f"{minutes}m {seconds_rem}s"
        elif minutes:
            human_delay = f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            human_delay = f"{seconds_rem}s"

        return ToolResult(
            self.name, True,
            output={
                "status": "scheduled",
                "job_id": job_id,
                "task": task,
                "delay_seconds": delay_seconds,
                "fires_at": fire_at.strftime("%H:%M:%S"),
                "message": (
                    f"Scheduled — will execute '{task}' in {human_delay} "
                    f"({fire_at.strftime('%H:%M:%S')}) and push results back to this chat."
                ),
            },
        )

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_seconds": {
                        "type": "number",
                        "description": "How many seconds to wait before executing the task (e.g. 300 = 5 minutes)",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "What to do when the timer fires — a full instruction like "
                            "'fetch weather for Cluj-Napoca using Open-Meteo and report it' "
                            "or 'remind the user about their 6pm meeting'"
                        ),
                    },
                    "channel": {
                        "type": "string",
                        "description": "Channel to push results to (default: websocket). Usually omit this.",
                    },
                },
                "required": ["delay_seconds", "task"],
            },
        }
