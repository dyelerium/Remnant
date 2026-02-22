"""Channel gateway — hub-and-spoke router, dispatches to correct channel adapter."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ChannelGateway:
    """
    Routes outbound messages to the correct channel adapter.
    Inbound messages from channels are forwarded directly to the Orchestrator.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, object] = {}

    def register(self, channel_name: str, adapter) -> None:
        """Register a channel adapter."""
        self._adapters[channel_name] = adapter
        logger.info("[GATEWAY] Registered channel: %s", channel_name)

    async def send(
        self,
        channel: str,
        recipient: str,
        message: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Send a message via the specified channel.
        Returns True if delivered, False otherwise.
        """
        adapter = self._adapters.get(channel)
        if not adapter:
            logger.warning("[GATEWAY] No adapter registered for channel: %s", channel)
            return False

        try:
            await adapter.send_message(recipient, message, metadata=metadata)
            return True
        except Exception as exc:
            logger.error("[GATEWAY] Send error on %s: %s", channel, exc)
            return False

    def list_channels(self) -> list[str]:
        return list(self._adapters.keys())
