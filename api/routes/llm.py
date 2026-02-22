"""GET/POST /llm/providers — CRUD for LLM provider config + budgets."""
from __future__ import annotations

from fastapi import APIRouter, Request

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
