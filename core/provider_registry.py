"""Provider registry — load llm_providers.yaml, resolve model by use-case."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelSpec:
    provider: str
    model: str
    context_window: int = 128000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    use_cases: list[str] = field(default_factory=list)
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    extra_headers: dict = field(default_factory=dict)


class ProviderRegistry:
    """Registry of available LLM providers and models."""

    def __init__(self, config: dict) -> None:
        self._providers_cfg: dict = config.get("providers", {})
        self._defaults: dict = config.get("defaults", {})
        self._models: dict[str, ModelSpec] = {}
        self._load()

    def _load(self) -> None:
        for provider_name, provider_cfg in self._providers_cfg.items():
            api_key_env = provider_cfg.get("api_key_env")
            api_key = os.environ.get(api_key_env, "") if api_key_env else None
            base_url = provider_cfg.get("base_url")

            extra_headers = provider_cfg.get("extra_headers", {})

            for model_name, model_cfg in provider_cfg.get("models", {}).items():
                key = f"{provider_name}/{model_name}"
                self._models[key] = ModelSpec(
                    provider=provider_name,
                    model=model_name,
                    context_window=model_cfg.get("context_window", 128000),
                    cost_per_1k_input=float(model_cfg.get("cost_per_1k_input", 0.0)),
                    cost_per_1k_output=float(model_cfg.get("cost_per_1k_output", 0.0)),
                    use_cases=model_cfg.get("use_cases", []),
                    api_key=api_key,
                    base_url=base_url,
                    extra_headers=extra_headers,
                )

        logger.debug("Loaded %d model specs", len(self._models))

    def resolve(
        self,
        use_case: str,
        project_id: Optional[str] = None,
        override: Optional[str] = None,
    ) -> ModelSpec:
        """
        Resolve the best model for a use-case.

        Priority:
          1. Explicit override ("provider/model")
          2. Project-specific default (future)
          3. Global defaults from llm_providers.yaml

        Raises ValueError if no model found.
        """
        target = override or self._defaults.get(use_case)

        if target:
            if target in self._models:
                spec = self._models[target]
                # Check API key is available
                if spec.api_key is not None and not spec.api_key:
                    logger.warning("API key missing for %s", target)
                return spec

            # Try partial match
            for key, spec in self._models.items():
                if key.endswith(f"/{target}") or key == target:
                    return spec

        # Fall back to first model with this use_case
        for spec in self._models.values():
            if use_case in spec.use_cases:
                if spec.api_key is None or spec.api_key:
                    return spec

        raise ValueError(f"No model available for use_case={use_case!r}")

    def get(self, provider_model: str) -> Optional[ModelSpec]:
        """Get a specific model spec by 'provider/model' key."""
        return self._models.get(provider_model)

    def list_models(self, use_case: Optional[str] = None) -> list[ModelSpec]:
        specs = list(self._models.values())
        if use_case:
            specs = [s for s in specs if use_case in s.use_cases]
        return specs
