"""Redis client — pooled connection, HNSW index management, chunk storage."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import redis
from redis.commands.search.field import (
    NumericField,
    TagField,
    TextField,
    VectorField,
)
from redis.commands.search.index_definition import IndexDefinition, IndexType

from .memory_schema import (
    CHUNK_KEY_PREFIX,
    HNSW_PARAMS_DEFAULT,
    FLAT_PARAMS_DEFAULT,
    INDEX_NAME,
    INDEX_PREFIX,
    RECENT_ZSET_KEY,
    SCORE_ZSET_KEY,
    ImportanceLabel,
    chunk_key,
    project_chunks_key,
    type_chunks_key,
    IMPORTANCE_TTL_MAP,
    TTL_DEFAULT,
)

logger = logging.getLogger(__name__)


class RemnantRedisClient:
    """Pooled Redis client with HNSW vector index management."""

    def __init__(self, config: dict) -> None:
        self.config = config
        redis_cfg = config["redis"]

        self._pool = redis.ConnectionPool(
            host=redis_cfg.get("host", "localhost"),
            port=int(redis_cfg.get("port", 6379)),
            db=int(redis_cfg.get("db", 0)),
            password=redis_cfg.get("password") or None,
            decode_responses=False,  # Binary for embeddings
            max_connections=int(redis_cfg.get("max_connections", 20)),
        )
        self.r: redis.Redis = redis.Redis(connection_pool=self._pool)

        vec_cfg = config.get("vector_index", {})
        self.index_name: str = vec_cfg.get("name", INDEX_NAME)
        self.algorithm: str = vec_cfg.get("algorithm", "HNSW")

        self._dim: int = config.get("embedding", {}).get("dimensions", 384)
        self._metric: str = vec_cfg.get("distance_metric", "COSINE")

        self._ttl_default: int = (
            config.get("recording", {}).get("default_ttl_days", 90) * 86400
        )
        self._ttl_extend: bool = config.get("aging", {}).get(
            "ttl_extension_on_use", True
        )
        self._ttl_extend_days: int = (
            config.get("aging", {}).get("ttl_extension_days", 30) * 86400
        )

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def ensure_index(self) -> None:
        """Create HNSW index if it does not yet exist (idempotent)."""
        try:
            self.r.ft(self.index_name).info()
            logger.debug("Index %s already exists", self.index_name)
            return
        except Exception:
            pass  # Index does not exist — create it

        if self.algorithm == "HNSW":
            vec_cfg = self.config.get("vector_index", {})
            vector_params = {
                **HNSW_PARAMS_DEFAULT,
                "DIM": self._dim,
                "DISTANCE_METRIC": self._metric,
                "M": vec_cfg.get("hnsw_m", 16),
                "EF_CONSTRUCTION": vec_cfg.get("hnsw_ef_construction", 200),
            }
        else:
            vector_params = {
                **FLAT_PARAMS_DEFAULT,
                "DIM": self._dim,
                "DISTANCE_METRIC": self._metric,
            }

        self.r.ft(self.index_name).create_index(
            [
                VectorField("embedding", self.algorithm, vector_params),
                TextField("text_excerpt", weight=1.0),
                TextField("file_path"),
                TagField("chunk_type"),
                TagField("project_id"),
                TagField("importance_label"),
                NumericField("created_at", sortable=True),
                NumericField("last_used_at", sortable=True),
                NumericField("useful_score", sortable=True),
            ],
            definition=IndexDefinition(
                prefix=[INDEX_PREFIX],
                index_type=IndexType.HASH,
            ),
        )
        logger.info("Created vector index: %s", self.index_name)

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    def store_chunk(
        self,
        chunk_id: str,
        chunk_data: dict,
        ttl_days: Optional[int] = None,
        importance: ImportanceLabel = ImportanceLabel.UNSCORED,
    ) -> None:
        """Store a memory chunk hash and update secondary indices."""
        key = chunk_key(chunk_id)

        # Resolve TTL from importance label
        importance_ttl = IMPORTANCE_TTL_MAP.get(importance)
        if importance_ttl is None and importance == ImportanceLabel.GLOBAL_HIGH:
            ttl_secs = None  # Persist forever
        elif importance_ttl is not None:
            ttl_secs = importance_ttl
        elif ttl_days is not None:
            ttl_secs = ttl_days * 86400
        else:
            ttl_secs = self._ttl_default

        chunk_data["importance_label"] = importance.value
        self.r.hset(key, mapping=chunk_data)

        if ttl_secs is not None:
            self.r.expire(key, ttl_secs)

        # Secondary indices
        project_id = chunk_data.get("project_id", "")
        if project_id:
            self.r.sadd(project_chunks_key(project_id), chunk_id)

        chunk_type = chunk_data.get("chunk_type", "")
        if chunk_type:
            self.r.sadd(type_chunks_key(chunk_type), chunk_id)

        score = float(chunk_data.get("useful_score", 0.0))
        ts = float(chunk_data.get("last_used_at", time.time()))
        self.r.zadd(SCORE_ZSET_KEY, {chunk_id: score})
        self.r.zadd(RECENT_ZSET_KEY, {chunk_id: ts})

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        """Retrieve a single chunk by ID."""
        data = self.r.hgetall(chunk_key(chunk_id))
        if not data:
            return None
        return {
            k.decode() if isinstance(k, bytes) else k: (
                v.decode() if isinstance(v, bytes) and k not in (b"embedding",) else v
            )
            for k, v in data.items()
        }

    def delete_chunk(self, chunk_id: str) -> None:
        """Delete a chunk and clean up secondary indices."""
        data = self.get_chunk(chunk_id)
        if not data:
            return

        self.r.delete(chunk_key(chunk_id))

        if data.get("project_id"):
            self.r.srem(project_chunks_key(data["project_id"]), chunk_id)
        if data.get("chunk_type"):
            self.r.srem(type_chunks_key(data["chunk_type"]), chunk_id)

        self.r.zrem(SCORE_ZSET_KEY, chunk_id)
        self.r.zrem(RECENT_ZSET_KEY, chunk_id)

    def update_score(self, chunk_id: str, delta: float) -> float:
        """Increment useful_score by delta. Returns new score."""
        key = chunk_key(chunk_id)
        new_score = self.r.hincrbyfloat(key, "useful_score", delta)
        new_score = float(new_score)
        self.r.zadd(SCORE_ZSET_KEY, {chunk_id: new_score})
        return new_score

    def extend_ttl(self, chunk_id: str, days: Optional[int] = None) -> None:
        """Extend TTL on access (sliding window pattern)."""
        if not self._ttl_extend:
            return
        secs = (days * 86400) if days else self._ttl_extend_days
        self.r.expire(chunk_key(chunk_id), secs)
        self.r.hset(chunk_key(chunk_id), "last_used_at", int(time.time()))

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return self.r.ping()
        except Exception:
            return False

    def pipeline(self):
        return self.r.pipeline()
