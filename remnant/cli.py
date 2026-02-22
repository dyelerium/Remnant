#!/usr/bin/env python3
"""Remnant CLI — index, retrieve, record memory and start the server."""
from __future__ import annotations

import sys
from pathlib import Path

import click

# Ensure project root is importable
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _get_components():
    """Lazy-load all components (avoids heavy imports on --help)."""
    from core.config_loader import get_config
    from core.logging_config import configure_logging
    from memory.redis_client import RemnantRedisClient
    from memory.embedding_provider import get_embedding_provider

    config = get_config().load()
    configure_logging(config.get("log_level", "INFO"))
    redis = RemnantRedisClient(config)
    redis.ensure_index()
    embedder = get_embedding_provider(config)
    return config, redis, embedder


@click.group()
def cli():
    """Remnant — Redis-backed hierarchical AI agent framework."""
    pass


@cli.command()
def init():
    """Initialize Remnant directory structure."""
    dirs = ["memory", "memory/projects", "logs", "workspace"]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)

    memory_file = Path("memory/MEMORY.md")
    if not memory_file.exists():
        memory_file.write_text("# Long-Term Memory\n\n")

    click.echo("✓ Initialized Remnant project structure")


@cli.command()
@click.option("--project", default=None, help="Restrict to this project")
@click.option("--force", is_flag=True, help="Re-index even if chunk exists")
def index(project, force):
    """Scan memory/ files and rebuild the Redis HNSW index."""
    from memory.memory_indexer import MemoryIndexer

    config, redis, embedder = _get_components()
    indexer = MemoryIndexer(redis, embedder, config)
    n = indexer.scan_and_index(project_id=project, force=force)
    click.echo(f"✓ Indexed {n} chunks")


@cli.command()
@click.argument("query")
@click.option("--project", default=None, help="Filter by project")
@click.option("--max-tokens", default=None, type=int, help="Max tokens to retrieve")
@click.option("--max-chunks", default=None, type=int, help="Max chunks to return")
def retrieve(query, project, max_tokens, max_chunks):
    """Retrieve relevant memory chunks for a query."""
    from memory.memory_retriever import MemoryRetriever

    config, redis, embedder = _get_components()
    retriever = MemoryRetriever(redis, embedder, config)
    chunks = retriever.retrieve(
        query,
        project_id=project,
        max_tokens=max_tokens,
        max_chunks=max_chunks,
    )
    click.echo(f"\nRetrieved {len(chunks)} chunks:\n")
    click.echo(retriever.format_for_prompt(chunks))


@cli.command()
@click.argument("text")
@click.option("--type", "chunk_type", default="log", help="Chunk type (preference, decision, learning, log)")
@click.option("--project", default=None, help="Project name")
@click.option("--source", default="cli", help="Source label")
def record(text, chunk_type, project, source):
    """Record new memory to the store."""
    from core.security import SecurityManager
    from memory.memory_recorder import MemoryRecorder

    config, redis, embedder = _get_components()
    security = SecurityManager(redis, config)
    recorder = MemoryRecorder(redis, embedder, security, config)

    chunk_ids = recorder.record(text, chunk_type=chunk_type, project_id=project, source=source)
    if chunk_ids:
        click.echo(f"✓ Recorded {len(chunk_ids)} chunks")
    else:
        click.echo("✗ Recording blocked by security filter", err=True)
        sys.exit(1)


@cli.command()
def serve():
    """Start the Remnant API server."""
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)


@cli.command()
def health():
    """Check Redis connectivity."""
    from memory.redis_client import RemnantRedisClient
    from core.config_loader import get_config

    config = get_config().load()
    redis = RemnantRedisClient(config)
    if redis.ping():
        click.echo("✓ Redis is up")
    else:
        click.echo("✗ Redis is down", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
