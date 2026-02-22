"""Cross-project and identity/preferences memory helpers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .memory_schema import SCORE_ZSET_KEY, RECENT_ZSET_KEY, ImportanceLabel

logger = logging.getLogger(__name__)


class GlobalIndex:
    """Helpers for global (cross-project) memory access."""

    def __init__(self, redis_client, config: dict) -> None:
        self.redis = redis_client
        self._memory_root = Path(config.get("memory_root", "./memory"))

    # ------------------------------------------------------------------
    # High-importance identity/preferences chunks
    # ------------------------------------------------------------------

    def get_global_high_chunks(self, limit: int = 50) -> list[dict]:
        """Return chunks labelled GLOBAL_HIGH sorted by useful_score."""
        # Scan chunks:by_score zset from highest to lowest
        chunk_ids = self.redis.r.zrevrange(SCORE_ZSET_KEY, 0, limit * 3)
        results = []
        for cid_bytes in chunk_ids:
            cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
            chunk = self.redis.get_chunk(cid)
            if not chunk:
                continue
            if chunk.get("importance_label") == ImportanceLabel.GLOBAL_HIGH.value:
                chunk["id"] = cid
                results.append(chunk)
            if len(results) >= limit:
                break
        return results

    def get_identity_chunks(self, limit: int = 20) -> list[dict]:
        """Return preference/identity/rule chunks from MEMORY.md."""
        all_chunks = self.get_global_high_chunks(limit * 5)
        identity = [
            c for c in all_chunks
            if c.get("chunk_type") in ("preference", "identity", "rule")
        ]
        return identity[:limit]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def get_recent_chunks(self, limit: int = 20) -> list[dict]:
        """Return most recently accessed chunks (any project)."""
        chunk_ids = self.redis.r.zrevrange(RECENT_ZSET_KEY, 0, limit - 1)
        results = []
        for cid_bytes in chunk_ids:
            cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
            chunk = self.redis.get_chunk(cid)
            if chunk:
                chunk["id"] = cid
                results.append(chunk)
        return results

    def memory_root_stats(self) -> dict:
        """Return stats about the Markdown source-of-truth files."""
        md_files = list(self._memory_root.glob("**/*.md"))
        total_bytes = sum(f.stat().st_size for f in md_files if f.exists())
        return {
            "markdown_files": len(md_files),
            "total_bytes": total_bytes,
            "memory_root": str(self._memory_root),
        }
