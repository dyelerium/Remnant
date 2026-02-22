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
    max_tokens: int = 800


@router.post("/memory/search")
async def search_memory(req: SearchRequest, request: Request) -> dict:
    retriever = request.app.state.retriever
    try:
        chunks = retriever.retrieve(
            req.query,
            project_id=req.project_id,
            max_chunks=req.max_chunks,
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
    return global_index.memory_root_stats()


@router.get("/memory/recent")
async def recent_memory(limit: int = 20, request: Request = None) -> dict:
    global_index = request.app.state.global_index
    chunks = global_index.get_recent_chunks(limit=limit)
    return {"chunks": chunks, "count": len(chunks)}
