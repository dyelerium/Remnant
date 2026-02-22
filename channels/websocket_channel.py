"""WebSocket channel adapter."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WebSocketChannel:
    """Adapter for WebSocket connections (managed by FastAPI route in api/routes/chat.py)."""

    name = "websocket"

    def __init__(self) -> None:
        self._connections: dict[str, object] = {}  # session_id → WebSocket

    def register_connection(self, session_id: str, ws) -> None:
        self._connections[session_id] = ws
        logger.debug("[WS] Registered session %s", session_id[:8])

    def deregister_connection(self, session_id: str) -> None:
        self._connections.pop(session_id, None)

    async def send_message(
        self,
        session_id: str,
        message: str,
        metadata: Optional[dict] = None,
    ) -> None:
        ws = self._connections.get(session_id)
        if not ws:
            logger.warning("[WS] No connection for session %s", session_id[:8])
            return
        await ws.send_json({"type": "message", "content": message, **(metadata or {})})

    def active_sessions(self) -> int:
        return len(self._connections)
