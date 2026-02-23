"""WhatsApp sidecar proxy — /api/whatsapp/* endpoints."""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["whatsapp"])

_DEFAULT_SIDECAR = "http://whatsapp-sidecar:3000"


def _sidecar_url() -> str:
    return os.environ.get("WHATSAPP_SIDECAR_URL", "").rstrip("/") or _DEFAULT_SIDECAR


@router.get("/whatsapp/status")
async def whatsapp_status() -> dict:
    """Check sidecar health and WhatsApp connection status."""
    url = _sidecar_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            data = resp.json()
            return {
                "sidecar_url": url,
                "sidecar_reachable": True,
                "ready": data.get("ready", False),
            }
    except Exception as exc:
        return {
            "sidecar_url": url,
            "sidecar_reachable": False,
            "ready": False,
            "error": str(exc),
        }


@router.get("/whatsapp/qr")
async def whatsapp_qr() -> dict:
    """Fetch the current QR code from the sidecar (proxied so browser can reach it)."""
    url = _sidecar_url()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/qr")
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="No QR code available yet — sidecar is still initialising")
            data = resp.json()
            return {
                "status": data.get("status", "unknown"),
                "qr": data.get("qr"),          # base64 data URL or null
                "timestamp": data.get("timestamp"),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Sidecar unreachable: {exc}")


@router.post("/whatsapp/logout")
async def whatsapp_logout() -> dict:
    """Send logout request to sidecar."""
    url = _sidecar_url()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{url}/logout")
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Sidecar unreachable: {exc}")
