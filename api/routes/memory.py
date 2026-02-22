"""GET/POST /memory — search + record (debug + programmatic)."""
from __future__ import annotations

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
