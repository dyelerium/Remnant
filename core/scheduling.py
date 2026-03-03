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
    - Config snapshot (02:30 UTC)
    - One-shot proactive tasks (scheduled by the agent via ScheduleTool)
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
        self._orchestrator = None
        self._broadcast = None
        from pathlib import Path
        self._config_dir = Path("/app/config") if Path("/app/config").exists() else Path("config")

    def set_dispatch(self, orchestrator, broadcast_fn) -> None:
        """Wire orchestrator and broadcast function after construction (avoids circular dep)."""
        self._orchestrator = orchestrator
        self._broadcast = broadcast_fn

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

        # Daily config snapshot at 02:30 UTC
        self._scheduler.add_job(
            self._run_config_snapshot,
            "cron",
            hour=2,
            minute=30,
            id="config_snapshot",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("[SCHEDULER] Started — compaction@03:00 UTC, curator every 30m, snapshot@02:30 UTC")

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("[SCHEDULER] Stopped")

    # ------------------------------------------------------------------
    # Proactive one-shot scheduling (agent-triggered)
    # ------------------------------------------------------------------

    def schedule_once(
        self,
        delay_seconds: float,
        task: str,
        session_id: str,
        channel: str = "websocket",
    ) -> str:
        """Schedule a one-shot proactive task. Returns the job ID."""
        import uuid
        from datetime import datetime, timedelta

        job_id = f"proactive_{uuid.uuid4().hex[:8]}"

        if self._scheduler and self._scheduler.running:
            run_at = datetime.now() + timedelta(seconds=delay_seconds)
            self._scheduler.add_job(
                self._proactive_job,
                "date",
                run_date=run_at,
                args=[task, session_id, channel],
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                "[SCHEDULER] Proactive job %s in %.0fs at %s: %s",
                job_id, delay_seconds, run_at.strftime("%H:%M:%S"), task[:60],
            )
        else:
            # APScheduler not running — fall back to an asyncio task
            asyncio.create_task(self._delayed_job(delay_seconds, task, session_id, channel))
            logger.info(
                "[SCHEDULER] Proactive asyncio task in %.0fs (APScheduler unavailable): %s",
                delay_seconds, task[:60],
            )

        return job_id

    async def _proactive_job(self, task: str, session_id: str, channel: str) -> None:
        """Execute a scheduled task through the orchestrator and broadcast the result."""
        if not self._orchestrator or not self._broadcast:
            logger.error("[SCHEDULER] Proactive job fired but orchestrator/broadcast not set — call set_dispatch()")
            return

        logger.info("[SCHEDULER] Firing proactive job for session=%s: %s", session_id[:8], task[:60])
        full_response = ""
        try:
            async for chunk in self._orchestrator.handle(
                message=task,
                session_id=session_id,
                channel=channel,
                memory_context="",
            ):
                full_response += chunk
        except Exception as exc:
            logger.error("[SCHEDULER] Proactive job failed: %s", exc)
            full_response = f"[Scheduled task error: {exc}]"

        await self._broadcast({
            "type": "proactive",
            "content": full_response,
            "session_id": session_id,
            "task": task,
        })

    async def _delayed_job(self, delay: float, task: str, session_id: str, channel: str) -> None:
        """Asyncio fallback when APScheduler is unavailable."""
        await asyncio.sleep(delay)
        await self._proactive_job(task, session_id, channel)

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

    async def _run_config_snapshot(self) -> None:
        """Take a tarball snapshot of all config YAML files."""
        import tarfile
        import time as _time
        from pathlib import Path
        try:
            snap_dir = self._config_dir.parent / "snapshots"
            snap_dir.mkdir(exist_ok=True)
            ts = int(_time.time())
            snap_path = snap_dir / f"config-{ts}.tar.gz"
            with tarfile.open(snap_path, "w:gz") as tar:
                for f in self._config_dir.glob("*.yaml"):
                    tar.add(f, arcname=f.name)
            # Prune to last 20 snapshots
            existing = sorted(snap_dir.glob("config-*.tar.gz"))
            for old in existing[:-20]:
                old.unlink()
            logger.info("[SCHEDULER] Config snapshot saved: %s", snap_path.name)
        except Exception as exc:
            logger.error("[SCHEDULER] Config snapshot failed: %s", exc)

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
