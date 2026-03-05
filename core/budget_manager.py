"""Budget manager — Redis-backed usage counters, cap check, fallback/stop."""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Raised when all fallback providers are exhausted."""
    pass


class ModelCapExceeded(Exception):
    """Raised when a specific model's daily token cap is exceeded — triggers fallback."""
    pass


class BudgetManager:
    """Track token/cost usage and enforce per-project and global caps."""

    def __init__(self, redis_client, config: dict) -> None:
        self.redis = redis_client.r
        self.config = config
        budget_cfg = config.get("global", {})

        self._max_tokens_day: Optional[int] = budget_cfg.get("max_tokens_per_day")
        self._max_cost_day: Optional[float] = budget_cfg.get("max_cost_usd_per_day")
        self._max_tokens_hour: Optional[int] = budget_cfg.get("max_tokens_per_hour")
        self._max_cost_hour: Optional[float] = budget_cfg.get("max_cost_usd_per_hour")
        self._warn_at: float = budget_cfg.get("warn_at_fraction", 0.8)
        self._on_exhausted: str = budget_cfg.get("on_budget_exhausted", "stop")
        self._fallback_chain: list[str] = budget_cfg.get("fallback_chain", [])
        self._window: int = config.get("window_seconds", 86400)
        self._prefix: str = config.get("redis_prefix", "budget")
        # Per-model daily token caps: {"provider/model": max_tokens}
        self._model_caps: dict[str, int] = budget_cfg.get("model_caps", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pre_check(self, spec, estimated_tokens: int, project_id: Optional[str] = None) -> None:
        """
        Check budget before an LLM call.
        Raises BudgetExceeded if hard cap is breached.
        Logs a warning at soft threshold.
        """
        window_key_day = self._window_key("day")
        window_key_hour = self._window_key("hour")

        tokens_day = self._get_counter(f"tokens:{window_key_day}")
        cost_day = self._get_float_counter(f"cost:{window_key_day}")

        # Warn at soft threshold
        if self._max_tokens_day and tokens_day > self._max_tokens_day * self._warn_at:
            logger.warning(
                "[BUDGET] Daily token usage at %.0f%% of cap (%d/%d)",
                tokens_day / self._max_tokens_day * 100,
                tokens_day,
                self._max_tokens_day,
            )

        # Hard cap check
        if self._max_tokens_day and (tokens_day + estimated_tokens) > self._max_tokens_day:
            self._handle_exceeded("daily token cap", project_id)

        if self._max_cost_day and cost_day > self._max_cost_day:
            self._handle_exceeded("daily cost cap", project_id)

        # Per-project checks
        if project_id:
            proj_cfg = self.config.get("projects", {}).get(
                project_id,
                self.config.get("projects", {}).get("_defaults", {}),
            )
            proj_tokens_day = self._get_counter(f"proj:{project_id}:tokens:{window_key_day}")
            proj_max = proj_cfg.get("max_tokens_per_day")
            if proj_max and (proj_tokens_day + estimated_tokens) > proj_max:
                self._handle_exceeded(f"project {project_id} daily token cap", project_id)

    def check_model_cap(self, model_key: str, estimated_tokens: int = 0) -> None:
        """Raise ModelCapExceeded if this model has exceeded its daily token cap."""
        cap = self._model_caps.get(model_key)
        if not cap:
            return
        day_key = self._window_key("day")
        # Redis key stores provider:model (colon) — convert from provider/model (slash)
        pkey = model_key.replace("/", ":", 1)
        model_tokens = self._get_counter(f"tokens:by_provider:{pkey}:{day_key}")
        if (model_tokens + estimated_tokens) > cap:
            raise ModelCapExceeded(
                f"Model {model_key} daily cap of {cap:,} tokens exceeded "
                f"(used today: {model_tokens:,})"
            )

    def get_model_tokens_today(self, model_key: str) -> int:
        """Return tokens used today for a specific model key (provider/model)."""
        day_key = self._window_key("day")
        pkey = model_key.replace("/", ":", 1)
        return self._get_counter(f"tokens:by_provider:{pkey}:{day_key}")

    def set_model_cap(self, model_key: str, cap: int) -> None:
        """Set or clear (cap=0) the daily token cap for a model at runtime."""
        if cap <= 0:
            self._model_caps.pop(model_key, None)
        else:
            self._model_caps[model_key] = cap

    def get_model_caps(self) -> dict[str, int]:
        return dict(self._model_caps)

    def record_usage(
        self,
        spec,
        tokens_in: int,
        tokens_out: int,
        project_id: Optional[str] = None,
    ) -> None:
        """Record actual token usage after a successful LLM call."""
        total_tokens = tokens_in + tokens_out
        cost = (tokens_in / 1000 * spec.cost_per_1k_input) + (
            tokens_out / 1000 * spec.cost_per_1k_output
        )

        window_key_day = self._window_key("day")
        window_key_hour = self._window_key("hour")

        # Global counters
        self._increment_counter(f"tokens:{window_key_day}", total_tokens, self._window)
        self._increment_float_counter(f"cost:{window_key_day}", cost, self._window)
        self._increment_counter(f"tokens:{window_key_hour}", total_tokens, 3600)

        # Per-project counters
        if project_id:
            self._increment_counter(
                f"proj:{project_id}:tokens:{window_key_day}", total_tokens, self._window
            )

        # Provider-specific counter
        pkey = f"{spec.provider}:{spec.model}"
        self._increment_counter(f"tokens:by_provider:{pkey}:{window_key_day}", total_tokens, self._window)

    def get_usage_summary(self, project_id: Optional[str] = None) -> dict:
        """Return current usage metrics."""
        wk = self._window_key("day")
        tokens = self._get_counter(f"tokens:{wk}")
        cost = self._get_float_counter(f"cost:{wk}")

        result = {
            "tokens_today": tokens,
            "cost_usd_today": round(cost, 4),
            "max_tokens_day": self._max_tokens_day,
            "max_cost_day": self._max_cost_day,
        }

        if project_id:
            proj_tokens = self._get_counter(f"proj:{project_id}:tokens:{wk}")
            result[f"project_{project_id}_tokens_today"] = proj_tokens

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _window_key(self, granularity: str) -> str:
        now = int(time.time())
        if granularity == "hour":
            return str(now // 3600)
        return str(now // 86400)

    def _redis_key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def _get_counter(self, suffix: str) -> int:
        val = self.redis.get(self._redis_key(suffix))
        return int(val or 0)

    def _get_float_counter(self, suffix: str) -> float:
        val = self.redis.get(self._redis_key(suffix))
        return float(val or 0.0)

    def _increment_counter(self, suffix: str, amount: int, ttl: int) -> int:
        key = self._redis_key(suffix)
        new_val = self.redis.incrby(key, amount)
        self.redis.expire(key, ttl + 3600)  # slight buffer
        return new_val

    def _increment_float_counter(self, suffix: str, amount: float, ttl: int) -> float:
        key = self._redis_key(suffix)
        new_val = self.redis.incrbyfloat(key, amount)
        self.redis.expire(key, ttl + 3600)
        return float(new_val)

    def _handle_exceeded(self, cap_name: str, project_id: Optional[str]) -> None:
        action = self._on_exhausted
        logger.error("[BUDGET] Cap exceeded: %s (action=%s)", cap_name, action)
        if action == "stop":
            raise BudgetExceeded(f"Budget cap exceeded: {cap_name}")
        elif action == "error":
            raise BudgetExceeded(f"Budget cap exceeded: {cap_name}")
        # "queue" — future: add to retry queue; for now just warn
