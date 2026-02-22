"""Tests: budget cap enforcement, fallback trigger, stop behaviour."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.budget_manager import BudgetManager, BudgetExceeded
from core.provider_registry import ModelSpec


@pytest.fixture
def mock_redis():
    store = {}

    r = MagicMock()

    def get(key):
        return str(store.get(key, 0)).encode() if store.get(key) else None

    def incrby(key, amount):
        store[key] = store.get(key, 0) + amount
        return store[key]

    def incrbyfloat(key, amount):
        store[key] = store.get(key, 0.0) + amount
        return store[key]

    r.get.side_effect = get
    r.incrby.side_effect = incrby
    r.incrbyfloat.side_effect = incrbyfloat
    r.expire.return_value = True

    redis_client = MagicMock()
    redis_client.r = r
    return redis_client, store


@pytest.fixture
def spec():
    return ModelSpec(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_per_1k_input=0.00025,
        cost_per_1k_output=0.00125,
        api_key="test-key",
    )


class TestBudgetManager:
    def _make_budget(self, mock_redis, max_tokens=None, max_cost=None):
        config = {
            "global": {
                "max_tokens_per_day": max_tokens,
                "max_cost_usd_per_day": max_cost,
                "warn_at_fraction": 0.8,
                "on_budget_exhausted": "stop",
                "fallback_chain": [],
            },
            "projects": {},
            "window_seconds": 86400,
            "redis_prefix": "budget",
        }
        client, store = mock_redis
        return BudgetManager(client, config), store

    def test_pre_check_within_budget(self, mock_redis, spec):
        budget, _ = self._make_budget(mock_redis, max_tokens=1_000_000)
        # Should not raise
        budget.pre_check(spec, 100)

    def test_pre_check_over_token_cap(self, mock_redis, spec):
        budget, store = self._make_budget(mock_redis, max_tokens=100)
        # Pre-fill counter to exceed cap
        wk = budget._window_key("day")
        key = budget._redis_key(f"tokens:{wk}")
        store[key] = 200  # Already over cap

        with pytest.raises(BudgetExceeded, match="daily token cap"):
            budget.pre_check(spec, 50)

    def test_record_usage(self, mock_redis, spec):
        budget, store = self._make_budget(mock_redis)
        budget.record_usage(spec, tokens_in=1000, tokens_out=500)

        wk = budget._window_key("day")
        key = budget._redis_key(f"tokens:{wk}")
        assert store.get(key, 0) == 1500

    def test_get_usage_summary(self, mock_redis, spec):
        budget, _ = self._make_budget(mock_redis, max_tokens=5_000_000, max_cost=20.0)
        budget.record_usage(spec, tokens_in=100, tokens_out=50)

        summary = budget.get_usage_summary()
        assert "tokens_today" in summary
        assert "cost_usd_today" in summary

    def test_no_cap_no_raise(self, mock_redis, spec):
        # No caps set — should never raise
        budget, _ = self._make_budget(mock_redis, max_tokens=None, max_cost=None)
        budget.pre_check(spec, 999_999)
