"""Reminder tool — persistent, Redis-backed reminders with natural language time parsing."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_HASH_PREFIX = "remnant:reminder:"
_PENDING_ZSET = "remnant:reminders:pending"
_USER_TZ = "Europe/Bucharest"

_RECURRENCE_DELTAS = {
    "hourly":   timedelta(hours=1),
    "daily":    timedelta(days=1),
    "weekly":   timedelta(weeks=1),
    # "weekdays" handled specially — skips Sat/Sun
}


class ReminderTool(BaseTool):
    """Create, list, and cancel persistent reminders.

    Reminders are stored in Redis and survive container restarts.
    When a reminder fires, its task is executed through the orchestrator
    and the result is pushed back to the user's chat as a proactive message.

    Actions
    -------
    create  — schedule a reminder
    list    — show all pending reminders
    cancel  — cancel by id or label
    """

    name = "reminder"
    description = (
        "Create, list, or cancel persistent reminders that survive restarts. "
        "When a reminder fires, the task runs and the result appears in chat automatically. "
        "create: when='tomorrow at 9am' or delay_seconds=3600, task='...', label='...', recurrence='daily|weekdays|weekly|hourly'. "
        "list: show pending. cancel: id=... or label=..."
    )
    safety_flags = []

    def __init__(self, redis_client, scheduler=None) -> None:
        self._redis = redis_client
        self._scheduler = scheduler

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def run(self, args: dict, **context) -> ToolResult:
        action = args.get("action", "create")
        if action == "create":
            return self._create(args, context)
        elif action == "list":
            return self._list()
        elif action == "cancel":
            return self._cancel(args)
        else:
            return ToolResult(
                self.name, False,
                error=f"Unknown action {action!r}. Use: create, list, cancel",
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create(self, args: dict, context: dict) -> ToolResult:
        task = str(args.get("task", "")).strip()
        if not task:
            return ToolResult(self.name, False, error="task is required")

        label = str(args.get("label") or task[:60])
        recurrence = (args.get("recurrence") or "").strip().lower() or None
        session_id = str(context.get("session_id") or "default")
        channel = str(context.get("channel") or "websocket")

        valid_recurrences = {"hourly", "daily", "weekdays", "weekly"}
        if recurrence and recurrence not in valid_recurrences:
            return ToolResult(
                self.name, False,
                error=f"Invalid recurrence {recurrence!r}. Use: {', '.join(sorted(valid_recurrences))}",
            )

        try:
            fire_at = self._parse_time(
                when=args.get("when"),
                delay_seconds=args.get("delay_seconds"),
            )
        except ValueError as exc:
            return ToolResult(self.name, False, error=str(exc))

        if fire_at <= datetime.now():
            return ToolResult(
                self.name, False,
                error="Reminder time is in the past — please specify a future time",
            )

        rem_id = uuid.uuid4().hex[:12]
        reminder = {
            "id":          rem_id,
            "task":        task,
            "label":       label,
            "session_id":  session_id,
            "channel":     channel,
            "fire_at":     fire_at.isoformat(),
            "recurrence":  recurrence or "",
            "status":      "pending",
            "created_at":  datetime.now().isoformat(),
        }
        self._redis.r.hset(f"{_HASH_PREFIX}{rem_id}", mapping=reminder)
        self._redis.r.zadd(_PENDING_ZSET, {rem_id: fire_at.timestamp()})

        if self._scheduler:
            self._scheduler.schedule_reminder(rem_id, fire_at, recurrence)

        # Human-readable time description
        delta = fire_at - datetime.now()
        secs = int(delta.total_seconds())
        if secs < 3600:
            human_when = f"in {secs // 60}m {secs % 60}s"
        elif secs < 86400:
            human_when = f"in {secs // 3600}h {(secs % 3600) // 60}m"
        else:
            human_when = f"on {fire_at.strftime('%a %d %b at %H:%M')}"

        recur_str = f" (repeats {recurrence})" if recurrence else ""
        return ToolResult(
            self.name, True,
            output={
                "status":     "created",
                "id":         rem_id,
                "label":      label,
                "task":       task,
                "fire_at":    fire_at.strftime("%Y-%m-%d %H:%M:%S"),
                "recurrence": recurrence,
                "message":    f"Reminder set: '{label}' — {human_when}{recur_str}.",
            },
        )

    def _list(self) -> ToolResult:
        raw = self._redis.r.zrangebyscore(_PENDING_ZSET, "-inf", "+inf")
        reminders = []
        for id_bytes in raw:
            rem_id = id_bytes.decode() if isinstance(id_bytes, bytes) else id_bytes
            data = self._redis.r.hgetall(f"{_HASH_PREFIX}{rem_id}")
            if not data:
                continue
            d = self._decode(data)
            if d.get("status") == "pending":
                reminders.append({
                    "id":         d["id"],
                    "label":      d.get("label") or d.get("task", "")[:40],
                    "fire_at":    d.get("fire_at", ""),
                    "recurrence": d.get("recurrence") or None,
                    "task":       d.get("task", ""),
                })

        msg = f"{len(reminders)} pending reminder(s)." if reminders else "No pending reminders."
        return ToolResult(
            self.name, True,
            output={"reminders": reminders, "count": len(reminders), "message": msg},
        )

    def _cancel(self, args: dict) -> ToolResult:
        rem_id = args.get("id", "").strip()
        label_q = args.get("label", "").strip().lower()

        if not rem_id and not label_q:
            return ToolResult(self.name, False, error="Provide id or label to cancel")

        if not rem_id:
            raw = self._redis.r.zrangebyscore(_PENDING_ZSET, "-inf", "+inf")
            for id_bytes in raw:
                rid = id_bytes.decode() if isinstance(id_bytes, bytes) else id_bytes
                data = self._redis.r.hgetall(f"{_HASH_PREFIX}{rid}")
                d = self._decode(data)
                if label_q in d.get("label", "").lower() or label_q in d.get("task", "").lower():
                    rem_id = rid
                    break

        if not rem_id:
            return ToolResult(
                self.name, False,
                error=f"No pending reminder found matching {label_q!r}",
            )

        self._redis.r.hset(f"{_HASH_PREFIX}{rem_id}", "status", "cancelled")
        self._redis.r.zrem(_PENDING_ZSET, rem_id)

        if self._scheduler and self._scheduler._scheduler:
            try:
                self._scheduler._scheduler.remove_job(f"reminder_{rem_id}")
            except Exception:
                pass

        return ToolResult(
            self.name, True,
            output={"status": "cancelled", "id": rem_id, "message": f"Reminder '{rem_id}' cancelled."},
        )

    # ------------------------------------------------------------------
    # Internal helpers — used by Scheduler
    # ------------------------------------------------------------------

    def list_pending_raw(self) -> list[tuple[str, datetime]]:
        """Return [(id, fire_at_datetime), ...] for all pending reminders."""
        raw = self._redis.r.zrangebyscore(_PENDING_ZSET, "-inf", "+inf", withscores=True)
        result = []
        for id_bytes, score in raw:
            rem_id = id_bytes.decode() if isinstance(id_bytes, bytes) else id_bytes
            status_raw = self._redis.r.hget(f"{_HASH_PREFIX}{rem_id}", "status")
            status = (status_raw.decode() if isinstance(status_raw, bytes) else status_raw) if status_raw else ""
            if status == "pending":
                result.append((rem_id, datetime.fromtimestamp(score)))
        return result

    def get_reminder(self, rem_id: str) -> Optional[dict]:
        data = self._redis.r.hgetall(f"{_HASH_PREFIX}{rem_id}")
        return self._decode(data) if data else None

    def mark_fired(self, rem_id: str) -> None:
        self._redis.r.hset(f"{_HASH_PREFIX}{rem_id}", "status", "fired")
        self._redis.r.zrem(_PENDING_ZSET, rem_id)

    def reschedule(self, rem_id: str, next_fire: datetime) -> None:
        self._redis.r.hset(
            f"{_HASH_PREFIX}{rem_id}",
            mapping={"fire_at": next_fire.isoformat(), "status": "pending"},
        )
        self._redis.r.zadd(_PENDING_ZSET, {rem_id: next_fire.timestamp()})

    @staticmethod
    def compute_next_fire(fired_at: datetime, recurrence: str) -> Optional[datetime]:
        """Calculate next fire time for recurring reminders."""
        if recurrence in _RECURRENCE_DELTAS:
            return fired_at + _RECURRENCE_DELTAS[recurrence]
        if recurrence == "weekdays":
            nxt = fired_at + timedelta(days=1)
            while nxt.weekday() >= 5:  # skip Sat=5, Sun=6
                nxt += timedelta(days=1)
            return nxt
        return None

    # ------------------------------------------------------------------
    # Time parsing
    # ------------------------------------------------------------------

    def _parse_time(self, when: Optional[str], delay_seconds=None) -> datetime:
        if delay_seconds is not None:
            try:
                return datetime.now() + timedelta(seconds=float(delay_seconds))
            except (TypeError, ValueError):
                raise ValueError(f"Invalid delay_seconds: {delay_seconds!r}")

        if not when:
            raise ValueError(
                "Provide 'when' (e.g. 'tomorrow at 9am', 'in 2 hours', 'Monday at 14:00') "
                "or 'delay_seconds'"
            )

        # Try dateparser first
        try:
            import dateparser
            parsed = dateparser.parse(
                when,
                settings={
                    "TIMEZONE":               _USER_TZ,
                    "RETURN_AS_TIMEZONE_AWARE": False,
                    "PREFER_DATES_FROM":      "future",
                    "PREFER_DAY_OF_MONTH":    "first",
                    "TO_TIMEZONE":            "UTC",
                },
            )
            if parsed:
                return parsed
        except ImportError:
            logger.warning("[REMINDER] dateparser not installed — using basic parser")

        # Fallback: basic "in N unit" patterns
        w = when.lower().strip()
        now = datetime.now()
        if w.startswith("in "):
            parts = w[3:].split()
            if len(parts) >= 2:
                try:
                    amount = float(parts[0])
                    unit = parts[1].rstrip("s")
                    mapping = {
                        "second": timedelta(seconds=1),
                        "sec":    timedelta(seconds=1),
                        "minute": timedelta(minutes=1),
                        "min":    timedelta(minutes=1),
                        "hour":   timedelta(hours=1),
                        "hr":     timedelta(hours=1),
                        "day":    timedelta(days=1),
                        "week":   timedelta(weeks=1),
                    }
                    if unit in mapping:
                        return now + mapping[unit] * amount
                except ValueError:
                    pass

        raise ValueError(
            f"Could not parse time: {when!r}. "
            "Try: 'in 5 minutes', 'tomorrow at 9am', 'Monday at 14:00', 'in 2 hours', 'next Friday at 10am'"
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(data: dict) -> dict:
        return {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
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
                        "type":        "string",
                        "enum":        ["create", "list", "cancel"],
                        "description": "create=new reminder, list=show all pending, cancel=remove one",
                    },
                    "when": {
                        "type":        "string",
                        "description": "Natural language time: 'tomorrow at 9am', 'in 2 hours', 'Monday at 14:00', 'next Friday'",
                    },
                    "delay_seconds": {
                        "type":        "number",
                        "description": "Alternative to 'when' — seconds from now (e.g. 300 = 5 minutes)",
                    },
                    "task": {
                        "type":        "string",
                        "description": "Instruction to execute when the reminder fires — e.g. 'fetch weather for Cluj-Napoca and report it'",
                    },
                    "label": {
                        "type":        "string",
                        "description": "Short human-readable name for listing and cancelling",
                    },
                    "recurrence": {
                        "type":        "string",
                        "enum":        ["hourly", "daily", "weekdays", "weekly"],
                        "description": "Optional — repeat on this schedule after first fire",
                    },
                    "id": {
                        "type":        "string",
                        "description": "Reminder ID to cancel (from reminder(action=list))",
                    },
                },
                "required": ["action"],
            },
        }
