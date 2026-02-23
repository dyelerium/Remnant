"""WhatsApp sidecar proxy — /api/whatsapp/* endpoints."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["whatsapp"])

# Use the container_name (remnant-whatsapp) as the default hostname.
# Docker always adds the container_name as a DNS alias on any user-defined network,
# regardless of whether the container was started by compose or the SDK.
_DEFAULT_SIDECAR = "http://remnant-whatsapp:3000"
_SIDECAR_CONTAINER = "remnant-whatsapp"
_SIDECAR_IMAGE = "remnant-whatsapp-sidecar"
_SIDECAR_DIR = "/app/project/whatsapp-sidecar"


def _sidecar_url() -> str:
    return os.environ.get("WHATSAPP_SIDECAR_URL", "").rstrip("/") or _DEFAULT_SIDECAR


async def _run_blocking(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)


# ---------------------------------------------------------------------------
# Health / status / QR (proxy to sidecar)
# ---------------------------------------------------------------------------

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
            data = resp.json()
            if resp.status_code == 404:
                # Sidecar is initialising — QR not generated yet; tell frontend to retry
                return {"status": "not_ready", "qr": None}
            return {
                "status": data.get("status", "unknown"),
                "qr": data.get("qr"),       # base64 data URL or null
                "timestamp": data.get("timestamp"),
            }
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


# ---------------------------------------------------------------------------
# Container lifecycle (via Python docker SDK — no CLI binary needed)
# ---------------------------------------------------------------------------

def _docker_client():
    """Return a docker.DockerClient connected to the local socket."""
    try:
        import docker
        return docker.from_env()
    except ImportError:
        raise RuntimeError(
            "Python docker SDK not installed. Add 'docker>=7.0.0' to requirements.txt."
        )
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to Docker socket: {exc}")


def _ensure_image(client) -> str:
    """Return the sidecar image tag, building it if necessary."""
    import docker

    try:
        client.images.get(_SIDECAR_IMAGE)
        return _SIDECAR_IMAGE
    except docker.errors.ImageNotFound:
        pass

    sidecar_dir = Path(_SIDECAR_DIR)
    if not sidecar_dir.exists():
        raise RuntimeError(
            f"WhatsApp sidecar source not found at {sidecar_dir}. "
            "Make sure the repository is mounted at /app/project."
        )

    client.images.build(path=str(sidecar_dir), tag=_SIDECAR_IMAGE, rm=True)
    return _SIDECAR_IMAGE


def _remnant_network(client) -> str:
    """Return the Docker network the remnant container is on."""
    try:
        me = client.containers.get("remnant")
        networks = list(me.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        if networks:
            return networks[0]
    except Exception:
        pass
    return "bridge"


def _do_start() -> dict:
    import docker

    client = _docker_client()

    # ── Container already exists ────────────────────────────────────────────
    try:
        container = client.containers.get(_SIDECAR_CONTAINER)
        if container.status == "running":
            return {"status": "already_running"}
        container.start()
        return {"status": "started"}
    except docker.errors.NotFound:
        pass

    # ── First time: build image + create container ──────────────────────────
    image_tag = _ensure_image(client)
    network = _remnant_network(client)

    # Ensure the named volume exists
    try:
        client.volumes.get("remnant_whatsapp_data")
    except docker.errors.NotFound:
        client.volumes.create("remnant_whatsapp_data")

    # Use the low-level API so we can set network aliases.
    # This makes the container reachable as both "remnant-whatsapp" (container_name)
    # and "whatsapp-sidecar" (compose service name) on the same network.
    endpoint_config = client.api.create_endpoint_config(
        aliases=["whatsapp-sidecar"]
    )
    networking_config = client.api.create_networking_config({
        network: endpoint_config
    })
    host_config = client.api.create_host_config(
        port_bindings={"3000/tcp": 3000},
        restart_policy={"Name": "unless-stopped"},
        binds={"remnant_whatsapp_data": {"bind": "/app/.wwebjs_auth", "mode": "rw"}},
    )

    resp = client.api.create_container(
        image_tag,
        name=_SIDECAR_CONTAINER,
        detach=True,
        environment=["REMNANT_URL=http://remnant:8000"],
        host_config=host_config,
        networking_config=networking_config,
    )
    client.api.start(resp["Id"])
    return {"status": "created_and_started", "id": resp["Id"][:12]}


def _do_stop() -> dict:
    import docker

    client = _docker_client()
    try:
        container = client.containers.get(_SIDECAR_CONTAINER)
        container.stop(timeout=10)
        return {"status": "stopped"}
    except docker.errors.NotFound:
        return {"status": "not_found"}


@router.post("/whatsapp/start")
async def whatsapp_start() -> dict:
    """Start the whatsapp-sidecar container via Python docker SDK."""
    try:
        return await _run_blocking(_do_start)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/whatsapp/stop")
async def whatsapp_stop() -> dict:
    """Stop the whatsapp-sidecar container."""
    try:
        return await _run_blocking(_do_stop)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
