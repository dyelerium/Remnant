"""GET/POST /memory — search + record (debug + programmatic)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["memory"])


class RecordRequest(BaseModel):
    text: str
    chunk_type: str = "log"
    project_id: Optional[str] = None
    source: str = "api"


class SearchRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    max_chunks: int = 10
    top_k: Optional[int] = None   # alias used by the web UI
    max_tokens: int = 800


@router.post("/memory/search")
async def search_memory(req: SearchRequest, request: Request) -> dict:
    retriever = request.app.state.retriever
    try:
        chunks = retriever.retrieve(
            req.query,
            project_id=req.project_id,
            max_chunks=req.top_k or req.max_chunks,
            max_tokens=req.max_tokens,
        )
        return {"chunks": chunks, "count": len(chunks)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/memory/record")
async def record_memory(req: RecordRequest, request: Request) -> dict:
    recorder = request.app.state.recorder
    try:
        chunk_ids = recorder.record(
            req.text,
            chunk_type=req.chunk_type,
            project_id=req.project_id,
            source=req.source,
        )
        if chunk_ids is None:
            raise HTTPException(status_code=422, detail="Content blocked by security filter")
        return {"chunk_ids": chunk_ids, "count": len(chunk_ids)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/memory/stats")
async def memory_stats(request: Request) -> dict:
    global_index = request.app.state.global_index
    redis = request.app.state.redis
    fs_stats = global_index.memory_root_stats()

    # Count chunks and unique projects from Redis
    try:
        from memory.memory_schema import RECENT_ZSET_KEY
        total_chunks = redis.r.zcard(RECENT_ZSET_KEY)
        # Scan all chunk hashes for unique project_ids
        project_ids = set()
        chunk_ids = redis.r.zrange(RECENT_ZSET_KEY, 0, -1)
        for cid_bytes in chunk_ids:
            cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
            pid = redis.r.hget(f"remnant:chunk:{cid}", "project_id")
            if pid:
                project_ids.add(pid.decode() if isinstance(pid, bytes) else pid)
        total_projects = len(project_ids)
    except Exception:
        total_chunks = 0
        total_projects = 0

    return {**fs_stats, "total_chunks": total_chunks, "total_projects": total_projects}


@router.get("/memory/recent")
async def recent_memory(limit: int = 20, project_id: Optional[str] = None, request: Request = None) -> dict:
    global_index = request.app.state.global_index
    chunks = global_index.get_recent_chunks(limit=limit)
    if project_id:  # filter by project when specified
        chunks = [c for c in chunks if c.get("project_id") == project_id]
    return {"chunks": chunks, "count": len(chunks)}


# ------------------------------------------------------------------
# Memory file CRUD (Markdown source-of-truth files)
# ------------------------------------------------------------------

def _memory_root(request: Request) -> Path:
    config = request.app.state.config
    return Path(config.get("memory_root", "./memory"))


def _safe_path(memory_root: Path, rel_path: str) -> Path:
    """Resolve relative path and ensure it stays within memory root."""
    resolved = (memory_root / rel_path).resolve()
    if not str(resolved).startswith(str(memory_root.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if resolved.suffix.lower() not in (".md", ".txt"):
        raise HTTPException(status_code=400, detail="Only .md and .txt files allowed")
    return resolved


@router.get("/memory/files")
async def list_memory_files(request: Request) -> dict:
    """List all Markdown files in the memory root."""
    root = _memory_root(request)
    files = []
    if root.exists():
        for p in sorted(root.rglob("*.md")):
            rel = str(p.relative_to(root))
            stat = p.stat()
            files.append({
                "path": rel,
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            })
    return {"files": files, "count": len(files)}


@router.get("/memory/file")
async def read_memory_file(path: str, request: Request) -> dict:
    """Read content of a memory Markdown file."""
    root = _memory_root(request)
    target = _safe_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


class FileWriteRequest(BaseModel):
    path: str
    content: str


@router.put("/memory/file")
async def write_memory_file(body: FileWriteRequest, request: Request) -> dict:
    """Write (overwrite) a memory Markdown file."""
    root = _memory_root(request)
    target = _safe_path(root, body.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    old_mask = os.umask(0o000)
    try:
        target.write_text(body.content, encoding="utf-8")
        try:
            target.chmod(0o666)
        except OSError:
            pass
    finally:
        os.umask(old_mask)
    return {"path": body.path, "size": target.stat().st_size}


@router.delete("/memory/chunk/{chunk_id}")
async def delete_memory_chunk(chunk_id: str, request: Request) -> dict:
    """Delete a memory chunk from Redis."""
    redis = request.app.state.redis
    chunk = redis.get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id!r} not found")
    redis.delete_chunk(chunk_id)
    return {"deleted": chunk_id}
