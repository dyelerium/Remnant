"""WhatsApp QR channel — HTTP client to whatsapp-web.js sidecar."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class WhatsAppQRChannel:
    """HTTP client wrapper for the whatsapp-web.js sidecar service."""

    name = "whatsapp"

    def __init__(self, sidecar_url: str = "http://whatsapp-sidecar:3000") -> None:
        self._url = sidecar_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send_message(
        self,
        phone: str,
        message: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Send a WhatsApp message via the sidecar.

        Args:
            phone:   Phone number (digits only, with country code, e.g. "491234567890")
            message: Text to send
        """
        try:
            response = await self._client.post(
                f"{self._url}/send",
                json={"phone": phone, "message": message},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("[WHATSAPP] Send failed to %s: %s", phone, exc)
            return {"error": str(exc)}

    async def get_qr(self) -> dict:
        """Fetch the current QR code for WhatsApp login."""
        try:
            response = await self._client.get(f"{self._url}/qr")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("[WHATSAPP] QR fetch failed: %s", exc)
            return {"error": str(exc)}

    async def is_ready(self) -> bool:
        """Check if WhatsApp client is authenticated."""
        try:
            response = await self._client.get(f"{self._url}/health", timeout=5.0)
            return response.json().get("ready", False)
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
