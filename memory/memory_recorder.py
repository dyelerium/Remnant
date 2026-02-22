"""Memory recorder — security check → Markdown SoT → chunk → embed → Redis."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chunking import auto_chunk
from .memory_schema import ChunkType, ImportanceLabel

logger = logging.getLogger(__name__)


class MemoryRecorder:
    """Record new memories: validate → append Markdown → embed → Redis."""

    def __init__(
        self,
        redis_client,
        embedding_provider,
        security_manager,    # core.security.SecurityManager
        config: dict,
    ) -> None:
        self.redis = redis_client
        self.embedder = embedding_provider
        self.security = security_manager
        self.config = config

        self._memory_root = Path(config.get("memory_root", "./memory"))
        rec = config.get("recording", {})
        self._chunk_max = rec.get("chunk_max_tokens", 300)
        self._chunk_overlap = rec.get("chunk_overlap_tokens", 50)
        self._default_ttl = rec.get("default_ttl_days", 90)

    def record(
        self,
        text: str,
        chunk_type: str = "log",
        project_id: Optional[str] = None,
        source: str = "conversation",
        importance: ImportanceLabel = ImportanceLabel.UNSCORED,
    ) -> Optional[list[str]]:
        """
        Record a new memory.

        Steps:
          1. Security check via core.security.SecurityManager.is_safe()
          2. Append to source-of-truth Markdown file
          3. Chunk text
          4. Embed chunks
          5. Store in Redis

        Args:
            text:        Memory content.
            chunk_type:  ChunkType value string.
            project_id:  Optional project scope.
            source:      Origin label (conversation, tool-run, compaction, …).
            importance:  Curator label (default UNSCORED, scored later).

        Returns:
            List of chunk IDs stored, or None if blocked.
        """
        safe, reason = self.security.is_safe(text)
        if not safe:
            logger.warning("[SECURITY] Blocked memory recording: %s — %s", text[:80], reason)
            self.security.log_blocked(text, reason)
            return None

        target_file = self._resolve_target_file(chunk_type, project_id)
        self._append_markdown(target_file, text, chunk_type, project_id)

        chunks = auto_chunk(text, self._chunk_max, self._chunk_overlap)
        embeddings = self.embedder.embed_batch([c.text for c in chunks])

        now = int(time.time())
        rel_path = str(target_file.relative_to(self._memory_root))
        chunk_ids: list[str] = []

        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = str(uuid.uuid4())
            text_hash = hashlib.sha256(chunk.text.encode()).hexdigest()

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
                "source": source,
                "importance_label": importance.value,
            }

            self.redis.store_chunk(
                chunk_id,
                chunk_data,
                ttl_days=self._default_ttl,
                importance=importance,
            )
            chunk_ids.append(chunk_id)

        logger.info("[RECORD] Stored %d chunks → %s", len(chunk_ids), rel_path)
        return chunk_ids

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_target_file(
        self, chunk_type: str, project_id: Optional[str]
    ) -> Path:
        """Determine which Markdown file to append to."""
        if chunk_type in (
            ChunkType.PREFERENCE.value,
            ChunkType.IDENTITY.value,
            ChunkType.RULE.value,
        ):
            return self._memory_root / "MEMORY.md"

        if project_id:
            target = self._memory_root / "projects" / f"{project_id}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            return target

        date_str = datetime.now().strftime("%Y-%m-%d")
        daily = self._memory_root / f"{date_str}.md"
        return daily

    def _append_markdown(
        self,
        target_file: Path,
        text: str,
        chunk_type: str,
        project_id: Optional[str],
    ) -> None:
        """Append timestamped entry to the Markdown source of truth."""
        target_file.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"\n## [{timestamp}] {chunk_type}"
        if project_id:
            header += f" (project: {project_id})"
        entry = f"{header}\n\n{text}\n"
        with open(target_file, "a", encoding="utf-8") as fh:
            fh.write(entry)
