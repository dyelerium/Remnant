"""
Agent runtime — RECALL → PLAN → LLM → TOOLS → RECORD → CURATE loop per lane.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from .agent_graph import AgentNode, NodeStatus
from .logging_config import set_logging_context

logger = logging.getLogger(__name__)


class AgentRuntime:
    """
    Core per-lane agent execution loop.

    Steps per message:
      1. RECALL    — retrieve relevant memory chunks
      2. PLAN      — planner.decompose() (for simple requests, identity only)
      3. LLM       — chat with memory context (streaming)
      4. TOOLS     — parse + execute tool calls with security check
      5. RECORD    — store result chunks in memory
      6. CURATE    — async background importance scoring
    """

    def __init__(
        self,
        memory_retriever,
        memory_recorder,
        llm_client,
        security_manager,
        curator_agent,
        config: dict,
        tool_registry: Optional[dict] = None,
    ) -> None:
        self.retriever = memory_retriever
        self.recorder = memory_recorder
        self.llm = llm_client
        self.security = security_manager
        self.curator = curator_agent
        self.config = config
        self._tool_registry: dict = tool_registry or {}
        self._agents_cfg: dict = config.get("agents", {})
        self._max_tool_rounds = 5

    # ------------------------------------------------------------------
    # Streaming run
    # ------------------------------------------------------------------

    async def run_stream(
        self,
        message: str,
        agent_node: AgentNode,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "websocket",
    ) -> AsyncIterator[str]:
        """Execute one agent loop, yielding response chunks."""
        set_logging_context(
            lane_id=agent_node.lane_id or "",
            agent_id=agent_node.agent_id,
            project_id=project_id or "",
            request_id=session_id or "",
        )
        agent_node.status = NodeStatus.RUNNING

        # -- 1. RECALL --
        memory_chunks = await self._recall(message, project_id)
        safe_chunks = self.security.sanitise_memory(memory_chunks)
        memory_context = self.retriever.format_for_prompt(safe_chunks)

        # -- 2. Build messages with system prompt + memory context --
        agent_cfg = self._agents_cfg.get(
            agent_node.agent_type,
            self._agents_cfg.get("default", {}),
        )
        system_prompt = agent_cfg.get("system_prompt", "You are a helpful AI assistant.")

        if memory_context:
            system_prompt = f"{system_prompt}\n\n{memory_context}"

        # Redact any secrets from the incoming message
        safe_message = self.security.redact_prompt(message)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": safe_message},
        ]

        # -- 3. LLM (streaming) --
        full_response = ""
        yield "[GEN] "
        try:
            async for chunk in self.llm.chat_stream(
                messages=messages,
                use_case="chat",
                project_id=project_id,
            ):
                full_response += chunk
                yield chunk
        except Exception as exc:
            logger.error("[RUNTIME] LLM stream error: %s", exc)
            yield f"\n[Error: {exc}]"
            agent_node.status = NodeStatus.FAILED
            return

        yield "\n"

        # -- 4. TOOLS (if LLM emitted tool calls) --
        tool_results = []
        if self._should_execute_tools(full_response):
            tool_results = await self._execute_tools(
                full_response, agent_node, project_id
            )
            if tool_results:
                yield f"[EXE] {len(tool_results)} tool(s) executed\n"

        # -- 5. RECORD --
        record_text = full_response
        if tool_results:
            record_text += "\n\nTool results:\n" + "\n".join(
                str(r) for r in tool_results
            )

        new_chunk_ids = await self._record(record_text, project_id, source="agent")

        # -- 6. CURATE (background) --
        if new_chunk_ids and self.curator:
            new_chunks = [{"id": cid, "text_excerpt": record_text[:500]} for cid in new_chunk_ids]
            asyncio.create_task(self.curator.score_async(new_chunks))

        agent_node.status = NodeStatus.DONE

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _recall(
        self, message: str, project_id: Optional[str]
    ) -> list[dict]:
        """Retrieve relevant memory chunks."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self.retriever.retrieve(message, project_id=project_id),
            )
        except Exception as exc:
            logger.warning("[RUNTIME] Memory recall failed: %s", exc)
            return []

    async def _record(
        self,
        text: str,
        project_id: Optional[str],
        source: str = "agent",
    ) -> list[str]:
        """Record result to memory."""
        try:
            loop = asyncio.get_event_loop()
            chunk_ids = await loop.run_in_executor(
                None,
                lambda: self.recorder.record(
                    text,
                    chunk_type="log",
                    project_id=project_id,
                    source=source,
                ),
            )
            return chunk_ids or []
        except Exception as exc:
            logger.warning("[RUNTIME] Memory record failed: %s", exc)
            return []

    def _should_execute_tools(self, response: str) -> bool:
        """Heuristic: check if LLM response contains tool call markers."""
        return "```tool" in response.lower() or "<tool>" in response.lower()

    async def _execute_tools(
        self,
        response: str,
        agent_node: AgentNode,
        project_id: Optional[str],
    ) -> list[dict]:
        """Parse and execute tool calls from LLM response."""
        import json, re
        results = []

        # Extract JSON tool blocks: ```tool\n{...}\n```
        pattern = re.compile(r"```tool\s*\n(.*?)\n```", re.DOTALL)
        for match in pattern.finditer(response):
            raw = match.group(1).strip()
            try:
                call = json.loads(raw)
            except json.JSONDecodeError:
                continue

            tool_name = call.get("name", "")
            tool_args = call.get("args", {})

            # Security policy check
            if not self.security.check_tool_policy(tool_name, project_id):
                logger.warning(
                    "[RUNTIME] Tool %r denied by policy (project=%s)", tool_name, project_id
                )
                results.append({"tool": tool_name, "error": "denied by policy"})
                continue

            tool = self._tool_registry.get(tool_name)
            if not tool:
                results.append({"tool": tool_name, "error": "tool not found"})
                continue

            try:
                result = await tool.run(tool_args, agent_node=agent_node)
                results.append({"tool": tool_name, "result": result})
            except Exception as exc:
                logger.error("[RUNTIME] Tool %r failed: %s", tool_name, exc)
                results.append({"tool": tool_name, "error": str(exc)})

        return results
