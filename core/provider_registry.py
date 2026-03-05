"""Provider registry — load llm_providers.yaml, resolve model by use-case."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def _resolve_env(value: Optional[str]) -> Optional[str]:
    """Expand ${VAR:default} patterns in config strings using environment variables."""
    if not value:
        return value
    def _replacer(m: re.Match) -> str:
        var, default = m.group(1), m.group(2) if m.group(2) is not None else ""
        return os.environ.get(var, default)
    return re.sub(r"\$\{([^}:]+)(?::([^}]*))?\}", _replacer, value)

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
    has_vision: bool = False
    max_completion_tokens: int = 4096
    history_fraction: float = 0.7
    temperature: float = 0.7
    top_p: float = 1.0
    stream: bool = True
    native_tools: bool = False
    extra_pricing: dict = field(default_factory=dict)
    # Thinking / extended reasoning
    has_thinking: bool = False
    thinking_enabled: bool = True
    thinking_budget_tokens: int = 8000


class ProviderRegistry:
    """Registry of available LLM providers and models."""

    def __init__(self, config: dict) -> None:
        self._providers_cfg: dict = config.get("providers", {})
        self._defaults: dict = config.get("defaults", {})
        self._fallback_chain: list[str] = config.get("fallback_chain", [])
        self._models: dict[str, ModelSpec] = {}
        self._load()

    def _load(self) -> None:
        for provider_name, provider_cfg in self._providers_cfg.items():
            api_key_env = provider_cfg.get("api_key_env")
            api_key = os.environ.get(api_key_env, "") if api_key_env else None
            base_url = _resolve_env(provider_cfg.get("base_url"))

            # For providers with no API key requirement (api_key_env=null), skip if
            # base_url resolved to empty — these are local providers (LM Studio etc.)
            # that must be explicitly configured via their env var before they show models.
            if api_key_env is None and base_url == "":
                continue

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
                    has_vision=bool(model_cfg.get("has_vision", False)),
                    max_completion_tokens=int(model_cfg.get("max_completion_tokens", 4096)),
                    history_fraction=float(model_cfg.get("history_fraction", 0.7)),
                    temperature=float(model_cfg.get("temperature", 0.7)),
                    top_p=float(model_cfg.get("top_p", 1.0)),
                    stream=bool(model_cfg.get("stream", True)),
                    native_tools=bool(model_cfg.get("native_tools", False)),
                    extra_pricing=model_cfg.get("extra_pricing", {}),
                    has_thinking=bool(model_cfg.get("has_thinking", False)),
                    thinking_enabled=bool(model_cfg.get("thinking_enabled", True)),
                    thinking_budget_tokens=int(model_cfg.get("thinking_budget_tokens", 8000)),
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

    def set_default(self, use_case: str, model_key: str) -> None:
        """Override the default model for a use-case at runtime."""
        self._defaults[use_case] = model_key
        logger.info("Default model for %s set to %s", use_case, model_key)

    def add_or_update_model(self, key: str, spec: ModelSpec) -> None:
        """Add or update a model spec in the registry."""
        self._models[key] = spec
        logger.info("Model %s added/updated in registry", key)

    def get_fallback_chain(self) -> list[str]:
        """Return the configured global fallback chain (list of provider/model keys)."""
        return self._fallback_chain

    def reload_from_yaml(self, config: dict) -> None:
        """Reload the registry from a new config dict (clears existing models)."""
        self._providers_cfg = config.get("providers", {})
        self._defaults = config.get("defaults", {})
        self._models = {}
        self._load()
        logger.info("Registry reloaded — %d models loaded", len(self._models))

    def save_defaults_to_yaml(self, yaml_path: Path) -> None:
        """Persist current in-memory defaults back to a YAML file."""
        if not yaml_path.exists():
            logger.warning("YAML path %s does not exist, skipping save", yaml_path)
            return
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        config["defaults"] = dict(self._defaults)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        logger.info("Defaults saved to %s", yaml_path)
