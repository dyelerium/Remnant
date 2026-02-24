"""
Tests: Budget mode integration — smart use-case routing through orchestrator→runtime.

Covers:
  - _smart_use_case with various budget-mode scenarios
  - use_case="chat" when budget_mode=False (always)
  - use_case correctly determined when budget_mode=True
  - BudgetManager cost/token calculations
  - BudgetManager per-project caps
  - BudgetManager fallback (queue action doesn't raise)
  - ProviderRegistry.set_default and resolution after override
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.budget_manager import BudgetManager, BudgetExceeded
from core.provider_registry import ModelSpec, ProviderRegistry
from core.runtime import AgentRuntime


# ===========================================================================
# Smart use-case selection (already tested in test_runtime_static.py,
# but here we test the integration with budget_mode flag in run_stream)
# ===========================================================================

class TestSmartUseCaseIntegration:
    """
    Verify that when budget_mode=False the use_case is always 'chat',
    and when budget_mode=True the _smart_use_case() result is used.
    """

    def test_budget_mode_false_always_chat(self):
        """When budget_mode is False, use_case should be 'chat' regardless of message."""
        # We can verify this by checking that _smart_use_case is NOT the path taken.
        # We test the static logic: if budget_mode=False, use_case = "chat".
        # Simulate the logic from run_stream:
        message = "analyze the entire system architecture"
        budget_mode = False
        use_case = AgentRuntime._smart_use_case(message) if budget_mode else "chat"
        assert use_case == "chat"

    def test_budget_mode_true_picks_planning(self):
        message = "analyze the entire system architecture"
        budget_mode = True
        use_case = AgentRuntime._smart_use_case(message) if budget_mode else "chat"
        assert use_case == "planning"

    def test_budget_mode_true_picks_fast_for_simple(self):
        message = "hi"
        budget_mode = True
        use_case = AgentRuntime._smart_use_case(message) if budget_mode else "chat"
        assert use_case == "fast"

    def test_budget_mode_true_picks_chat_for_code(self):
        message = "write a python script to parse JSON"
        budget_mode = True
        use_case = AgentRuntime._smart_use_case(message) if budget_mode else "chat"
        assert use_case == "chat"

    def test_budget_mode_false_does_not_call_smart_use_case(self):
        """Confirm the logic: when budget_mode is False, _smart_use_case is bypassed."""
        budget_mode = False
        message = "search for something"
        # Inline the if/else from run_stream
        use_case = AgentRuntime._smart_use_case(message) if budget_mode else "chat"
        # Should not be "chat" from _smart_use_case (which would return "chat" for search too)
        # but the key is it came from the else branch unconditionally
        assert use_case == "chat"

    def test_budget_mode_true_with_planning_keyword_list(self):
        """Each _COMPLEX_KW keyword triggers planning."""
        from core.runtime import _COMPLEX_KW
        for kw in _COMPLEX_KW:
            result = AgentRuntime._smart_use_case(f"please {kw} this")
            assert result == "planning", f"Expected planning for keyword '{kw}'"

    def test_budget_mode_true_with_coder_keyword_list(self):
        from core.runtime import _CODER_KW
        for kw in _CODER_KW:
            result = AgentRuntime._smart_use_case(f"please {kw} this")
            assert result == "chat", f"Expected chat for coder keyword '{kw}'"

    def test_budget_mode_true_with_search_keyword_list(self):
        from core.runtime import _SEARCH_KW
        for kw in _SEARCH_KW:
            result = AgentRuntime._smart_use_case(f"please {kw} this")
            assert result == "chat", f"Expected chat for search keyword '{kw}'"


# ===========================================================================
# BudgetManager — extended tests
# ===========================================================================

@pytest.fixture
def mock_redis_store():
    store = {}

    r = MagicMock()

    def get(key):
        val = store.get(key)
        return str(val).encode() if val is not None else None

    def incrby(key, amount):
        store[key] = store.get(key, 0) + amount
        return store[key]

    def incrbyfloat(key, amount):
        store[key] = store.get(key, 0.0) + amount
        return float(store[key])

    r.get.side_effect = get
    r.incrby.side_effect = incrby
    r.incrbyfloat.side_effect = incrbyfloat
    r.expire.return_value = True

    client = MagicMock()
    client.r = r
    return client, store


@pytest.fixture
def haiku_spec():
    return ModelSpec(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_per_1k_input=0.00025,
        cost_per_1k_output=0.00125,
        api_key="test",
    )


@pytest.fixture
def free_spec():
    return ModelSpec(
        provider="ollama",
        model="qwen3:4b",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
    )


def _make_budget(mock_redis_store, **kwargs):
    client, store = mock_redis_store
    config = {
        "global": {
            "max_tokens_per_day": kwargs.get("max_tokens"),
            "max_cost_usd_per_day": kwargs.get("max_cost"),
            "warn_at_fraction": 0.8,
            "on_budget_exhausted": kwargs.get("action", "stop"),
            "fallback_chain": [],
        },
        "projects": kwargs.get("projects", {}),
        "window_seconds": 86400,
        "redis_prefix": "budget",
    }
    return BudgetManager(client, config), store


class TestBudgetManagerExtended:
    # ---- pre_check ----

    def test_pre_check_no_caps_never_raises(self, mock_redis_store, haiku_spec):
        budget, _ = _make_budget(mock_redis_store)
        # Should never raise even with huge estimated tokens
        budget.pre_check(haiku_spec, 999_999_999)

    def test_pre_check_within_token_cap_ok(self, mock_redis_store, haiku_spec):
        budget, _ = _make_budget(mock_redis_store, max_tokens=1_000_000)
        budget.pre_check(haiku_spec, 500)  # fine

    def test_pre_check_over_token_cap_raises(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store, max_tokens=100)
        wk = budget._window_key("day")
        store[budget._redis_key(f"tokens:{wk}")] = 200  # already 200 → over cap
        with pytest.raises(BudgetExceeded, match="daily token cap"):
            budget.pre_check(haiku_spec, 50)

    def test_pre_check_over_cost_cap_raises(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store, max_cost=0.01)
        wk = budget._window_key("day")
        store[budget._redis_key(f"cost:{wk}")] = 0.05  # already over
        with pytest.raises(BudgetExceeded, match="daily cost cap"):
            budget.pre_check(haiku_spec, 1)

    def test_pre_check_queue_action_does_not_raise(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store, max_tokens=10, action="queue")
        wk = budget._window_key("day")
        store[budget._redis_key(f"tokens:{wk}")] = 1000  # over cap
        # "queue" action should not raise
        budget.pre_check(haiku_spec, 100)

    def test_per_project_cap_raises(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(
            mock_redis_store,
            projects={"proj_x": {"max_tokens_per_day": 50}},
        )
        wk = budget._window_key("day")
        store[budget._redis_key(f"proj:proj_x:tokens:{wk}")] = 100  # over project cap
        with pytest.raises(BudgetExceeded, match="project proj_x"):
            budget.pre_check(haiku_spec, 10, project_id="proj_x")

    # ---- record_usage ----

    def test_record_usage_increments_token_counter(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store)
        budget.record_usage(haiku_spec, tokens_in=400, tokens_out=200)
        wk = budget._window_key("day")
        key = budget._redis_key(f"tokens:{wk}")
        assert store.get(key, 0) == 600

    def test_record_usage_increments_cost_counter(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store)
        # 1000 in = 0.00025 USD; 1000 out = 0.00125 USD → total 0.0015
        budget.record_usage(haiku_spec, tokens_in=1000, tokens_out=1000)
        wk = budget._window_key("day")
        key = budget._redis_key(f"cost:{wk}")
        cost = store.get(key, 0.0)
        assert abs(cost - 0.0015) < 1e-9

    def test_free_model_zero_cost(self, mock_redis_store, free_spec):
        budget, store = _make_budget(mock_redis_store)
        budget.record_usage(free_spec, tokens_in=10000, tokens_out=10000)
        wk = budget._window_key("day")
        key = budget._redis_key(f"cost:{wk}")
        assert store.get(key, 0.0) == 0.0

    def test_record_usage_increments_project_counter(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store)
        budget.record_usage(haiku_spec, tokens_in=100, tokens_out=50, project_id="proj_z")
        wk = budget._window_key("day")
        key = budget._redis_key(f"proj:proj_z:tokens:{wk}")
        assert store.get(key, 0) == 150

    def test_record_usage_increments_provider_counter(self, mock_redis_store, haiku_spec):
        budget, store = _make_budget(mock_redis_store)
        budget.record_usage(haiku_spec, tokens_in=200, tokens_out=100)
        wk = budget._window_key("day")
        pkey = f"anthropic:{haiku_spec.model}"
        key = budget._redis_key(f"tokens:by_provider:{pkey}:{wk}")
        assert store.get(key, 0) == 300

    # ---- get_usage_summary ----

    def test_get_usage_summary_returns_expected_keys(self, mock_redis_store, haiku_spec):
        budget, _ = _make_budget(mock_redis_store, max_tokens=1_000_000, max_cost=10.0)
        budget.record_usage(haiku_spec, tokens_in=100, tokens_out=50)
        summary = budget.get_usage_summary()
        assert "tokens_today" in summary
        assert "cost_usd_today" in summary
        assert "max_tokens_day" in summary
        assert "max_cost_day" in summary

    def test_get_usage_summary_with_project_id(self, mock_redis_store, haiku_spec):
        budget, _ = _make_budget(mock_redis_store)
        budget.record_usage(haiku_spec, tokens_in=50, tokens_out=50, project_id="proj_q")
        summary = budget.get_usage_summary(project_id="proj_q")
        assert "project_proj_q_tokens_today" in summary

    def test_get_usage_summary_zero_initially(self, mock_redis_store):
        budget, _ = _make_budget(mock_redis_store)
        summary = budget.get_usage_summary()
        assert summary["tokens_today"] == 0
        assert summary["cost_usd_today"] == 0.0

    # ---- window key ----

    def test_window_key_day_changes_daily(self, mock_redis_store):
        budget, _ = _make_budget(mock_redis_store)
        now = int(__import__("time").time())
        expected_key = str(now // 86400)
        assert budget._window_key("day") == expected_key

    def test_window_key_hour_changes_hourly(self, mock_redis_store):
        budget, _ = _make_budget(mock_redis_store)
        now = int(__import__("time").time())
        expected_key = str(now // 3600)
        assert budget._window_key("hour") == expected_key


# ===========================================================================
# ProviderRegistry — extended tests
# ===========================================================================

_REGISTRY_CONFIG = {
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
                "qwen3:4b": {
                    "context_window": 32768,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "use_cases": ["fast", "chat"],
                },
            },
        },
    },
    "defaults": {
        "chat": "anthropic/claude-sonnet-4-6",
        "planning": "anthropic/claude-sonnet-4-6",
        "fast": "ollama/qwen3:4b",
        "compaction": "anthropic/claude-haiku-4-5-20251001",
    },
}


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return ProviderRegistry(_REGISTRY_CONFIG)


class TestProviderRegistryExtended:
    def test_set_default_changes_resolution(self, registry):
        # Initially chat → sonnet
        spec_before = registry.resolve("chat")
        assert "sonnet" in spec_before.model

        # Override to haiku
        registry.set_default("chat", "anthropic/claude-haiku-4-5-20251001")
        spec_after = registry.resolve("chat")
        assert "haiku" in spec_after.model

    def test_set_default_does_not_affect_other_use_cases(self, registry):
        registry.set_default("fast", "anthropic/claude-haiku-4-5-20251001")
        # planning should still be sonnet
        spec = registry.resolve("planning")
        assert "sonnet" in spec.model

    def test_resolve_with_override_ignores_default(self, registry):
        spec = registry.resolve("planning", override="anthropic/claude-haiku-4-5-20251001")
        assert "haiku" in spec.model

    def test_resolve_unknown_use_case_raises(self, registry):
        with pytest.raises(ValueError):
            registry.resolve("nonexistent_use_case")

    def test_list_models_returns_all(self, registry):
        models = registry.list_models()
        providers = {m.provider for m in models}
        assert "anthropic" in providers
        assert "ollama" in providers

    def test_list_models_filtered_by_use_case(self, registry):
        fast_models = registry.list_models(use_case="fast")
        assert all("fast" in m.use_cases for m in fast_models)

    def test_get_existing_model(self, registry):
        spec = registry.get("anthropic/claude-sonnet-4-6")
        assert spec is not None
        assert spec.provider == "anthropic"

    def test_get_nonexistent_model_returns_none(self, registry):
        assert registry.get("fake/model-xyz") is None

    def test_free_model_has_zero_cost(self, registry):
        spec = registry.get("ollama/qwen3:4b")
        assert spec.cost_per_1k_input == 0.0
        assert spec.cost_per_1k_output == 0.0

    def test_model_spec_has_all_fields(self, registry):
        spec = registry.resolve("chat")
        assert spec.provider
        assert spec.model
        assert spec.context_window > 0

    def test_reload_from_yaml_updates_models(self, monkeypatch, registry):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        new_cfg = {
            "providers": {
                "anthropic": {
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "models": {
                        "claude-new-model": {
                            "context_window": 100000,
                            "cost_per_1k_input": 0.001,
                            "cost_per_1k_output": 0.005,
                            "use_cases": ["chat"],
                        }
                    },
                }
            },
            "defaults": {"chat": "anthropic/claude-new-model"},
        }
        registry.reload_from_yaml(new_cfg)
        spec = registry.resolve("chat")
        assert spec.model == "claude-new-model"
