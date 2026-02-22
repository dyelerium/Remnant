"""Config loader — merge YAML files + env var overrides into typed Pydantic settings."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_VAR_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${VAR:default} placeholders in YAML values."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default or "")
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_yaml(path: str | Path) -> dict:
    """Load a YAML file and resolve env var placeholders."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return _resolve_env_vars(raw)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class RemnantSettings(BaseSettings):
    """Typed top-level settings (env vars take precedence over YAML)."""

    model_config = SettingsConfigDict(
        env_prefix="REMNANT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    master_key: str = Field(default="", alias="REMNANT_MASTER_KEY")
    env: str = Field(default="development", alias="REMNANT_ENV")
    log_level: str = Field(default="INFO", alias="REMNANT_LOG_LEVEL")


class ConfigLoader:
    """Loads and merges all Remnant YAML config files."""

    CONFIG_FILES = [
        "config/remnant.yaml",
        "config/llm_providers.yaml",
        "config/budget.yaml",
        "config/security.yaml",
        "config/projects.yaml",
        "config/agents.yaml",
    ]

    def __init__(self, config_dir: str | Path = "config") -> None:
        self._config_dir = Path(config_dir)
        self._merged: Optional[dict] = None

    def load(self) -> dict:
        """Load, merge, and cache all config files."""
        if self._merged is not None:
            return self._merged

        merged: dict = {}
        for rel_path in self.CONFIG_FILES:
            full = self._config_dir.parent / rel_path
            data = load_yaml(full)
            merged = _deep_merge(merged, data)

        # Inject Pydantic settings (env vars take priority)
        try:
            settings = RemnantSettings()
            if settings.master_key:
                merged.setdefault("secrets", {})["master_key"] = settings.master_key
            merged["env"] = settings.env
            merged["log_level"] = settings.log_level
        except Exception:
            pass

        self._merged = merged
        return merged

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def reload(self) -> dict:
        """Force reload all configs."""
        self._merged = None
        return self.load()


@lru_cache(maxsize=1)
def get_config() -> ConfigLoader:
    """Singleton config loader."""
    return ConfigLoader()
