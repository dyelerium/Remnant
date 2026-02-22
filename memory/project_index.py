"""Per-project memory scoping and isolation helpers."""
from __future__ import annotations

import logging
from typing import Optional

from .memory_schema import project_chunks_key

logger = logging.getLogger(__name__)


class ProjectIndex:
    """Helpers for project-scoped memory access."""

    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    def list_chunk_ids(self, project_id: str) -> list[str]:
        """Return all chunk IDs belonging to a project."""
        raw = self.redis.r.smembers(project_chunks_key(project_id))
        return [cid.decode() if isinstance(cid, bytes) else cid for cid in raw]

    def count_chunks(self, project_id: str) -> int:
        return self.redis.r.scard(project_chunks_key(project_id))

    def delete_all(self, project_id: str) -> int:
        """Delete all memory chunks for a project. Returns count deleted."""
        chunk_ids = self.list_chunk_ids(project_id)
        for cid in chunk_ids:
            self.redis.delete_chunk(cid)
        return len(chunk_ids)

    def tag_chunk(self, chunk_id: str, project_id: str) -> None:
        """Associate an existing chunk with a project."""
        self.redis.r.sadd(project_chunks_key(project_id), chunk_id)
        self.redis.r.hset(f"memory:chunk:{chunk_id}", "project_id", project_id)

    def get_project_stats(self, project_id: str) -> dict:
        """Return basic stats for a project's memory."""
        chunk_ids = self.list_chunk_ids(project_id)
        total_score = 0.0
        for cid in chunk_ids:
            chunk = self.redis.get_chunk(cid)
            if chunk:
                total_score += float(chunk.get("useful_score", 0.0) or 0.0)
        return {
            "project_id": project_id,
            "chunk_count": len(chunk_ids),
            "total_useful_score": round(total_score, 2),
        }
