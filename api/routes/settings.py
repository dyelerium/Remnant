"""GET/POST /settings — API keys, model config, connector config, Ollama, budget."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])

# Env vars we expose status of (never return actual values)
_API_KEY_VARS = [
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "NVIDIA_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "SAMBANOVA_API_KEY",
    "VENICE_API_KEY",
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

    config = getattr(request.app.state, "config", {})
    return {
        "api_keys": {k: _env_status(k) for k in _API_KEY_VARS},
        "connectors": {
            "telegram_bot_token": _env_status("TELEGRAM_BOT_TOKEN"),
            "whatsapp_sidecar_url": os.environ.get("WHATSAPP_SIDECAR_URL", ""),
        },
        "default_model": default_model,
        "ollama": {
            "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            "api_key_set": bool(os.environ.get("OLLAMA_API_KEY", "")),
        },
        "lmstudio": {
            "base_url": os.environ.get("LMSTUDIO_BASE_URL", ""),
        },
        "budget_mode": bool(config.get("budget_mode", False)),
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
    """Set the default chat model in the provider registry and persist to YAML."""
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

        # Persist to YAML so it survives registry reloads
        config_path = Path("/app/config/llm_providers.yaml")
        if not config_path.exists():
            config_path = Path("config/llm_providers.yaml")
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            config.setdefault("defaults", {})["chat"] = body.model_key
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        return {"status": "updated", "model": body.model_key}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ModelConfigRequest(BaseModel):
    provider: str
    model: str
    thinking_enabled: Optional[bool] = None
    thinking_budget_tokens: Optional[int] = None


@router.post("/settings/model-config")
async def update_model_config(body: ModelConfigRequest, request: Request) -> dict:
    """Update per-model config fields (thinking, etc.) in the registry + YAML."""
    registry = request.app.state.registry
    key = f"{body.provider}/{body.model}"
    spec = registry.get(key)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Model {key!r} not found")

    if body.thinking_enabled is not None:
        spec.thinking_enabled = body.thinking_enabled
    if body.thinking_budget_tokens is not None:
        spec.thinking_budget_tokens = body.thinking_budget_tokens

    # Persist to YAML
    config_path = Path("/app/config/llm_providers.yaml")
    if not config_path.exists():
        config_path = Path("config/llm_providers.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.get("providers", {}).get(body.provider, {}).get("models", {}).get(body.model, {})
        if body.thinking_enabled is not None:
            model_cfg["thinking_enabled"] = body.thinking_enabled
        if body.thinking_budget_tokens is not None:
            model_cfg["thinking_budget_tokens"] = body.thinking_budget_tokens
        # Write back
        cfg.setdefault("providers", {}).setdefault(body.provider, {}).setdefault("models", {})[body.model] = model_cfg
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    return {"status": "updated", "key": key}


class ConnectorRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    whatsapp_sidecar_url: Optional[str] = None


@router.post("/settings/connectors")
async def set_connectors(body: ConnectorRequest, request: Request) -> dict:
    """Save connector settings."""
    import asyncio
    saved = {}
    if body.telegram_bot_token is not None:
        os.environ["TELEGRAM_BOT_TOKEN"] = body.telegram_bot_token
        _update_dot_env("TELEGRAM_BOT_TOKEN", body.telegram_bot_token)
        saved["telegram_bot_token"] = "saved"

        # Stop old bot (if running) and start a new one with the new token
        old_bot = getattr(request.app.state, "telegram_bot", None)
        if old_bot:
            try:
                await old_bot.stop()
            except Exception:
                pass
            request.app.state.telegram_bot = None
        if body.telegram_bot_token.strip():
            from channels.telegram_bot import TelegramBot
            new_bot = TelegramBot(
                body.telegram_bot_token.strip(),
                request.app.state.orchestrator,
                request.app.state.retriever,
                broadcast_fn=getattr(request.app.state, "broadcast", None),
            )
            new_bot._task = asyncio.create_task(new_bot.start())
            request.app.state.telegram_bot = new_bot
            logger.info("[TELEGRAM] Bot (re)started with new token")

    if body.whatsapp_sidecar_url is not None:
        os.environ["WHATSAPP_SIDECAR_URL"] = body.whatsapp_sidecar_url
        _update_dot_env("WHATSAPP_SIDECAR_URL", body.whatsapp_sidecar_url)
        saved["whatsapp_sidecar_url"] = "saved"
    return {"status": "saved", "saved": saved}


# -----------------------------------------------------------------------
# Ollama settings
# -----------------------------------------------------------------------

class OllamaRequest(BaseModel):
    url: str        # e.g. "http://192.168.1.5:11434"
    api_key: str = ""  # optional for cloud auth


@router.post("/settings/ollama")
async def save_ollama_settings(body: OllamaRequest, request: Request) -> dict:
    """Save Ollama base URL (and optional API key) to .env, then reload registry."""
    url = body.url.rstrip("/")
    os.environ["OLLAMA_BASE_URL"] = url
    _update_dot_env("OLLAMA_BASE_URL", url)
    if body.api_key:
        os.environ["OLLAMA_API_KEY"] = body.api_key
        _update_dot_env("OLLAMA_API_KEY", body.api_key)

    # Reload registry so Ollama models get the resolved base_url immediately
    registry = request.app.state.registry
    registry.reload_from_yaml({
        "providers": registry._providers_cfg,
        "defaults": registry._defaults,
        "fallback_chain": registry._fallback_chain,
    })

    return {"status": "saved", "url": url}


@router.get("/settings/ollama/test")
async def test_ollama_connection() -> dict:
    """Test connectivity to the configured Ollama instance."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.environ.get("OLLAMA_API_KEY", "")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{base_url}/api/tags", headers=headers)
            resp.raise_for_status()
            data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        return {"reachable": True, "model_count": len(models), "models": models[:20]}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


