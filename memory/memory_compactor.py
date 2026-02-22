"""Memory compactor — nightly: group low-score chunks → LLM summarise → promote durable facts."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .memory_schema import ImportanceLabel

logger = logging.getLogger(__name__)

# Chunks with useful_score below this threshold are compaction candidates
_LOW_SCORE_THRESHOLD = 1.0
# How many low-score chunks to process per compaction run
_BATCH_SIZE = 50


class MemoryCompactor:
    """Summarise low-value memory chunks using an LLM and promote durable facts."""

    def __init__(
        self,
        redis_client,
        embedding_provider,
        recorder,               # MemoryRecorder
        llm_client,             # core.llm_client.LLMClient (may be None in tests)
        config: dict,
    ) -> None:
        self.redis = redis_client
        self.embedder = embedding_provider
        self.recorder = recorder
        self.llm = llm_client
        self.config = config

        compact_cfg = config.get("compaction", {})
        self._threshold_tokens: int = compact_cfg.get("trigger_token_threshold", 4000)
        self._summary_target: int = compact_cfg.get("summary_target_tokens", 300)
        self._llm_provider: str = compact_cfg.get("llm_provider", "claude")
        self._llm_model: str = compact_cfg.get("llm_model", "claude-haiku-4-5-20251001")
        self._memory_root = Path(config.get("memory_root", "./memory"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compact_if_needed(self, current_token_usage: int) -> bool:
        """Trigger compaction if context usage exceeds threshold."""
        if current_token_usage < self._threshold_tokens:
            return False
        logger.info(
            "[COMPACT] Token usage %d exceeds threshold %d — compacting",
            current_token_usage,
            self._threshold_tokens,
        )
        self.compact()
        return True

    def compact(self, project_id: Optional[str] = None) -> int:
        """
        Full compaction run:
          1. Identify low-score chunks.
          2. Group by file.
          3. LLM-summarise each group.
          4. Write summary to daily log.
          5. Promote durable facts to MEMORY.md.
          6. Optionally delete original low-score chunks.

        Returns: Number of chunks compacted.
        """
        low_chunks = self._get_low_score_chunks(project_id)
        if not low_chunks:
            logger.info("[COMPACT] No low-score chunks to compact")
            return 0

        # Group by file_path
        by_file: dict[str, list[dict]] = {}
        for chunk in low_chunks:
            fp = chunk.get("file_path", "unknown")
            by_file.setdefault(fp, []).append(chunk)

        total_compacted = 0

        for file_path, file_chunks in by_file.items():
            try:
                compacted = self._compact_group(file_path, file_chunks, project_id)
                total_compacted += compacted
            except Exception as exc:
                logger.error("[COMPACT] Failed for %s: %s", file_path, exc)

        logger.info("[COMPACT] Compacted %d chunks", total_compacted)
        return total_compacted

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_low_score_chunks(
        self, project_id: Optional[str]
    ) -> list[dict]:
        """Fetch chunks with useful_score below threshold from Redis sorted set."""
        from .memory_schema import SCORE_ZSET_KEY

        # ZRANGEBYSCORE: lowest scores first
        chunk_ids = self.redis.r.zrangebyscore(
            SCORE_ZSET_KEY, "-inf", _LOW_SCORE_THRESHOLD, start=0, num=_BATCH_SIZE
        )

        chunks = []
        for cid_bytes in chunk_ids:
            cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
            chunk = self.redis.get_chunk(cid)
            if chunk is None:
                continue
            if project_id and chunk.get("project_id") != project_id:
                continue
            # Skip EPHEMERAL (already short TTL, just let them expire)
            if chunk.get("importance_label") == ImportanceLabel.EPHEMERAL.value:
                continue
            chunk["id"] = cid
            chunks.append(chunk)

        return chunks

    def _compact_group(
        self,
        file_path: str,
        chunks: list[dict],
        project_id: Optional[str],
    ) -> int:
        """Summarise a group of chunks from the same file."""
        combined = "\n\n".join(c.get("text_excerpt", "") for c in chunks)
        if not combined.strip():
            return 0

        if self.llm:
            summary, facts = self._llm_summarise(combined)
        else:
            # Fallback: truncate to summary_target tokens (no LLM)
            words = combined.split()
            target_words = int(self._summary_target * 0.75)
            summary = " ".join(words[:target_words])
            facts = []

        # Write summary to today's daily log
        date_str = datetime.now().strftime("%Y-%m-%d")
        daily_file = self._memory_root / f"{date_str}.md"

        with open(daily_file, "a", encoding="utf-8") as fh:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            fh.write(f"\n## [{ts}] compaction-summary (from: {file_path})\n\n{summary}\n")

        # Promote durable facts to MEMORY.md
        for fact in facts:
            self.recorder.record(
                fact,
                chunk_type="learning",
                project_id=project_id,
                source="compaction",
                importance=ImportanceLabel.PROJECT_HIGH,
            )

        # Delete the original low-score chunks from Redis
        for chunk in chunks:
            self.redis.delete_chunk(chunk["id"])

        return len(chunks)

    def _llm_summarise(self, text: str) -> tuple[str, list[str]]:
        """Use LLM to produce a summary and extract durable facts."""
        prompt = (
            f"Summarise the following memory chunks into ≤{self._summary_target} tokens. "
            "Then list any durable facts worth keeping long-term as bullet points under "
            "the heading '### Durable Facts'. Be concise.\n\n"
            f"---\n{text}\n---"
        )
        try:
            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self._llm_model,
                provider=self._llm_provider,
                max_tokens=self._summary_target + 200,
            )
        except Exception as exc:
            logger.warning("[COMPACT] LLM call failed: %s", exc)
            words = text.split()
            return " ".join(words[: int(self._summary_target * 0.75)]), []

        raw = response.get("content", "")

        # Parse durable facts
        facts: list[str] = []
        if "### Durable Facts" in raw:
            parts = raw.split("### Durable Facts", 1)
            summary = parts[0].strip()
            for line in parts[1].splitlines():
                line = line.lstrip("•- ").strip()
                if line:
                    facts.append(line)
        else:
            summary = raw.strip()

        return summary, facts
