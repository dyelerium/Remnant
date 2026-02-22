"""Memory retriever — HNSW KNN search + metadata filter + token budget + reinforcement weighting."""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
from redis.commands.search.query import Query

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """Retrieve memory chunks using hybrid vector + metadata search."""

    def __init__(self, redis_client, embedding_provider, config: dict) -> None:
        self.redis = redis_client
        self.embedder = embedding_provider
        self.config = config
        self._retrieval_cfg = config.get("retrieval", {})

    def retrieve(
        self,
        query_text: str,
        project_id: Optional[str] = None,
        chunk_types: Optional[list[str]] = None,
        max_tokens: Optional[int] = None,
        max_chunks: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> list[dict]:
        """
        Retrieve relevant memory chunks using hybrid search.

        Args:
            query_text:    Natural language query.
            project_id:    Optional project scope filter.
            chunk_types:   Optional list of chunk types to filter.
            max_tokens:    Token budget (overrides config).
            max_chunks:    Max number of chunks (overrides config).
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of chunk dicts, sorted by weighted score (best first).
        """
        max_tokens = max_tokens or self._retrieval_cfg.get("max_tokens", 800)
        max_chunks = max_chunks or self._retrieval_cfg.get("max_chunks", 15)
        min_sim = min_similarity or self._retrieval_cfg.get("min_similarity", 0.3)
        hybrid = self._retrieval_cfg.get("hybrid_filter", True)

        query_vector = self.embedder.embed(query_text)

        # Build KNN query with optional metadata filters
        knn_count = max_chunks * 2
        filter_str = self._build_filter(project_id, chunk_types, hybrid)

        if filter_str:
            base_query = f"({filter_str})=>[KNN {knn_count} @embedding $vec AS score]"
        else:
            base_query = f"*=>[KNN {knn_count} @embedding $vec AS score]"

        q = (
            Query(base_query)
            .return_fields(
                "file_path",
                "text_excerpt",
                "chunk_type",
                "project_id",
                "useful_score",
                "last_used_at",
                "heading",
                "importance_label",
                "score",
            )
            .sort_by("score", asc=True)
            .paging(0, knn_count)
            .dialect(2)
        )

        try:
            results = self.redis.r.ft(self.redis.index_name).search(
                q, query_params={"vec": query_vector.tobytes()}
            )
        except Exception as exc:
            logger.error("Vector search failed: %s", exc)
            return []

        chunks = []
        total_tokens = 0

        for doc in results.docs:
            # COSINE distance: 0 = identical, 2 = opposite → convert to similarity
            try:
                similarity = 1.0 - (float(doc.score) / 2.0)
            except (AttributeError, ValueError):
                continue

            if similarity < min_sim:
                continue

            useful_score = float(getattr(doc, "useful_score", 0.0) or 0.0)
            weighted_score = similarity * (1.0 + self._sigmoid(useful_score))

            chunk_tokens = len(getattr(doc, "text_excerpt", "") or "") // 4

            if total_tokens + chunk_tokens > max_tokens:
                break

            chunk_id = doc.id.replace("memory:chunk:", "")

            chunks.append(
                {
                    "id": chunk_id,
                    "file_path": getattr(doc, "file_path", ""),
                    "text_excerpt": getattr(doc, "text_excerpt", ""),
                    "chunk_type": getattr(doc, "chunk_type", ""),
                    "project_id": getattr(doc, "project_id", ""),
                    "heading": getattr(doc, "heading", ""),
                    "importance_label": getattr(doc, "importance_label", "UNSCORED"),
                    "similarity": round(similarity, 4),
                    "weighted_score": round(weighted_score, 4),
                    "useful_score": useful_score,
                    "tokens": chunk_tokens,
                }
            )
            total_tokens += chunk_tokens

            # Reinforce: update last_used_at + extend TTL
            self.redis.r.hset(
                f"memory:chunk:{chunk_id}", "last_used_at", int(time.time())
            )
            self.redis.extend_ttl(chunk_id)

            if len(chunks) >= max_chunks:
                break

        chunks.sort(key=lambda x: x["weighted_score"], reverse=True)
        return chunks

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_for_prompt(self, chunks: list[dict]) -> str:
        """Format retrieved chunks for injection into an LLM prompt."""
        if not chunks:
            return ""

        lines = ["=== Remnant Memory Snippets (authoritative context) ===\n"]
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("file_path", "unknown")
            ctype = chunk.get("chunk_type", "")
            heading = chunk.get("heading", "")
            heading_str = f" § {heading}" if heading else ""
            lines.append(f"[{i}] {source}{heading_str} (type: {ctype})")
            lines.append(f'"""\n{chunk["text_excerpt"]}\n"""\n')

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(
        project_id: Optional[str],
        chunk_types: Optional[list[str]],
        hybrid: bool,
    ) -> str:
        filters = []
        if project_id and hybrid:
            # Escape special chars in project_id
            safe_pid = project_id.replace("-", r"\-")
            filters.append(f"@project_id:{{{safe_pid}}}")
        if chunk_types and hybrid:
            type_filter = "|".join(t.replace("-", r"\-") for t in chunk_types)
            filters.append(f"@chunk_type:{{{type_filter}}}")
        return "&".join(filters) if len(filters) > 1 else (filters[0] if filters else "")

    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + np.exp(-float(x)))