# -----------------------------------------------------------------------
# LM Studio settings
# -----------------------------------------------------------------------

class LMStudioRequest(BaseModel):
    url: str   # e.g. "http://192.168.1.5:1234/v1"


@router.post("/settings/lmstudio")
async def save_lmstudio_settings(body: LMStudioRequest, request: Request) -> dict:
    """Save LM Studio base URL to .env and reload registry."""
    url = body.url.rstrip("/")
    os.environ["LMSTUDIO_BASE_URL"] = url
    _update_dot_env("LMSTUDIO_BASE_URL", url)

    registry = request.app.state.registry
    registry.reload_from_yaml({
        "providers": registry._providers_cfg,
        "defaults": registry._defaults,
        "fallback_chain": registry._fallback_chain,
    })

    return {"status": "saved", "url": url}


@router.get("/settings/lmstudio/test")
async def test_lmstudio_connection() -> dict:
    """Test connectivity to the configured LM Studio instance."""
    base_url = os.environ.get("LMSTUDIO_BASE_URL", "")
    if not base_url:
        return {"reachable": False, "error": "LMSTUDIO_BASE_URL not configured"}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/models")
            resp.raise_for_status()
            data = resp.json()
        models = [m.get("id", "") for m in data.get("data", [])]
        return {"reachable": True, "model_count": len(models), "models": models[:20]}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


# -----------------------------------------------------------------------
# Budget settings
# -----------------------------------------------------------------------

class BudgetRequest(BaseModel):
    max_cost_usd_per_day: Optional[float] = None
    max_tokens_per_day: Optional[int] = None


