"""
Agent runtime — RECALL → PLAN → LLM → TOOLS → RECORD → CURATE loop per lane.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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

        # -- 2. Build messages with system prompt + tool docs + memory context --
        agent_cfg = self._agents_cfg.get(
            agent_node.agent_type,
            self._agents_cfg.get("default", {}),
        )
        system_prompt = agent_cfg.get("system_prompt", "You are a helpful AI assistant.")

        # Inject current model identity so the agent knows what LLM it is using
        try:
            _spec = self.llm.registry.resolve("chat")
            system_prompt += (
                f"\n[Current model: {_spec.provider}/{_spec.model}"
                f" | context: {_spec.context_window} tokens]"
            )
        except Exception:
            pass

        # Inject available tool schemas so the LLM knows what to call and how
        if self._tool_registry:
            system_prompt = system_prompt + "\n\n" + self._build_tool_docs()

        if memory_context:
            system_prompt = f"{system_prompt}\n\n{memory_context}"

        # Redact any secrets from the incoming message
        safe_message = self.security.redact_prompt(message)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": safe_message},
        ]

        # -- 3. LLM → TOOLS agentic loop --
        #
        # Strategy: buffer each LLM response rather than streaming it.
        # If the response contains tool calls, execute them, inject results,
        # and call the LLM again.  Only the FINAL response (no tool calls) is
        # streamed to the user — this prevents the LLM from writing made-up
        # tool results mid-stream.
        full_response = ""
        all_tool_results: list[dict] = []

        for _round in range(self._max_tool_rounds + 1):
            # Buffer this LLM call
            buffered_parts: list[str] = []
            try:
                async for chunk in self.llm.chat_stream(
                    messages=messages,
                    use_case="chat",
                    project_id=project_id,
                ):
                    buffered_parts.append(chunk)
            except Exception as exc:
                logger.error("[RUNTIME] LLM stream error: %s", exc)
                yield f"[Error: {exc}]"
                agent_node.status = NodeStatus.FAILED
                return

            buffered = "".join(buffered_parts)

            if not self._should_execute_tools(buffered) or _round == self._max_tool_rounds:
                # No tool calls (or max rounds reached) — stream this as the final response
                full_response = buffered
                yield "[GEN] "
                for chunk in buffered_parts:
                    yield chunk
                yield "\n"
                break

            # Execute tool calls found in this response
            tool_results = await self._execute_tools(buffered, agent_node, project_id)
            all_tool_results.extend(tool_results)

            if tool_results:
                yield f"[EXE] {len(tool_results)} tool(s) executed\n"
                logger.info("[RUNTIME] Round %d: executed %d tool(s)", _round + 1, len(tool_results))

            # Build the clean assistant turn (strip raw tool blocks from display)
            clean_assistant = self._strip_tool_blocks(buffered).strip()
            if clean_assistant:
                messages.append({"role": "assistant", "content": clean_assistant})

            # Inject tool results as the next user turn
            def _safe_json(v):
                try:
                    return json.dumps(v)
                except (TypeError, ValueError):
                    return str(v)

            tool_result_text = "\n".join(
                f"[{r['tool']}]: {_safe_json(r.get('result', r.get('error')))}"
                for r in tool_results
            )
            messages.append({
                "role": "user",
                "content": (
                    f"Tool results:\n{tool_result_text}\n\n"
                    "Use these real results to answer the user. Do not guess or fabricate data."
                ),
            })

            full_response = buffered  # keep last for recording

        # -- 5. RECORD --
        record_text = full_response
        if all_tool_results:
            record_text += "\n\nTool results:\n" + "\n".join(
                str(r) for r in all_tool_results
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

    def _build_tool_docs(self) -> str:
        """Build a tool reference block injected into the system prompt."""
        import json
        lines = [
            "## Tools",
            "When a task requires using a tool, output a fenced block with this exact format:",
            "```tool",
            '{"name": "tool_name", "args": {"param": "value"}}',
            "```",
            "Execute ONE tool per block. The result will be appended and you can reason over it.",
            "",
            "Available tools:",
        ]
        for name, tool in self._tool_registry.items():
            hint = getattr(tool, "schema_hint", {})
            desc = hint.get("description", getattr(tool, "description", ""))
            props = hint.get("parameters", {}).get("properties", {})
            required = hint.get("parameters", {}).get("required", [])
            params = ", ".join(
                f"{k}{'*' if k in required else ''}: {v.get('type', '?')}"
                for k, v in props.items()
            )
            lines.append(f"- **{name}**: {desc}")
            if params:
                lines.append(f"  args: {{{params}}}  (* = required)")
        return "\n".join(lines)

    def _should_execute_tools(self, response: str) -> bool:
        """Check if LLM response contains tool call markers (any supported format)."""
        lower = response.lower()
        return (
            "```tool" in lower          # JSON block format
            or "<tool_call>" in lower   # XML function-call format
            or "<tool>" in lower        # simple XML tag
        )

    def _strip_tool_blocks(self, response: str) -> str:
        """Remove tool call blocks (all formats) from text."""
        # Strip JSON blocks
        text = re.sub(r"```tool\s*\n.*?\n```", "", response, flags=re.DOTALL)
        # Strip XML <tool_call> blocks
        text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
        # Strip simple <tool> blocks
        text = re.sub(r"<tool>.*?</tool>", "", text, flags=re.DOTALL)
        return text.strip()

    def _parse_tool_calls(self, response: str) -> list[tuple[str, dict]]:
        """Extract (tool_name, args) pairs from response, supporting multiple formats."""
        calls: list[tuple[str, dict]] = []

        # Format 1: ```tool\n{"name": "...", "args": {...}}\n```
        for match in re.finditer(r"```tool\s*\n(.*?)\n```", response, re.DOTALL):
            try:
                obj = json.loads(match.group(1).strip())
                calls.append((obj.get("name", ""), obj.get("args", {})))
            except json.JSONDecodeError:
                pass

        # Format 2: <tool_call><function=name><parameter=k>v</parameter></function></tool_call>
        for tc in re.finditer(r"<tool_call>(.*?)</tool_call>", response, re.DOTALL):
            inner = tc.group(1)
            fn = re.search(r"<function=(\w+)>(.*?)</function>", inner, re.DOTALL)
            if not fn:
                continue
            tool_name = fn.group(1)
            args: dict = {}
            for pm in re.finditer(r"<parameter=(\w+)>(.*?)</parameter>", fn.group(2), re.DOTALL):
                args[pm.group(1)] = pm.group(2).strip()
            calls.append((tool_name, args))

        # Format 3: <tool>{"name": "...", "args": {...}}</tool>
        for match in re.finditer(r"<tool>(.*?)</tool>", response, re.DOTALL):
            try:
                obj = json.loads(match.group(1).strip())
                calls.append((obj.get("name", ""), obj.get("args", {})))
            except json.JSONDecodeError:
                pass

        return calls

    async def _execute_tools(
        self,
        response: str,
        agent_node: AgentNode,
        project_id: Optional[str],
    ) -> list[dict]:
        """Parse (multi-format) and execute tool calls from LLM response."""
        results = []

        for tool_name, tool_args in self._parse_tool_calls(response):
            if not tool_name:
                continue

            if not self.security.check_tool_policy(tool_name, project_id):
                logger.warning("[RUNTIME] Tool %r denied by policy", tool_name)
                results.append({"tool": tool_name, "error": "denied by policy"})
                continue

            tool = self._tool_registry.get(tool_name)
            if not tool:
                results.append({"tool": tool_name, "error": f"tool '{tool_name}' not found in registry"})
                continue

            try:
                result = await tool.run(tool_args, agent_node=agent_node)
                # Convert ToolResult to plain dict for JSON serialisation
                result_data = result.to_dict() if hasattr(result, "to_dict") else result
                results.append({"tool": tool_name, "result": result_data})
                logger.info("[RUNTIME] Tool %r OK: %s", tool_name, str(result_data)[:120])
            except Exception as exc:
                logger.error("[RUNTIME] Tool %r failed: %s", tool_name, exc)
                results.append({"tool": tool_name, "error": str(exc)})

        return results
