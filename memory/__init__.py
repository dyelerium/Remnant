"""Remnant Memory System — Redis Stack + Markdown source of truth"""
from .redis_client import RemnantRedisClient
from .embedding_provider import get_embedding_provider, EmbeddingProvider, SentenceTransformerEmbedding
from .memory_retriever import MemoryRetriever
from .memory_recorder import MemoryRecorder
from .memory_compactor import MemoryCompactor
from .memory_indexer import MemoryIndexer
from .memory_schema import ImportanceLabel, ChunkType
from .chunking import auto_chunk, chunk_markdown, TextChunk
from .project_index import ProjectIndex
from .global_index import GlobalIndex
from .curator_bridge import CuratorBridge

__all__ = [
    "RemnantRedisClient",
    "get_embedding_provider",
    "EmbeddingProvider",
    "SentenceTransformerEmbedding",
    "MemoryRetriever",
    "MemoryRecorder",
    "MemoryCompactor",
    "MemoryIndexer",
    "ImportanceLabel",
    "ChunkType",
    "auto_chunk",
    "chunk_markdown",
    "TextChunk",
    "ProjectIndex",
    "GlobalIndex",
    "CuratorBridge",
]