@router.post("/settings/budget")
async def update_budget(body: BudgetRequest, request: Request) -> dict:
    """Update global budget caps in budget.yaml and apply to the running BudgetManager."""
    budget = request.app.state.budget

    config_path = Path("/app/config/budget.yaml")
    if not config_path.exists():
        config_path = Path("config/budget.yaml")

    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}

        global_cfg = cfg.setdefault("global", {})
        if body.max_cost_usd_per_day is not None:
            global_cfg["max_cost_usd_per_day"] = body.max_cost_usd_per_day
            budget._max_cost_day = body.max_cost_usd_per_day
        if body.max_tokens_per_day is not None:
            global_cfg["max_tokens_per_day"] = body.max_tokens_per_day
            budget._max_tokens_day = body.max_tokens_per_day

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        return {"status": "saved", "global": global_cfg}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# -----------------------------------------------------------------------
# Budget mode toggle
# -----------------------------------------------------------------------

class BudgetModeRequest(BaseModel):
    budget_mode: bool


@router.post("/settings/budget-mode")
async def set_budget_mode(body: BudgetModeRequest, request: Request) -> dict:
    """Persist budget_mode toggle to remnant.yaml and update running config."""
    _remnant_yaml = Path("/app/config/remnant.yaml")
    if not _remnant_yaml.exists():
        _remnant_yaml = Path("config/remnant.yaml")
    try:
        if _remnant_yaml.exists():
            with open(_remnant_yaml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["budget_mode"] = body.budget_mode
            with open(_remnant_yaml, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        # Update running config dict
        if hasattr(request.app.state, "config"):
            request.app.state.config["budget_mode"] = body.budget_mode
        return {"status": "saved", "budget_mode": body.budget_mode}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# -----------------------------------------------------------------------
# Fallback chain management
# -----------------------------------------------------------------------

_PROVIDERS_YAML = Path("/app/config/llm_providers.yaml") if Path("/app/config").exists() else Path("config/llm_providers.yaml")
_BUDGET_YAML = Path("/app/config/budget.yaml") if Path("/app/config").exists() else Path("config/budget.yaml")


@router.get("/settings/fallback")
async def get_fallback_chain(request: Request) -> dict:
    """Return the current global fallback chain."""
    registry = request.app.state.registry
    return {"fallback_chain": registry.get_fallback_chain()}


class FallbackRequest(BaseModel):
    fallback_chain: list[str]   # ordered list of "provider/model" keys


@router.post("/settings/fallback")
async def save_fallback_chain(body: FallbackRequest, request: Request) -> dict:
    """Persist the fallback chain to llm_providers.yaml and reload registry."""
    registry = request.app.state.registry
    known = {f"{m.provider}/{m.model}" for m in registry.list_models()}
    bad = [k for k in body.fallback_chain if k and k not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown model keys: {bad}")

    p = _PROVIDERS_YAML if _PROVIDERS_YAML.exists() else Path("config/llm_providers.yaml")
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cfg["fallback_chain"] = body.fallback_chain
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        registry.reload_from_yaml({
            "providers": cfg.get("providers", {}),
            "defaults": cfg.get("defaults", {}),
            "fallback_chain": body.fallback_chain,
        })
        return {"status": "saved", "fallback_chain": body.fallback_chain}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# -----------------------------------------------------------------------
# Per-model daily token cap
# -----------------------------------------------------------------------

class ModelCapRequest(BaseModel):
    model_key: str      # "provider/model"
    daily_token_cap: int   # 0 = unlimited


@router.post("/settings/model-cap")
async def save_model_cap(body: ModelCapRequest, request: Request) -> dict:
    """Set (or clear) the daily token cap for a specific model."""
    budget = request.app.state.budget
    budget.set_model_cap(body.model_key, body.daily_token_cap)

    # Persist to budget.yaml
    p = _BUDGET_YAML if _BUDGET_YAML.exists() else Path("config/budget.yaml")
    try:
        cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
        caps = cfg.setdefault("global", {}).setdefault("model_caps", {})
        if body.daily_token_cap <= 0:
            caps.pop(body.model_key, None)
        else:
            caps[body.model_key] = body.daily_token_cap
        p.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
        return {"status": "saved", "model_key": body.model_key, "daily_token_cap": body.daily_token_cap}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
