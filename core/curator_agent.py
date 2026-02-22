"""Curator agent — LLM assigns importance_label, emits events to curator_bridge."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from memory.memory_schema import ImportanceLabel

logger = logging.getLogger(__name__)

_CURATOR_PROMPT = """You are a memory curation agent for the Remnant AI framework.

Evaluate the following memory chunk and assign an importance label:
- GLOBAL_HIGH: Cross-project durable facts, identity, core preferences (no expiry)
- PROJECT_HIGH: Important project-specific facts (1 year TTL)
- EPHEMERAL: Low-value, transient, or redundant (7 day TTL)

Chunk content:
\"\"\"
{text}
\"\"\"

Metadata: type={chunk_type}, project={project_id}, source={source}

Respond with ONLY one of: GLOBAL_HIGH, PROJECT_HIGH, EPHEMERAL
Then on the next line, provide a one-sentence reason."""


class CuratorAgent:
    """
    LLM-backed memory importance scorer.
    Runs asynchronously in the background, emitting events to CuratorBridge.
    """

    def __init__(
        self,
        llm_client,
        curator_bridge,         # memory.curator_bridge.CuratorBridge
        config: dict,
        semaphore_limit: int = 3,
    ) -> None:
        self.llm = llm_client
        self.bridge = curator_bridge
        self.config = config
        self._sem = asyncio.Semaphore(semaphore_limit)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def score_async(self, chunks: list[dict]) -> None:
        """Enqueue chunks for async importance scoring."""
        for chunk in chunks:
            await self._queue.put(chunk)

    async def score_batch(self, chunks: list[dict]) -> list[dict]:
        """
        Score a batch of chunks immediately (not via queue).
        Returns list of { chunk_id, label, reason }.
        """
        tasks = [self._score_one(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    async def start(self) -> None:
        """Start background worker loop."""
        self._running = True
        asyncio.create_task(self._worker(), name="curator-worker")
        logger.info("[CURATOR] Background worker started")

    async def stop(self) -> None:
        """Stop background worker."""
        self._running = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                result = await self._score_one(chunk)
                if result:
                    label = ImportanceLabel(result["label"])
                    self.bridge.on_importance_event(
                        chunk["id"], label, result.get("reason")
                    )
            except Exception as exc:
                logger.error("[CURATOR] Score failed for chunk %s: %s", chunk.get("id"), exc)
            finally:
                self._queue.task_done()

    async def _score_one(self, chunk: dict) -> Optional[dict]:
        """Score a single chunk via LLM."""
        async with self._sem:
            chunk_id = chunk.get("id", "unknown")
            text = chunk.get("text_excerpt", "")
            if not text.strip():
                return None

            prompt = _CURATOR_PROMPT.format(
                text=text[:2000],
                chunk_type=chunk.get("chunk_type", ""),
                project_id=chunk.get("project_id", ""),
                source=chunk.get("source", ""),
            )

            try:
                # Use async streaming — emit synchronous via event loop
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.llm.chat(
                        messages=[{"role": "user", "content": prompt}],
                        use_case="curator",
                        max_tokens=80,
                        temperature=0.1,
                    ),
                )
                label_str, reason = self._parse_response(response.get("content", ""))
                return {"chunk_id": chunk_id, "label": label_str, "reason": reason}

            except Exception as exc:
                logger.warning("[CURATOR] LLM error for chunk %s: %s", chunk_id, exc)
                return None

    @staticmethod
    def _parse_response(content: str) -> tuple[str, str]:
        """Parse 'LABEL\nreason' response."""
        lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
        valid_labels = {l.value for l in ImportanceLabel}

        for i, line in enumerate(lines):
            upper = line.upper()
            for label in valid_labels:
                if label in upper:
                    reason = " ".join(lines[i + 1:]) or ""
                    return label, reason

        return ImportanceLabel.UNSCORED.value, ""
