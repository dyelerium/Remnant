"""Unified LLM client — chat() + embed() over provider registry, budget-aware."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Provider families that use the OpenAI-compatible API
_OPENAI_COMPAT_PROVIDERS = frozenset({
    "openai", "openrouter", "nvidia", "moonshot",
    "deepseek", "groq", "mistral", "xai", "sambanova", "venice", "lmstudio",
    "mercury",
})


class LLMClient:
    """
    Unified chat + embed interface over multiple LLM providers.
    Budget enforcement happens before each call.
    """

    def __init__(
        self,
        config: dict,
        provider_registry,      # core.provider_registry.ProviderRegistry
        budget_manager,         # core.budget_manager.BudgetManager
    ) -> None:
        self.config = config
        self.registry = provider_registry
        self.budget = budget_manager

    # ------------------------------------------------------------------
    # Synchronous chat
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        use_case: str = "chat",
        model: Optional[str] = None,
        provider: Optional[str] = None,
        project_id: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        **kwargs,
    ) -> dict:
        """
        Send a chat request.

        Returns: { "content": str, "tokens_in": int, "tokens_out": int,
                   "model": str, "provider": str, "cost_usd": float }
        """
        override = f"{provider}/{model}" if provider and model else model
        spec = self.registry.resolve(use_case, project_id, override)

        # Budget pre-check (estimate based on message length)
        estimated_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        self.budget.pre_check(spec, estimated_tokens, project_id)

        start = time.monotonic()
        response = self._dispatch_chat(spec, messages, max_tokens, temperature, **kwargs)
        elapsed = time.monotonic() - start

        tokens_in = response.get("tokens_in", 0)
        tokens_out = response.get("tokens_out", 0)
        cost = (tokens_in / 1000 * spec.cost_per_1k_input) + (
            tokens_out / 1000 * spec.cost_per_1k_output
        )

        # Record actual usage
        self.budget.record_usage(spec, tokens_in, tokens_out, project_id)

        logger.debug(
            "LLM %s/%s: in=%d out=%d cost=$%.4f elapsed=%.2fs",
            spec.provider, spec.model, tokens_in, tokens_out, cost, elapsed,
        )

        response["cost_usd"] = round(cost, 6)
        response["elapsed_secs"] = round(elapsed, 3)
        return response

    async def chat_stream(
        self,
        messages: list[dict],
        use_case: str = "chat",
        model: Optional[str] = None,
        provider: Optional[str] = None,
        project_id: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        cancel_event: Optional[asyncio.Event] = None,
        tools: Optional[list] = None,
        override: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Async streaming chat. Yields text chunks. Supports cancellation and fallback cascade."""
        if not override:
            override = f"{provider}/{model}" if provider and model else model
        spec = self.registry.resolve(use_case, project_id, override)

        estimated_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        self.budget.pre_check(spec, estimated_tokens, project_id)

        # Build fallback list: primary spec + remaining chain entries
        fallback_specs = self._get_fallback_specs(spec)

        # If primary spec requires an API key but it's missing, skip it immediately
        # rather than attempting the call and getting a 401.
        if spec.api_key is not None and not spec.api_key:
            logger.warning(
                "[LLM] API key missing for %s/%s — skipping to fallback chain",
                spec.provider, spec.model,
            )
            all_specs = fallback_specs
        else:
            all_specs = [spec] + fallback_specs

        if not all_specs:
            raise ValueError(f"No usable model found for use_case={use_case!r} — configure an API key")

        for attempt, current_spec in enumerate(all_specs):
            # Check per-model daily cap before attempting this model
            model_key = f"{current_spec.provider}/{current_spec.model}"
            try:
                self.budget.check_model_cap(model_key, estimated_tokens)
            except Exception as cap_exc:  # ModelCapExceeded
                if attempt < len(all_specs) - 1:
                    next_spec = all_specs[attempt + 1]
                    logger.warning("[LLM] %s — falling back to %s/%s", cap_exc, next_spec.provider, next_spec.model)
                    yield f"\n[SYS] {cap_exc} — switching to {next_spec.provider}/{next_spec.model}…\n"
                    continue
                from .budget_manager import BudgetExceeded
                raise BudgetExceeded(str(cap_exc))

            try:
                async for chunk in self._dispatch_chat_stream(
                    current_spec, messages, max_tokens, temperature,
                    cancel_event=cancel_event, tools=tools,
                ):
                    yield chunk
                return  # Success — done
            except Exception as exc:
                if self._is_rate_limit_error(exc) and attempt < len(all_specs) - 1:
                    next_spec = all_specs[attempt + 1]
                    logger.warning(
                        "[LLM] Rate limited on %s/%s, switching to %s/%s",
                        current_spec.provider, current_spec.model,
                        next_spec.provider, next_spec.model,
                    )
                    yield (
                        f"\n[SYS] Rate limited on {current_spec.provider}/{current_spec.model}, "
                        f"switching to {next_spec.provider}/{next_spec.model}…\n"
                    )
                    continue
                raise  # Re-raise non-rate-limit errors or if no more fallbacks

    # ------------------------------------------------------------------
    # Fallback helpers
    # ------------------------------------------------------------------

    def _get_fallback_specs(self, primary_spec) -> list:
        """Return ordered list of fallback ModelSpecs (skipping the primary)."""
        primary_key = f"{primary_spec.provider}/{primary_spec.model}"
        specs = []
        for key in self.registry.get_fallback_chain():
            if key == primary_key:
                continue
            fallback = self.registry.get(key)
            if fallback and (fallback.api_key is None or fallback.api_key):
                specs.append(fallback)
        return specs

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """Return True if the exception is transient (rate-limit, overload, unavailable).

        These errors warrant trying the fallback chain rather than hard-failing.
        """
        exc_type = type(exc).__name__
        # Anthropic SDK classes
        if exc_type in ("RateLimitError", "OverloadedError", "APIStatusError"):
            return True
        # OpenAI SDK status codes
        if hasattr(exc, "status_code") and exc.status_code in (429, 500, 502, 503, 504):
            return True
        # httpx responses (Ollama / any provider via httpx)
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code in (429, 500, 502, 503, 504)
        # Generic string matching (last resort)
        s = str(exc).lower()
        return any(k in s for k in ("429", "503", "rate limit", "overloaded", "service unavailable"))

    # Backward-compat alias used in tests
    _is_rate_limit_error = _is_transient_error


    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed(self, text: str, project_id: Optional[str] = None) -> list[float]:
        """Embed text using the configured embedding provider."""
        from memory.embedding_provider import get_embedding_provider
        embedder = get_embedding_provider(self.config)
        vec = embedder.embed(text)
        return vec.tolist()

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _dispatch_chat(
        self,
        spec,
        messages: list[dict],
        max_tokens: Optional[int],
        temperature: float,
        tools: Optional[list] = None,
        **kwargs,
    ) -> dict:
        if spec.provider == "anthropic":
            # tools not passed — Anthropic uses streaming; non-streaming path doesn't support tools
            return self._chat_anthropic(spec, messages, max_tokens, temperature, **kwargs)
        elif spec.provider in _OPENAI_COMPAT_PROVIDERS:
            return self._chat_openai_compat(spec, messages, max_tokens, temperature, tools=tools, **kwargs)
        elif spec.provider == "ollama":
            return self._chat_ollama(spec, messages, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {spec.provider}")

    async def _dispatch_chat_stream(self, spec, messages, max_tokens, temperature, cancel_event=None, tools=None):
        # If model is configured to not stream, fall back to a single non-streaming call
        if not spec.stream:
            result = self._dispatch_chat(spec, messages, max_tokens, temperature, tools=tools)
            yield result.get("content", "")
            return
        if spec.provider == "anthropic":
            async for chunk in self._stream_anthropic(spec, messages, max_tokens, temperature, cancel_event=cancel_event):
                yield chunk
        elif spec.provider in _OPENAI_COMPAT_PROVIDERS:
            async for chunk in self._stream_openai_compat(spec, messages, max_tokens, temperature, cancel_event=cancel_event):
                yield chunk
        elif spec.provider == "ollama":
            async for chunk in self._stream_ollama(spec, messages, max_tokens, temperature, cancel_event=cancel_event):
                yield chunk
        else:
            # Fallback: non-streaming
            result = self._dispatch_chat(spec, messages, max_tokens, temperature)
            yield result.get("content", "")

    # -- Helpers --

    @staticmethod
    def _convert_images_for_openai(messages: list) -> list:
        """Convert Anthropic-format image blocks to OpenAI image_url format."""
        converted = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_content = []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "image"
                        and block.get("source", {}).get("type") == "base64"
                    ):
                        src = block["source"]
                        mime = src.get("media_type", "image/jpeg")
                        data = src.get("data", "")
                        new_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        })
                    else:
                        new_content.append(block)
                converted.append({**msg, "content": new_content})
            else:
                converted.append(msg)
        return converted

    # -- Anthropic --

    def _chat_anthropic(self, spec, messages, max_tokens, temperature, **kwargs) -> dict:
        import anthropic
        client = anthropic.Anthropic(api_key=spec.api_key or None)
        max_tok = max_tokens or self.config.get("llm", {}).get("claude", {}).get("max_tokens", 8096)

        # Separate system message
        system = ""
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                filtered.append(m)

        if spec.top_p != 1.0:
            kwargs.setdefault("top_p", spec.top_p)
        # Extended thinking support
        if spec.has_thinking:
            thinking_param = (
                {"type": "enabled", "budget_tokens": spec.thinking_budget_tokens}
                if spec.thinking_enabled
                else {"type": "disabled"}
            )
            kwargs.setdefault("thinking", thinking_param)
        response = client.messages.create(
            model=spec.model,
            max_tokens=max_tok,
            temperature=temperature,
            system=system or anthropic.NOT_GIVEN,
            messages=filtered,
            **kwargs,
        )
        # Extract text content (skip thinking blocks)
        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        return {
            "content": text,
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_anthropic(self, spec, messages, max_tokens, temperature, cancel_event=None):
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=spec.api_key or None)
        max_tok = max_tokens or 8096

        system = ""
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                filtered.append(m)

        extra: dict = {}
        if spec.top_p != 1.0:
            extra["top_p"] = spec.top_p
        # Extended thinking support
        if spec.has_thinking:
            extra["thinking"] = (
                {"type": "enabled", "budget_tokens": spec.thinking_budget_tokens}
                if spec.thinking_enabled
                else {"type": "disabled"}
            )
        async with client.messages.stream(
            model=spec.model,
            max_tokens=max_tok,
            temperature=temperature,
            system=system or anthropic.NOT_GIVEN,
            messages=filtered,
            **extra,
        ) as stream:
            async for text in stream.text_stream:
                if cancel_event and cancel_event.is_set():
                    break
                yield text

    # -- OpenAI / OpenRouter --

    def _chat_openai_compat(self, spec, messages, max_tokens, temperature, tools=None, **kwargs) -> dict:
        import json as _json
        import openai
        client_kwargs: dict = {}
        if spec.api_key:
            client_kwargs["api_key"] = spec.api_key
        if spec.base_url:
            client_kwargs["base_url"] = spec.base_url
        if spec.extra_headers:
            client_kwargs["default_headers"] = spec.extra_headers
        client = openai.OpenAI(**client_kwargs)
        max_tok = max_tokens or 4096

        call_kwargs: dict = dict(
            model=spec.model,
            messages=self._convert_images_for_openai(messages),
            max_tokens=max_tok,
            temperature=temperature,
            top_p=spec.top_p,
            **kwargs,
        )
        if spec.native_tools and tools:
            call_kwargs["tools"] = tools

        response = client.chat.completions.create(**call_kwargs)
        msg = response.choices[0].message

        # Convert native tool_calls to code-block text format the runtime parses
        if spec.native_tools and msg.tool_calls:
            parts = []
            for tc in msg.tool_calls:
                try:
                    args = _json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                parts.append(
                    f'```tool\n{_json.dumps({"name": tc.function.name, "args": args})}\n```'
                )
            content = "\n".join(parts)
        else:
            content = msg.content or ""

        return {
            "content": content,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_openai_compat(self, spec, messages, max_tokens, temperature, cancel_event=None):
        import openai
        client_kwargs: dict = {}
        if spec.api_key:
            client_kwargs["api_key"] = spec.api_key
        if spec.base_url:
            client_kwargs["base_url"] = spec.base_url
        if spec.extra_headers:
            client_kwargs["default_headers"] = spec.extra_headers
        client = openai.AsyncOpenAI(**client_kwargs)

        stream = await client.chat.completions.create(
            model=spec.model,
            messages=self._convert_images_for_openai(messages),
            max_tokens=max_tokens or 4096,
            temperature=temperature,
            top_p=spec.top_p,
            stream=True,
        )
        in_think_block = False
        async for chunk in stream:
            if cancel_event and cancel_event.is_set():
                break
            delta = chunk.choices[0].delta.content
            if not delta:
                continue
            # Strip <think>...</think> blocks emitted by reasoning models
            if "<think>" in delta:
                in_think_block = True
            if in_think_block:
                if "</think>" in delta:
                    in_think_block = False
                    # Yield only the part after </think>
                    after = delta.split("</think>", 1)[1]
                    if after:
                        yield after
                continue
            yield delta

    # -- Ollama --

    def _chat_ollama(self, spec, messages, max_tokens, temperature) -> dict:
        import httpx
        base = os.environ.get("OLLAMA_BASE_URL") or spec.base_url or "http://localhost:11434"
        payload = {
            "model": spec.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "top_p": spec.top_p, "num_predict": max_tokens or 4096},
        }
        _RETRIES = 2
        for attempt in range(_RETRIES + 1):
            try:
                r = httpx.post(f"{base}/api/chat", json=payload, timeout=120.0)
                r.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (503, 502, 504) and attempt < _RETRIES:
                    wait = 3 * (attempt + 1)
                    logger.warning("[LLM] Ollama %s — retry %d/%d in %ds", exc.response.status_code, attempt + 1, _RETRIES, wait)
                    time.sleep(wait)
                else:
                    raise
        data = r.json()
        content = data.get("message", {}).get("content", "")
        return {
            "content": content,
            "tokens_in": data.get("prompt_eval_count", 0),
            "tokens_out": data.get("eval_count", 0),
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_ollama(self, spec, messages, max_tokens, temperature, cancel_event=None):
        import httpx
        import json
        base = os.environ.get("OLLAMA_BASE_URL") or spec.base_url or "http://localhost:11434"
        payload = {
            "model": spec.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "top_p": spec.top_p, "num_predict": max_tokens or 4096},
        }
        _RETRIES = 2
        for attempt in range(_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", f"{base}/api/chat", json=payload) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if cancel_event and cancel_event.is_set():
                                return
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                chunk = data.get("message", {}).get("content", "")
                                if chunk:
                                    yield chunk
                                if data.get("done"):
                                    return
                            except json.JSONDecodeError:
                                continue
                return  # success
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (503, 502, 504) and attempt < _RETRIES:
                    wait = 3 * (attempt + 1)
                    logger.warning("[LLM] Ollama %s — retry %d/%d in %ds", exc.response.status_code, attempt + 1, _RETRIES, wait)
                    await asyncio.sleep(wait)
                else:
                    raise
