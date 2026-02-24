"""Self-diagnostic checks: Redis, Ollama, Telegram, WhatsApp, LLM providers."""
from __future__ import annotations

import os
from typing import Any


async def run_diagnostics(redis_client, registry) -> dict[str, Any]:
    """Run all diagnostics and return a dict of {system: {ok, detail/error}}."""
    import asyncio

    results: dict[str, Any] = {}

    # Redis
    try:
        await asyncio.get_event_loop().run_in_executor(None, redis_client.r.ping)
        results["redis"] = {"ok": True, "detail": "pong"}
    except Exception as exc:
        results["redis"] = {"ok": False, "error": str(exc)}

    # Ollama
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
        tags = resp.json()
        model_count = len(tags.get("models", []))
        results["ollama"] = {"ok": True, "detail": f"{model_count} model(s) at {ollama_url}"}
    except Exception as exc:
        results["ollama"] = {"ok": False, "error": str(exc)}

    # Telegram
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if tg_token:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"https://api.telegram.org/bot{tg_token}/getMe")
            data = resp.json()
            if data.get("ok"):
                results["telegram"] = {"ok": True, "detail": data.get("result", {}).get("username", "")}
            else:
                results["telegram"] = {"ok": False, "error": data.get("description", "API error")}
        except Exception as exc:
            results["telegram"] = {"ok": False, "error": str(exc)}
    else:
        results["telegram"] = {"ok": None, "detail": "not configured"}

    # WhatsApp sidecar
    wa_url = os.environ.get("WHATSAPP_SIDECAR_URL", "")
    if wa_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{wa_url}/health")
            results["whatsapp"] = {"ok": resp.status_code < 400, "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:
            results["whatsapp"] = {"ok": False, "error": str(exc)}
    else:
        results["whatsapp"] = {"ok": None, "detail": "not configured"}

    # LLM providers — env-var check only (no API calls to avoid cost)
    provider_checks = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
    }
    configured = [name for name, env in provider_checks.items() if os.environ.get(env, "").strip()]
    not_configured = [name for name, env in provider_checks.items() if not os.environ.get(env, "").strip()]
    results["llm_providers"] = {
        "ok": True if configured else None,
        "detail": f"Keys set: {', '.join(configured) or 'none'}; missing: {', '.join(not_configured) or 'none'}",
    }

    return results
