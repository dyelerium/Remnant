"""Structured logging setup using structlog — per-lane/agent context vars."""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Optional

import structlog

# Context vars for per-request correlation
_ctx_lane: ContextVar[str] = ContextVar("lane_id", default="")
_ctx_agent: ContextVar[str] = ContextVar("agent_id", default="")
_ctx_project: ContextVar[str] = ContextVar("project_id", default="")
_ctx_request: ContextVar[str] = ContextVar("request_id", default="")


def set_logging_context(
    lane_id: str = "",
    agent_id: str = "",
    project_id: str = "",
    request_id: str = "",
) -> None:
    """Set per-request context variables used in structured log output."""
    _ctx_lane.set(lane_id)
    _ctx_agent.set(agent_id)
    _ctx_project.set(project_id)
    _ctx_request.set(request_id)


def _add_context(logger, method, event_dict):
    """Structlog processor: inject context vars into every log event."""
    event_dict["lane"] = _ctx_lane.get() or None
    event_dict["agent"] = _ctx_agent.get() or None
    event_dict["project"] = _ctx_project.get() or None
    event_dict["request_id"] = _ctx_request.get() or None
    # Strip None values
    event_dict = {k: v for k, v in event_dict.items() if v is not None}
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """
    Configure structlog + stdlib logging.

    Args:
        level:     Log level string ("DEBUG", "INFO", "WARNING", …)
        json_logs: Output JSON lines (for production log aggregation).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_context,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
    ]

    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib root logger to route through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Quieten noisy libraries
    for noisy in ("httpx", "httpcore", "redis", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
