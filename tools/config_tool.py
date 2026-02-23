"""Config tool — agents can list/switch LLM models and reload config."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ConfigTool(BaseTool):
    name = "config"
    description = (
        "Manage LLM model configuration. "
        "Actions: list_models, get_current, set_model, list_providers, reload_config."
    )
    safety_flags: list[str] = []

    def __init__(self, registry, config_dir: Path) -> None:
        self._registry = registry
        self._config_dir = config_dir

    @property
    def schema_hint(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_models",
                            "get_current",
                            "set_model",
                            "list_providers",
                            "reload_config",
                        ],
                        "description": "Action to perform",
                    },
                    "use_case": {
                        "type": "string",
                        "description": (
                            "Use case: 'chat', 'planning', 'curator', 'compaction', 'fast'. "
                            "Required for set_model and get_current."
                        ),
                    },
                    "model_key": {
                        "type": "string",
                        "description": (
                            "Provider/model key e.g. 'openrouter/stepfun/step-3.5-flash:free'. "
                            "Required for set_model."
                        ),
                    },
                    "persist": {
                        "type": "boolean",
                        "description": (
                            "If true, save the model change permanently to llm_providers.yaml. "
                            "Default false (session-only)."
                        ),
                    },
                },
                "required": ["action"],
            },
        }

    async def run(self, args: dict, **context) -> ToolResult:
        action = args.get("action", "")
        if action == "list_models":
            return await self._list_models()
        elif action == "get_current":
            return await self._get_current(args.get("use_case"))
        elif action == "set_model":
            return await self._set_model(
                args.get("use_case", "chat"),
                args.get("model_key", ""),
                bool(args.get("persist", False)),
            )
        elif action == "list_providers":
            return await self._list_providers()
        elif action == "reload_config":
            return await self._reload_config()
        else:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=(
                    f"Unknown action: {action!r}. "
                    "Valid: list_models, get_current, set_model, list_providers, reload_config"
                ),
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _list_models(self) -> ToolResult:
        models = self._registry.list_models()
        result = [
            {
                "key": f"{m.provider}/{m.model}",
                "provider": m.provider,
                "model": m.model,
                "use_cases": m.use_cases,
                "context_window": m.context_window,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "has_vision": m.has_vision,
                "max_completion_tokens": m.max_completion_tokens,
            }
            for m in models
        ]
        return ToolResult(tool_name=self.name, success=True, output=result)

    async def _get_current(self, use_case: Optional[str] = None) -> ToolResult:
        targets = [use_case] if use_case else ["chat", "planning", "curator", "compaction"]
        result = {}
        for uc in targets:
            try:
                spec = self._registry.resolve(uc)
                result[uc] = f"{spec.provider}/{spec.model}"
            except ValueError:
                result[uc] = None
        return ToolResult(tool_name=self.name, success=True, output=result)

    async def _set_model(self, use_case: str, model_key: str, persist: bool) -> ToolResult:
        if not model_key:
            return ToolResult(
                tool_name=self.name, success=False, error="model_key is required"
            )

        # Validate model exists in registry
        spec = self._registry.get(model_key)
        if not spec:
            known = {f"{m.provider}/{m.model}" for m in self._registry.list_models()}
            if model_key not in known:
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    error=(
                        f"Model {model_key!r} not found in registry. "
                        "Use list_models to see available models."
                    ),
                )

        # Apply at runtime (session default)
        self._registry.set_default(use_case, model_key)

        if persist:
            yaml_path = self._config_dir / "llm_providers.yaml"
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                config.setdefault("defaults", {})[use_case] = model_key
                with open(yaml_path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                # Reload registry so it picks up any other changes
                providers_cfg = {
                    "providers": config.get("providers", {}),
                    "defaults": config.get("defaults", {}),
                }
                self._registry.reload_from_yaml(providers_cfg)
                logger.info(
                    "Model %s set as default for %s and saved to YAML", model_key, use_case
                )
            except Exception as exc:
                return ToolResult(
                    tool_name=self.name,
                    success=True,
                    output={
                        "set": True,
                        "persisted": False,
                        "persist_error": str(exc),
                    },
                )

        return ToolResult(
            tool_name=self.name,
            success=True,
            output={"use_case": use_case, "model": model_key, "persisted": persist},
        )

    async def _list_providers(self) -> ToolResult:
        providers_cfg = self._registry._providers_cfg
        result = []
        for name, cfg in providers_cfg.items():
            api_key_env = cfg.get("api_key_env")
            has_key = bool(os.environ.get(api_key_env, "")) if api_key_env else True
            result.append(
                {
                    "name": name,
                    "display_name": cfg.get("name", name),
                    "api_key_env": api_key_env,
                    "has_api_key": has_key,
                    "model_count": len(cfg.get("models", {})),
                    "base_url": cfg.get("base_url"),
                }
            )
        return ToolResult(tool_name=self.name, success=True, output=result)

    async def _reload_config(self) -> ToolResult:
        yaml_path = self._config_dir / "llm_providers.yaml"
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            providers_cfg = {
                "providers": config.get("providers", {}),
                "defaults": config.get("defaults", {}),
            }
            self._registry.reload_from_yaml(providers_cfg)
            model_count = len(self._registry.list_models())
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"reloaded": True, "model_count": model_count},
            )
        except Exception as exc:
            return ToolResult(tool_name=self.name, success=False, error=str(exc))
