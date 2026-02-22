"""Curator bridge — translate Curator importance_label events → useful_score delta + TTL."""
from __future__ import annotations

import logging
from typing import Optional

from .memory_schema import ImportanceLabel, IMPORTANCE_TTL_MAP

logger = logging.getLogger(__name__)

# Score deltas applied when Curator assigns an importance label
_LABEL_SCORE_DELTA: dict[ImportanceLabel, float] = {
    ImportanceLabel.GLOBAL_HIGH: 5.0,
    ImportanceLabel.PROJECT_HIGH: 2.0,
    ImportanceLabel.EPHEMERAL: -2.0,
    ImportanceLabel.UNSCORED: 0.0,
}


class CuratorBridge:
    """Apply Curator importance_label events to Redis chunk metadata."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    def on_importance_event(
        self,
        chunk_id: str,
        label: ImportanceLabel,
        reason: Optional[str] = None,
    ) -> dict:
        """
        Handle a Curator importance assignment.

        Updates:
          - importance_label field in the chunk hash
          - useful_score (delta based on label)
          - TTL (based on label)

        Returns updated chunk metadata snippet.
        """
        key = f"memory:chunk:{chunk_id}"

        # Verify chunk exists
        exists = self.redis.r.exists(key)
        if not exists:
            logger.warning("[CURATOR] Chunk %s not found in Redis", chunk_id)
            return {}

        # Apply score delta
        delta = _LABEL_SCORE_DELTA.get(label, 0.0)
        new_score = self.redis.update_score(chunk_id, delta)

        # Update importance_label field
        self.redis.r.hset(key, "importance_label", label.value)

        # Adjust TTL
        ttl = IMPORTANCE_TTL_MAP.get(label)
        if ttl is None and label == ImportanceLabel.GLOBAL_HIGH:
            self.redis.r.persist(key)  # Remove TTL entirely
            logger.debug("[CURATOR] %s → GLOBAL_HIGH (persist)", chunk_id)
        elif ttl is not None:
            self.redis.r.expire(key, ttl)
            logger.debug("[CURATOR] %s → %s (TTL=%ds)", chunk_id, label.value, ttl)

        result = {
            "chunk_id": chunk_id,
            "label": label.value,
            "new_score": round(new_score, 4),
            "ttl_seconds": ttl,
            "reason": reason,
        }
        return result

    def batch_on_importance(self, events: list[dict]) -> list[dict]:
        """
        Process multiple importance events at once.

        Each event dict: { chunk_id, label (str or ImportanceLabel), reason? }
        """
        results = []
        for event in events:
            chunk_id = event.get("chunk_id")
            raw_label = event.get("label", ImportanceLabel.UNSCORED)
            reason = event.get("reason")

            if isinstance(raw_label, str):
                try:
                    label = ImportanceLabel(raw_label)
                except ValueError:
                    label = ImportanceLabel.UNSCORED

            elif isinstance(raw_label, ImportanceLabel):
                label = raw_label
            else:
                label = ImportanceLabel.UNSCORED

            result = self.on_importance_event(chunk_id, label, reason)
            results.append(result)

        return results
