"""GET/POST /settings — API keys, model config, connector config."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["settings"])

# Env vars we expose status of (never return actual values)
_API_KEY_VARS = [
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]
_CONNECTOR_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "WHATSAPP_SIDECAR_URL",
]


def _env_status(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        return "not_set"
    if len(v) < 8:
        return "too_short"
    return "set"


def _dot_env_path() -> Path:
    """Resolve .env file path (project root)."""
    return Path("/app/.env") if Path("/app/.env").exists() else Path(".env")


def _update_dot_env(key: str, value: str) -> None:
    """Write or update a key=value line in .env file."""
    env_path = _dot_env_path()
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    content = env_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)

    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"

    env_path.write_text(content, encoding="utf-8")


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    """Return settings status (no secret values)."""
    registry = request.app.state.registry

    # Resolve default model
    try:
        default_spec = registry.resolve("chat")
        default_model = f"{default_spec.provider}/{default_spec.model}"
    except Exception:
        default_model = None

    return {
        "api_keys": {k: _env_status(k) for k in _API_KEY_VARS},
        "connectors": {
            "telegram_bot_token": _env_status("TELEGRAM_BOT_TOKEN"),
            "whatsapp_sidecar_url": os.environ.get("WHATSAPP_SIDECAR_URL", ""),
        },
        "default_model": default_model,
    }


class ApiKeyRequest(BaseModel):
    name: str   # e.g. OPENROUTER_API_KEY
    value: str


@router.post("/settings/api-key")
async def set_api_key(body: ApiKeyRequest, request: Request) -> dict:
    """Save an API key to .env and update the running environment."""
    allowed = _API_KEY_VARS + _CONNECTOR_VARS
    if body.name not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {body.name!r}")
    if not body.value.strip():
        raise HTTPException(status_code=400, detail="Value cannot be empty")

    os.environ[body.name] = body.value
    _update_dot_env(body.name, body.value)
    return {"status": "saved", "name": body.name}


class ModelRequest(BaseModel):
    model_key: str   # e.g. "openrouter/stepfun/step-3.5-flash:free"


@router.post("/settings/model")
async def set_default_model(body: ModelRequest, request: Request) -> dict:
    """Set the default chat model in the provider registry."""
    registry = request.app.state.registry
    try:
        # Validate that the model key exists
        parts = body.model_key.split("/", 1)
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="Model key must be provider/model")
        models = registry.list_models()
        keys = [f"{m.provider}/{m.model}" for m in models]
        if body.model_key not in keys:
            raise HTTPException(status_code=404, detail=f"Model {body.model_key!r} not found in registry")
        registry.set_default("chat", body.model_key)
        return {"status": "updated", "model": body.model_key}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ConnectorRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    whatsapp_sidecar_url: Optional[str] = None


@router.post("/settings/connectors")
async def set_connectors(body: ConnectorRequest, request: Request) -> dict:
    """Save connector settings."""
    saved = {}
    if body.telegram_bot_token is not None:
        os.environ["TELEGRAM_BOT_TOKEN"] = body.telegram_bot_token
        _update_dot_env("TELEGRAM_BOT_TOKEN", body.telegram_bot_token)
        saved["telegram_bot_token"] = "saved"
    if body.whatsapp_sidecar_url is not None:
        os.environ["WHATSAPP_SIDECAR_URL"] = body.whatsapp_sidecar_url
        _update_dot_env("WHATSAPP_SIDECAR_URL", body.whatsapp_sidecar_url)
        saved["whatsapp_sidecar_url"] = "saved"
    return {"status": "saved", "saved": saved}
