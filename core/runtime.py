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

_SESSION_MAX_MESSAGES = 20   # keep last 20 messages (10 user+assistant pairs)
_SESSION_TTL = 86400          # 24 hours

_COMPLEX_KW = ("analyze", "compare", "reason", "plan", "architecture", "design", "evaluate")
_CODER_KW   = ("code", "script", "implement", "debug", "refactor", "fix bug", "function", "class")
_SEARCH_KW  = ("search", "find", "research", "summarize", "look up", "browse", "fetch", "latest")


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
        redis_client=None,
        audit_logger=None,
        skill_registry=None,
    ) -> None:
        self.retriever = memory_retriever
        self.recorder = memory_recorder
        self.llm = llm_client
        self.security = security_manager
        self.curator = curator_agent
        self.config = config
        self._tool_registry: dict = tool_registry or {}
        self._skill_registry = skill_registry  # for calling installed skills by name
        self._agents_cfg: dict = config.get("agents", {})
        self._max_tool_rounds = 5
        self._redis = redis_client
        self._audit = audit_logger

    def set_skill_registry(self, skill_registry) -> None:
        """Wire the skill registry post-construction (avoids circular dep)."""
        self._skill_registry = skill_registry

    # ------------------------------------------------------------------
    # Budget-mode use-case selector
    # ------------------------------------------------------------------

    @staticmethod
    def _smart_use_case(message: str) -> str:
        """Pick the cheapest capable use-case bucket based on prompt complexity."""
        m = message.lower()
        tok = len(message.split())
        if tok > 300 or any(k in m for k in _COMPLEX_KW):
            return "planning"   # most capable
        if any(k in m for k in _CODER_KW + _SEARCH_KW):
            return "chat"       # balanced
        return "fast"           # cheapest (qwen3:4b, free-tier models)

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
        cancel_event: Optional[asyncio.Event] = None,
        images: Optional[list] = None,
        budget_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Execute one agent loop, yielding response chunks."""
        set_logging_context(
            lane_id=agent_node.lane_id or "",
            agent_id=agent_node.agent_id,
            project_id=project_id or "",
            request_id=session_id or "",
        )
        agent_node.status = NodeStatus.RUNNING

        # -- Audit: log incoming chat request --
        if self._audit:
            self._audit.log_chat(
                channel=channel,
                session_id=session_id or "",
                user_message=message,
                project_id=project_id,
            )

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

        # Inject current model identity
        try:
            _spec = self.llm.registry.resolve("chat")
            system_prompt += f"\n[model:{_spec.provider}/{_spec.model}]"
        except Exception:
            pass

        # Inject available tool schemas so the LLM knows what to call and how
        if self._tool_registry:
            system_prompt = system_prompt + "\n\n" + self._build_tool_docs()

        if memory_context:
            system_prompt = f"{system_prompt}\n\n{memory_context}"

        # Redact any secrets from the incoming message
        safe_message = self.security.redact_prompt(message)

        # Inject recent session history so the agent remembers earlier turns
        history = await self._load_session_history(session_id) if session_id else []

        # Build user message — support vision content list when images are attached
        if images:
            user_content: list = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.get("mime", "image/jpeg"),
                        "data": img.get("data", ""),
                    },
                }
                for img in images
            ]
            user_content.append({"type": "text", "text": safe_message})
        else:
            user_content = safe_message  # type: ignore[assignment]

        messages = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": user_content}]
        )

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
            # Check cancellation before each round
            if cancel_event and cancel_event.is_set():
                logger.info("[RUNTIME] Cancelled before round %d", _round)
                agent_node.status = NodeStatus.DONE
                return

            # Buffer this LLM call
            buffered_parts: list[str] = []
            use_case = self._smart_use_case(message) if budget_mode else "chat"
            try:
                async for chunk in self.llm.chat_stream(
                    messages=messages,
                    use_case=use_case,
                    project_id=project_id,
                    cancel_event=cancel_event,
                ):
                    if cancel_event and cancel_event.is_set():
                        break
                    buffered_parts.append(chunk)
            except Exception as exc:
                logger.error("[RUNTIME] LLM stream error: %s", exc)
                yield f"[Error: {exc}]"
                agent_node.status = NodeStatus.FAILED
                return

            buffered = "".join(buffered_parts)
            # Strip <think>...</think> reasoning blocks produced by models like qwen3
            # before tool detection and user-facing output so internal reasoning
            # doesn't leak into the response or trigger spurious tool calls.
            buffered_clean = self._strip_think_blocks(buffered)

            if not self._should_execute_tools(buffered_clean) or _round == self._max_tool_rounds:
                # No tool calls (or max rounds reached) — stream this as the final response
                full_response = buffered_clean
                yield "[GEN] "
                yield buffered_clean
                yield "\n"
                break

            # Execute tool calls found in this response (using think-stripped text)
            tool_results = await self._execute_tools(buffered_clean, agent_node, project_id)
            all_tool_results.extend(tool_results)

            if tool_results:
                yield f"[EXE] {len(tool_results)} tool(s) executed\n"
                logger.info("[RUNTIME] Round %d: executed %d tool(s)", _round + 1, len(tool_results))

            # Build the clean assistant turn (strip tool blocks from display)
            clean_assistant = self._strip_tool_blocks(buffered_clean).strip()
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
                "content": f"Results:\n{tool_result_text}",
            })

            full_response = buffered  # keep last for recording

        # -- Save to session history so subsequent turns have context --
        if session_id and full_response:
            history.append({"role": "user", "content": safe_message})
            history.append({"role": "assistant", "content": full_response})
            await self._save_session_history(session_id, history)

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

    async def _load_session_history(self, session_id: str) -> list:
        """Load session conversation history from Redis (TTL-backed, survives restarts)."""
        if not self._redis:
            return []
        key = f"remnant:session:history:{session_id}"
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._redis.r.get, key)
            if data:
                return json.loads(data)
        except Exception as exc:
            logger.warning("[RUNTIME] Failed to load session history: %s", exc)
        return []

    async def _save_session_history(self, session_id: str, history: list) -> None:
        """Persist session history to Redis with 24h TTL, capped at last 20 messages."""
        if not self._redis:
            return
        key = f"remnant:session:history:{session_id}"
        trimmed = history[-_SESSION_MAX_MESSAGES:]
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._redis.r.set(key, json.dumps(trimmed), ex=_SESSION_TTL),
            )
        except Exception as exc:
            logger.warning("[RUNTIME] Failed to save session history: %s", exc)

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
        """Build a compact tool reference injected into the system prompt."""
        lines = [
            "## Tools — one per block:",
            "```tool",
            '{"name":"tool_name","args":{"param":"value"}}',
            "```",
        ]
        for name, tool in self._tool_registry.items():
            hint = getattr(tool, "schema_hint", {})
            desc = hint.get("description", getattr(tool, "description", ""))
            props = hint.get("parameters", {}).get("properties", {})
            required = hint.get("parameters", {}).get("required", [])
            params = ", ".join(
                f"{k}{'*' if k in required else ''}"
                for k in props
            )
            suffix = f"({params})" if params else ""
            lines.append(f"- {name}{suffix}: {desc}")
        # Also list installed skills (callable the same way)
        if self._skill_registry:
            for skill in self._skill_registry.list():
                sname = skill["name"]
                sdesc = skill.get("description", "")
                lines.append(f"- {sname}: {sdesc} [skill]")
        return "\n".join(lines)

    @staticmethod
    def _strip_think_blocks(text: str) -> str:
        """Remove <think>...</think> reasoning blocks emitted by models like qwen3."""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

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
                # Fall back to skill registry if available
                if self._skill_registry and self._skill_registry.get(tool_name):
                    try:
                        result_data = await self._skill_registry.invoke(
                            tool_name, tool_args, self._tool_registry,
                            agent_node=agent_node,
                        )
                        results.append({"tool": tool_name, "result": result_data})
                        logger.info("[RUNTIME] Skill %r OK: %s", tool_name, str(result_data)[:120])
                        if self._audit:
                            self._audit.log_tool(
                                tool_name=tool_name,
                                session_id=agent_node.agent_id,
                                args_preview=str(tool_args)[:200],
                                result_ok=True,
                            )
                    except Exception as exc:
                        logger.error("[RUNTIME] Skill %r failed: %s", tool_name, exc)
                        results.append({"tool": tool_name, "error": str(exc)})
                    continue
                results.append({"tool": tool_name, "error": f"tool '{tool_name}' not found in registry"})
                continue

            try:
                result = await tool.run(tool_args, agent_node=agent_node)
                # Convert ToolResult to plain dict for JSON serialisation
                result_data = result.to_dict() if hasattr(result, "to_dict") else result
                results.append({"tool": tool_name, "result": result_data})
                logger.info("[RUNTIME] Tool %r OK: %s", tool_name, str(result_data)[:120])
                if self._audit:
                    self._audit.log_tool(
                        tool_name=tool_name,
                        session_id=agent_node.agent_id,
                        args_preview=str(tool_args)[:200],
                        result_ok=True,
                    )
            except Exception as exc:
                logger.error("[RUNTIME] Tool %r failed: %s", tool_name, exc)
                results.append({"tool": tool_name, "error": str(exc)})
                if self._audit:
                    self._audit.log_tool(
                        tool_name=tool_name,
                        session_id=agent_node.agent_id,
                        args_preview=str(tool_args)[:200],
                        result_ok=False,
                        error=str(exc),
                    )

        return results
