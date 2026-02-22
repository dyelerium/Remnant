"""Tests: provider selection, fallback, overrides."""
from __future__ import annotations

import pytest

from core.provider_registry import ModelSpec, ProviderRegistry


_PROVIDERS_CONFIG = {
    "providers": {
        "anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "base_url": None,
            "models": {
                "claude-sonnet-4-6": {
                    "context_window": 200000,
                    "cost_per_1k_input": 0.003,
                    "cost_per_1k_output": 0.015,
                    "use_cases": ["chat", "planning", "default"],
                },
                "claude-haiku-4-5-20251001": {
                    "context_window": 200000,
                    "cost_per_1k_input": 0.00025,
                    "cost_per_1k_output": 0.00125,
                    "use_cases": ["chat", "compaction", "fast"],
                },
            },
        },
        "ollama": {
            "api_key_env": None,
            "base_url": "http://localhost:11434",
            "models": {
                "llama3.1:8b": {
                    "context_window": 128000,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "use_cases": ["chat", "fast"],
                },
            },
        },
    },
    "defaults": {
        "chat": "anthropic/claude-sonnet-4-6",
        "planning": "anthropic/claude-sonnet-4-6",
        "fast": "anthropic/claude-haiku-4-5-20251001",
        "compaction": "anthropic/claude-haiku-4-5-20251001",
    },
}


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")
    return ProviderRegistry(_PROVIDERS_CONFIG)


class TestProviderRegistry:
    def test_resolve_chat(self, registry):
        spec = registry.resolve("chat")
        assert spec.provider == "anthropic"
        assert "claude-sonnet" in spec.model

    def test_resolve_fast(self, registry):
        spec = registry.resolve("fast")
        assert "haiku" in spec.model

    def test_resolve_override(self, registry):
        spec = registry.resolve("chat", override="anthropic/claude-haiku-4-5-20251001")
        assert "haiku" in spec.model

    def test_resolve_unknown_raises(self, registry):
        with pytest.raises(ValueError, match="No model available"):
            registry.resolve("nonexistent_use_case_xyz")

    def test_list_models(self, registry):
        models = registry.list_models()
        assert len(models) >= 2

    def test_list_models_filtered(self, registry):
        fast_models = registry.list_models(use_case="fast")
        assert all("fast" in m.use_cases for m in fast_models)

    def test_get_specific_model(self, registry):
        spec = registry.get("anthropic/claude-sonnet-4-6")
        assert spec is not None
        assert spec.model == "claude-sonnet-4-6"

    def test_get_nonexistent_returns_none(self, registry):
        assert registry.get("nonexistent/model") is None

    def test_model_costs(self, registry):
        spec = registry.resolve("chat")
        assert spec.cost_per_1k_input > 0
        assert spec.cost_per_1k_output > 0
        assert spec.context_window > 0

    def test_free_provider_zero_cost(self, registry):
        spec = registry.get("ollama/llama3.1:8b")
        assert spec is not None
        assert spec.cost_per_1k_input == 0.0
        assert spec.cost_per_1k_output == 0.0
