"""POST /chat, WS /ws — session start/continue, project selection."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    channel: str = "api"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    """Streaming chat endpoint. Returns SSE text/event-stream."""
    orchestrator = request.app.state.orchestrator
    retriever = request.app.state.retriever

    session_id = req.session_id or str(uuid.uuid4())

    # Pre-fetch memory context
    try:
        chunks = retriever.retrieve(req.message, project_id=req.project_id)
        memory_context = retriever.format_for_prompt(chunks)
    except Exception:
        memory_context = ""

    async def event_stream():
        async for chunk in orchestrator.handle(
            message=req.message,
            project_id=req.project_id,
            session_id=session_id,
            channel=req.channel,
            memory_context=memory_context,
        ):
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """WebSocket chat endpoint."""
    await ws.accept()
    orchestrator = ws.app.state.orchestrator
    retriever = ws.app.state.retriever

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            message = data.get("message", "")
            project_id = data.get("project_id")
            session_id = data.get("session_id", str(uuid.uuid4()))

            if not message:
                continue

            try:
                chunks = retriever.retrieve(message, project_id=project_id)
                memory_context = retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            await ws.send_json({"type": "start", "session_id": session_id})

            async for chunk in orchestrator.handle(
                message=message,
                project_id=project_id,
                session_id=session_id,
                channel="websocket",
                memory_context=memory_context,
            ):
                await ws.send_json({"type": "chunk", "content": chunk})

            await ws.send_json({"type": "done", "session_id": session_id})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
