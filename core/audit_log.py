"""Audit logging — append-only event log stored in Redis sorted sets."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Redis key pattern: remnant:audit:YYYY-MM-DD
_KEY_PREFIX = "remnant:audit"
_TTL_DAYS = 30  # keep 30 days of audit logs


class AuditLogger:
    """
    Writes audit events to Redis sorted sets keyed by date.
    Score = epoch timestamp (float) for chronological ordering.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Public log methods
    # ------------------------------------------------------------------

    def log_chat(
        self,
        *,
        channel: str,
        session_id: str,
        user_message: str,
        project_id: Optional[str] = None,
    ) -> None:
        self._write("chat", {
            "channel": channel,
            "session_id": session_id,
            "message_preview": user_message[:200],
            "project_id": project_id,
        })

    def log_tool(
        self,
        *,
        tool_name: str,
        session_id: str,
        args_preview: str = "",
        result_ok: bool = True,
        error: Optional[str] = None,
    ) -> None:
        self._write("tool", {
            "tool_name": tool_name,
            "session_id": session_id,
            "args_preview": args_preview[:200],
            "result_ok": result_ok,
            "error": error,
        })

    def log_security(
        self,
        *,
        reason: str,
        message_preview: str,
        session_id: str = "",
        channel: str = "",
    ) -> None:
        self._write("security_block", {
            "reason": reason,
            "message_preview": message_preview[:200],
            "session_id": session_id,
            "channel": channel,
        })

    def log_memory(
        self,
        *,
        operation: str,   # "record" | "retrieve" | "compact"
        session_id: str = "",
        details: str = "",
    ) -> None:
        self._write("memory", {
            "operation": operation,
            "session_id": session_id,
            "details": details[:200],
        })

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_recent(self, limit: int = 100, date_str: Optional[str] = None) -> list[dict]:
        """
        Return the most recent `limit` audit entries.
        If date_str (YYYY-MM-DD) is given, reads that day's key.
        Otherwise reads today and yesterday to cover midnight boundaries.
        """
        import datetime
        today = datetime.date.today().isoformat()
        keys = [f"{_KEY_PREFIX}:{date_str or today}"]
        if not date_str:
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            keys.append(f"{_KEY_PREFIX}:{yesterday}")

        entries: list[dict] = []
        for key in keys:
            try:
                raw = self._redis.r.zrevrange(key, 0, limit - 1, withscores=True)
                for data, score in raw:
                    try:
                        entry = json.loads(data)
                        entry["_ts"] = score
                        entries.append(entry)
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("[AUDIT] Read failed for %s: %s", key, exc)

        entries.sort(key=lambda e: e.get("_ts", 0), reverse=True)
        return entries[:limit]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, event_type: str, details: dict[str, Any]) -> None:
        ts = time.time()
        import datetime
        date_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        key = f"{_KEY_PREFIX}:{date_str}"

        entry = {"event_type": event_type, "ts": ts, **details}
        try:
            self._redis.r.zadd(key, {json.dumps(entry): ts})
            self._redis.r.expire(key, _TTL_DAYS * 86400)
        except Exception as exc:
            logger.warning("[AUDIT] Write failed: %s", exc)
