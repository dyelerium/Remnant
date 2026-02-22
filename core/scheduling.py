"""APScheduler background jobs — nightly compaction, Curator background scan."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Background job scheduler for Remnant.

    Jobs:
    - Nightly memory compaction (03:00 UTC)
    - Curator background scan (every 30 minutes)
    - Budget counter cleanup (daily)
    """

    def __init__(
        self,
        memory_compactor,
        curator_agent,
        redis_client,
        config: dict,
    ) -> None:
        self.compactor = memory_compactor
        self.curator = curator_agent
        self.redis = redis_client
        self.config = config
        self._scheduler = None

    def start(self) -> None:
        """Start APScheduler with all jobs."""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError:
            logger.warning("[SCHEDULER] APScheduler not available — background jobs disabled")
            return

        self._scheduler = AsyncIOScheduler(timezone="UTC")

        # Nightly compaction at 03:00 UTC
        self._scheduler.add_job(
            self._run_compaction,
            "cron",
            hour=3,
            minute=0,
            id="nightly_compaction",
            replace_existing=True,
        )

        # Curator scan every 30 minutes
        self._scheduler.add_job(
            self._run_curator_scan,
            "interval",
            minutes=30,
            id="curator_scan",
            replace_existing=True,
        )

        # Budget cleanup every 24 hours
        self._scheduler.add_job(
            self._cleanup_old_counters,
            "interval",
            hours=24,
            id="budget_cleanup",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("[SCHEDULER] Started — compaction@03:00 UTC, curator every 30m")

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("[SCHEDULER] Stopped")

    # ------------------------------------------------------------------
    # Job implementations
    # ------------------------------------------------------------------

    async def _run_compaction(self) -> None:
        logger.info("[SCHEDULER] Starting nightly compaction")
        try:
            n = await asyncio.get_event_loop().run_in_executor(
                None, self.compactor.compact
            )
            logger.info("[SCHEDULER] Compaction complete — %d chunks processed", n)
        except Exception as exc:
            logger.error("[SCHEDULER] Compaction failed: %s", exc)

    async def _run_curator_scan(self) -> None:
        """Score a batch of recently-added UNSCORED chunks."""
        from memory.memory_schema import ImportanceLabel, SCORE_ZSET_KEY

        logger.debug("[SCHEDULER] Curator scan starting")
        try:
            # Fetch up to 20 recently-stored unscored chunks
            chunk_ids = self.redis.r.zrange(SCORE_ZSET_KEY, 0, 19)
            chunks = []
            for cid_bytes in chunk_ids:
                cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
                chunk = self.redis.get_chunk(cid)
                if chunk and chunk.get("importance_label") == ImportanceLabel.UNSCORED.value:
                    chunk["id"] = cid
                    chunks.append(chunk)

            if chunks:
                await self.curator.score_async(chunks)
                logger.info("[SCHEDULER] Curator enqueued %d chunks for scoring", len(chunks))
        except Exception as exc:
            logger.error("[SCHEDULER] Curator scan failed: %s", exc)

    async def _cleanup_old_counters(self) -> None:
        """Remove stale budget counters from Redis."""
        try:
            prefix = self.config.get("redis_prefix", "budget")
            pattern = f"{prefix}:*"
            stale = []
            for key in self.redis.r.scan_iter(pattern, count=500):
                ttl = self.redis.r.ttl(key)
                if ttl == -1:  # No expiry set — shouldn't happen but clean up
                    stale.append(key)
            if stale:
                self.redis.r.delete(*stale)
                logger.info("[SCHEDULER] Cleaned %d stale budget counters", len(stale))
        except Exception as exc:
            logger.error("[SCHEDULER] Budget cleanup failed: %s", exc)
