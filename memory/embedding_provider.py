"""Embedding providers — sentence-transformers (default), Ollama, OpenAI."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string. Returns float32 ndarray."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed a batch of texts."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Embedding vector dimensions."""


class SentenceTransformerEmbedding(EmbeddingProvider):
    """Local sentence-transformers embedding (no external service required)."""

    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "sentence-transformers is required. Run: pip install sentence-transformers"
            ) from e
        logger.info("Loading sentence-transformer model: %s", model)
        self._model = SentenceTransformer(model)
        self._model_name = model
        self._dim: int = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> np.ndarray:
        vec = self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return np.array(vec, dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        vecs = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [np.array(v, dtype=np.float32) for v in vecs]

    @property
    def dimensions(self) -> int:
        return self._dim


class OllamaEmbedding(EmbeddingProvider):
    """Ollama-based embedding provider (local LLM server)."""

    def __init__(self, config: dict) -> None:
        import httpx
        self._client = httpx.Client(timeout=30.0)
        self._base_url: str = config["embedding"].get(
            "base_url", "http://localhost:11434"
        )
        self._model: str = config["embedding"].get("model", "nomic-embed-text")
        self._dim: int = config["embedding"].get("dimensions", 384)

    def embed(self, text: str) -> np.ndarray:
        response = self._client.post(
            f"{self._base_url}/api/embeddings",
            json={"model": self._model, "prompt": text},
        )
        response.raise_for_status()
        return np.array(response.json()["embedding"], dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [self.embed(t) for t in texts]

    @property
    def dimensions(self) -> int:
        return self._dim


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI embedding provider."""

    def __init__(self, config: dict) -> None:
        import openai
        self._client = openai.OpenAI()
        self._model: str = config["embedding"].get("model", "text-embedding-3-small")
        self._dim: int = config["embedding"].get("dimensions", 1536)

    def embed(self, text: str) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=text)
        return np.array(response.data[0].embedding, dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [np.array(item.embedding, dtype=np.float32) for item in response.data]

    @property
    def dimensions(self) -> int:
        return self._dim


def get_embedding_provider(config: dict) -> EmbeddingProvider:
    """Resolve embedding provider from config."""
    provider = config.get("embedding", {}).get("provider", "sentence-transformers")

    if provider == "sentence-transformers":
        model = config.get("embedding", {}).get("model", "all-MiniLM-L6-v2")
        return SentenceTransformerEmbedding(model=model)
    elif provider == "ollama":
        return OllamaEmbedding(config)
    elif provider == "openai":
        return OpenAIEmbedding(config)
    else:
        logger.warning("Unknown embedding provider %r — falling back to sentence-transformers", provider)
        return SentenceTransformerEmbedding()
