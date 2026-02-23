"""GET/POST /llm/providers — CRUD for LLM provider config + budgets."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["llm"])


@router.get("/llm/providers")
async def list_providers(request: Request) -> dict:
    registry = request.app.state.registry
    models = [
        {
            "key": f"{s.provider}/{s.model}",
            "provider": s.provider,
            "model": s.model,
            "use_cases": s.use_cases,
            "cost_per_1k_input": s.cost_per_1k_input,
            "cost_per_1k_output": s.cost_per_1k_output,
            "context_window": s.context_window,
            "has_vision": s.has_vision,
            "max_completion_tokens": s.max_completion_tokens,
            "history_fraction": s.history_fraction,
            "temperature": s.temperature,
            "top_p": s.top_p,
            "stream": s.stream,
        }
        for s in registry.list_models()
    ]
    return {"models": models, "count": len(models)}


@router.get("/llm/usage")
async def usage(project_id: str = None, request: Request = None) -> dict:
    budget = request.app.state.budget
    return budget.get_usage_summary(project_id=project_id)


@router.get("/llm/providers/{use_case}")
async def resolve_provider(use_case: str, request: Request) -> dict:
    registry = request.app.state.registry
    try:
        spec = registry.resolve(use_case)
        return {
            "use_case": use_case,
            "provider": spec.provider,
            "model": spec.model,
        }
    except ValueError as exc:
        return {"error": str(exc)}


# -----------------------------------------------------------------------
# Remote model list fetching
# -----------------------------------------------------------------------

@router.get("/llm/providers/remote/{provider}")
async def fetch_remote_models(provider: str, request: Request) -> dict:
    """
    Fetch live model list from the provider's own API.
    Supported providers: openrouter, ollama, anthropic, openai
    """
    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY not set")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            models = []
            for m in data.get("data", []):
                pricing = m.get("pricing", {})
                try:
                    cost_in = float(pricing.get("prompt", 0)) * 1000
                    cost_out = float(pricing.get("completion", 0)) * 1000
                except (TypeError, ValueError):
                    cost_in = cost_out = 0.0
                arch = m.get("architecture", {})
                has_vision = "image" in arch.get("input_modalities", [])
                models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", m.get("id", "")),
                    "context_length": m.get("context_length", 128000),
                    "cost_per_1k_input": cost_in,
                    "cost_per_1k_output": cost_out,
                    "has_vision": has_vision,
                    "max_completion_tokens": m.get("top_provider", {}).get("max_completion_tokens", 4096),
                })
            return {"provider": "openrouter", "models": models, "count": len(models)}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"OpenRouter API error: {exc}")

    elif provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                models.append({
                    "id": name,
                    "name": name,
                    "context_length": 128000,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "has_vision": False,
                    "max_completion_tokens": 4096,
                })
            return {"provider": "ollama", "models": models, "count": len(models)}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Ollama not reachable: {exc}")

    elif provider == "anthropic":
        # No public models endpoint — return static list
        static = [
            {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "context_length": 200000,
             "cost_per_1k_input": 0.015, "cost_per_1k_output": 0.075, "has_vision": True, "max_completion_tokens": 8192},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_length": 200000,
             "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.015, "has_vision": True, "max_completion_tokens": 8192},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "context_length": 200000,
             "cost_per_1k_input": 0.00025, "cost_per_1k_output": 0.00125, "has_vision": True, "max_completion_tokens": 8192},
        ]
        return {"provider": "anthropic", "models": static, "count": len(static)}

    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not set")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                if not (mid.startswith("gpt-") or mid.startswith("o")):
                    continue
                models.append({
                    "id": mid,
                    "name": mid,
                    "context_length": 128000,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "has_vision": "vision" in mid or "4o" in mid,
                    "max_completion_tokens": 4096,
                })
            return {"provider": "openai", "models": models, "count": len(models)}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"OpenAI API error: {exc}")

    else:
        raise HTTPException(status_code=404, detail=f"Provider {provider!r} not supported for remote fetch")


# -----------------------------------------------------------------------
# Per-model config save
# -----------------------------------------------------------------------

class ModelConfigRequest(BaseModel):
    provider: str
    model: str
    context_window: int = 128000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    has_vision: bool = False
    max_completion_tokens: int = 4096
    history_fraction: float = 0.7
    temperature: float = 0.7
    top_p: float = 1.0
    stream: bool = True
    use_cases: list[str] = []
    set_as_default_for_chat: bool = False


@router.post("/llm/providers/model-config")
async def save_model_config(body: ModelConfigRequest, request: Request) -> dict:
    """Save per-model settings to llm_providers.yaml and reload registry."""
    registry = request.app.state.registry
    config_path = Path("/app/config/llm_providers.yaml")
    if not config_path.exists():
        config_path = Path("config/llm_providers.yaml")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        providers = config.setdefault("providers", {})
        provider_cfg = providers.setdefault(body.provider, {"models": {}})
        provider_cfg.setdefault("models", {})[body.model] = {
            "context_window": body.context_window,
            "cost_per_1k_input": body.cost_per_1k_input,
            "cost_per_1k_output": body.cost_per_1k_output,
            "has_vision": body.has_vision,
            "max_completion_tokens": body.max_completion_tokens,
            "history_fraction": body.history_fraction,
            "temperature": body.temperature,
            "top_p": body.top_p,
            "stream": body.stream,
            "use_cases": body.use_cases or ["chat"],
        }

        if body.set_as_default_for_chat:
            config.setdefault("defaults", {})["chat"] = f"{body.provider}/{body.model}"

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        # Reload registry — merge YAML defaults with in-memory overrides so that
        # any model chosen via POST /api/settings/model is not silently reverted.
        merged_defaults = {**config.get("defaults", {}), **registry._defaults}
        if body.set_as_default_for_chat:
            merged_defaults["chat"] = f"{body.provider}/{body.model}"
        registry.reload_from_yaml({
            "providers": config.get("providers", {}),
            "defaults": merged_defaults,
        })

        return {"status": "saved", "key": f"{body.provider}/{body.model}"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# -----------------------------------------------------------------------
# Cost breakdown
# -----------------------------------------------------------------------

@router.get("/llm/costs/breakdown")
async def cost_breakdown(request: Request) -> dict:
    """Return per-model token usage and cost for today."""
    budget = request.app.state.budget
    registry = request.app.state.registry
    redis = request.app.state.redis.r

    day_window = str(int(time.time()) // 86400)
    prefix = budget._prefix  # e.g. "budget"

    # Scan for all by-provider keys for today
    pattern = f"{prefix}:tokens:by_provider:*:{day_window}"
    breakdown = []
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor, match=pattern, count=100)
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            # key format: budget:tokens:by_provider:{provider}:{model}:{day_window}
            # Strip prefix + "tokens:by_provider:" + day_window suffix
            inner = key_str[len(f"{prefix}:tokens:by_provider:"):]
            # Remove trailing :{day_window}
            suffix = f":{day_window}"
            if inner.endswith(suffix):
                inner = inner[: -len(suffix)]
            # inner is now "{provider}:{model}" — split on first colon
            parts = inner.split(":", 1)
            if len(parts) != 2:
                continue
            provider, model = parts
            total_tokens = int(redis.get(key_str) or 0)

            # Look up cost rates
            spec = registry.get(f"{provider}/{model}")
            cost_per_1k_in = spec.cost_per_1k_input if spec else 0.0
            cost_per_1k_out = spec.cost_per_1k_output if spec else 0.0
            # Without in/out split we approximate 50/50
            approx_cost = total_tokens / 1000 * ((cost_per_1k_in + cost_per_1k_out) / 2)

            breakdown.append({
                "provider": provider,
                "model": model,
                "tokens_today": total_tokens,
                "cost_usd_today": round(approx_cost, 6),
            })
        if cursor == 0:
            break

    breakdown.sort(key=lambda x: x["tokens_today"], reverse=True)
    return {"breakdown": breakdown, "count": len(breakdown)}
