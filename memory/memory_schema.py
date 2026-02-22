"""Memory schema — key templates, HNSW index fields, ImportanceLabel enum."""
from enum import Enum


class ImportanceLabel(str, Enum):
    """Curator-assigned importance labels driving TTL and compaction priority."""
    GLOBAL_HIGH = "GLOBAL_HIGH"      # Cross-project durable facts → no expiry
    PROJECT_HIGH = "PROJECT_HIGH"    # Project-critical, long TTL (365 days)
    EPHEMERAL = "EPHEMERAL"          # Low-value, short TTL (7 days)
    UNSCORED = "UNSCORED"            # Not yet evaluated by Curator


class ChunkType(str, Enum):
    """Semantic type of a memory chunk."""
    PREFERENCE = "preference"
    IDENTITY = "identity"
    RULE = "rule"
    DECISION = "decision"
    LEARNING = "learning"
    LOG = "log"
    PROJECT = "project"
    SUMMARY = "summary"
    FACT = "fact"


# --- Key templates ---
CHUNK_KEY_PREFIX = "memory:chunk:"
SECRET_KEY_PREFIX = "remnant:secret:"
BUDGET_KEY_PREFIX = "budget:"
SECURITY_BLOCKED_PREFIX = "security:blocked:"
PROJECT_CHUNKS_KEY = "chunks:by_project:{project_id}"
TYPE_CHUNKS_KEY = "chunks:by_type:{chunk_type}"
SCORE_ZSET_KEY = "chunks:by_score"
RECENT_ZSET_KEY = "chunks:recent"


def chunk_key(chunk_id: str) -> str:
    return f"{CHUNK_KEY_PREFIX}{chunk_id}"


def project_chunks_key(project_id: str) -> str:
    return PROJECT_CHUNKS_KEY.format(project_id=project_id)


def type_chunks_key(chunk_type: str) -> str:
    return TYPE_CHUNKS_KEY.format(chunk_type=chunk_type)


# --- TTL constants (seconds) ---
TTL_GLOBAL_HIGH = None          # No expiry
TTL_PROJECT_HIGH = 365 * 86400  # 1 year
TTL_DEFAULT = 90 * 86400        # 90 days
TTL_EPHEMERAL = 7 * 86400       # 7 days
TTL_BLOCKED_LOG = 30 * 86400    # 30 days

IMPORTANCE_TTL_MAP = {
    ImportanceLabel.GLOBAL_HIGH: TTL_GLOBAL_HIGH,
    ImportanceLabel.PROJECT_HIGH: TTL_PROJECT_HIGH,
    ImportanceLabel.EPHEMERAL: TTL_EPHEMERAL,
    ImportanceLabel.UNSCORED: TTL_DEFAULT,
}


# --- HNSW index field definitions (for redis_client.py) ---
INDEX_NAME = "idx:memory_chunks"
INDEX_PREFIX = "memory:chunk:"
EMBEDDING_DIM = 384

HNSW_PARAMS_DEFAULT = {
    "TYPE": "FLOAT32",
    "DIM": EMBEDDING_DIM,
    "DISTANCE_METRIC": "COSINE",
    "INITIAL_CAP": 10000,
    "M": 16,
    "EF_CONSTRUCTION": 200,
}

FLAT_PARAMS_DEFAULT = {
    "TYPE": "FLOAT32",
    "DIM": EMBEDDING_DIM,
    "DISTANCE_METRIC": "COSINE",
}
