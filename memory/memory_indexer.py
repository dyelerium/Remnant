"""Memory indexer — bulk scan Markdown files → chunk → embed → Redis."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from .chunking import auto_chunk
from .memory_schema import ChunkType, ImportanceLabel

logger = logging.getLogger(__name__)


class MemoryIndexer:
    """Scan memory/ directory, chunk all Markdown files, and store in Redis."""

    def __init__(self, redis_client, embedding_provider, config: dict) -> None:
        self.redis = redis_client
        self.embedder = embedding_provider
        self.config = config
        self._memory_root = Path(config.get("memory_root", "./memory"))
        self._chunk_max = config.get("recording", {}).get("chunk_max_tokens", 300)
        self._chunk_overlap = config.get("recording", {}).get("chunk_overlap_tokens", 50)
        self._default_ttl = config.get("recording", {}).get("default_ttl_days", 90)

    def scan_and_index(
        self,
        project_id: Optional[str] = None,
        glob: str = "**/*.md",
        force: bool = False,
    ) -> int:
        """
        Scan memory_root for Markdown files, chunk and embed them into Redis.

        Args:
            project_id: Restrict to this project subdirectory (optional).
            glob:  Glob pattern relative to memory_root.
            force: Re-index even if chunk already exists (by text hash).

        Returns:
            Number of chunks stored.
        """
        search_root = self._memory_root
        if project_id:
            search_root = search_root / "projects" / project_id

        files = list(search_root.glob(glob))
        logger.info("Indexing %d Markdown files from %s", len(files), search_root)

        total = 0
        for filepath in files:
            try:
                n = self._index_file(filepath, project_id=project_id, force=force)
                total += n
            except Exception as exc:
                logger.error("Failed to index %s: %s", filepath, exc)

        logger.info("Indexed %d chunks total", total)
        return total

    def _index_file(
        self,
        filepath: Path,
        project_id: Optional[str],
        force: bool,
    ) -> int:
        """Index a single Markdown file."""
        text = filepath.read_text(encoding="utf-8")
        if not text.strip():
            return 0

        rel_path = str(filepath.relative_to(self._memory_root))
        chunk_type = self._infer_type(filepath, project_id)

        chunks = auto_chunk(text, self._chunk_max, self._chunk_overlap)

        stored = 0
        embeddings = self.embedder.embed_batch([c.text for c in chunks])

        now = int(time.time())

        for chunk, embedding in zip(chunks, embeddings):
            import hashlib
            text_hash = hashlib.sha256(chunk.text.encode()).hexdigest()

            # Skip if already indexed (unless force)
            if not force and self._chunk_exists(text_hash):
                continue

            chunk_id = str(uuid.uuid4())
            chunk_data = {
                "file_path": rel_path,
                "chunk_type": chunk_type,
                "project_id": project_id or "",
                "heading": chunk.heading or "",
                "heading_level": chunk.heading_level,
                "created_at": now,
                "last_used_at": now,
                "useful_score": 0.0,
                "embedding": embedding.tobytes(),
                "text_excerpt": chunk.text,
                "text_hash": text_hash,
                "source": "indexer",
                "importance_label": ImportanceLabel.UNSCORED.value,
            }

            self.redis.store_chunk(
                chunk_id,
                chunk_data,
                ttl_days=self._default_ttl,
                importance=ImportanceLabel.UNSCORED,
            )
            stored += 1

        logger.debug("Indexed %d/%d chunks from %s", stored, len(chunks), filepath.name)
        return stored

    def _chunk_exists(self, text_hash: str) -> bool:
        """Check if a chunk with this text_hash already exists in Redis."""
        # Scan is expensive; use a secondary lookup set
        return bool(self.redis.r.sismember("chunks:hash_index", text_hash))

    def _infer_type(self, filepath: Path, project_id: Optional[str]) -> str:
        """Infer chunk type from file name/path."""
        name = filepath.stem.lower()
        if name == "memory":
            return ChunkType.PREFERENCE.value
        if "project" in str(filepath).lower() or project_id:
            return ChunkType.PROJECT.value
        if name.startswith("20"):  # Date-stamped daily log
            return ChunkType.LOG.value
        return ChunkType.LOG.value
