"""Unified LLM client — chat() + embed() over provider registry, budget-aware."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)


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
    ) -> AsyncIterator[str]:
        """Async streaming chat. Yields text chunks."""
        override = f"{provider}/{model}" if provider and model else model
        spec = self.registry.resolve(use_case, project_id, override)

        estimated_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        self.budget.pre_check(spec, estimated_tokens, project_id)

        async for chunk in self._dispatch_chat_stream(spec, messages, max_tokens, temperature):
            yield chunk

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
        **kwargs,
    ) -> dict:
        if spec.provider == "anthropic":
            return self._chat_anthropic(spec, messages, max_tokens, temperature, **kwargs)
        elif spec.provider in ("openai", "openrouter"):
            return self._chat_openai_compat(spec, messages, max_tokens, temperature, **kwargs)
        elif spec.provider == "ollama":
            return self._chat_ollama(spec, messages, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {spec.provider}")

    async def _dispatch_chat_stream(self, spec, messages, max_tokens, temperature):
        if spec.provider == "anthropic":
            async for chunk in self._stream_anthropic(spec, messages, max_tokens, temperature):
                yield chunk
        elif spec.provider in ("openai", "openrouter"):
            async for chunk in self._stream_openai_compat(spec, messages, max_tokens, temperature):
                yield chunk
        elif spec.provider == "ollama":
            async for chunk in self._stream_ollama(spec, messages, max_tokens, temperature):
                yield chunk
        else:
            # Fallback: non-streaming
            result = self._dispatch_chat(spec, messages, max_tokens, temperature)
            yield result.get("content", "")

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

        response = client.messages.create(
            model=spec.model,
            max_tokens=max_tok,
            temperature=temperature,
            system=system or anthropic.NOT_GIVEN,
            messages=filtered,
            **kwargs,
        )
        return {
            "content": response.content[0].text,
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_anthropic(self, spec, messages, max_tokens, temperature):
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

        async with client.messages.stream(
            model=spec.model,
            max_tokens=max_tok,
            temperature=temperature,
            system=system or anthropic.NOT_GIVEN,
            messages=filtered,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    # -- OpenAI / OpenRouter --

    def _chat_openai_compat(self, spec, messages, max_tokens, temperature, **kwargs) -> dict:
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

        response = client.chat.completions.create(
            model=spec.model,
            messages=messages,
            max_tokens=max_tok,
            temperature=temperature,
            **kwargs,
        )
        return {
            "content": response.choices[0].message.content,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_openai_compat(self, spec, messages, max_tokens, temperature):
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
            messages=messages,
            max_tokens=max_tokens or 4096,
            temperature=temperature,
            stream=True,
        )
        in_think_block = False
        async for chunk in stream:
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
            "options": {"temperature": temperature, "num_predict": max_tokens or 4096},
        }
        r = httpx.post(f"{base}/api/chat", json=payload, timeout=120.0)
        r.raise_for_status()
        data = r.json()
        content = data.get("message", {}).get("content", "")
        return {
            "content": content,
            "tokens_in": data.get("prompt_eval_count", 0),
            "tokens_out": data.get("eval_count", 0),
            "model": spec.model,
            "provider": spec.provider,
        }

    async def _stream_ollama(self, spec, messages, max_tokens, temperature):
        import httpx
        import json
        base = os.environ.get("OLLAMA_BASE_URL") or spec.base_url or "http://localhost:11434"
        payload = {
            "model": spec.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens or 4096},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{base}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            yield chunk
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
