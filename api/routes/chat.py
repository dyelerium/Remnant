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

# Active WebSocket connections — used for server-push broadcasts (e.g. WhatsApp messages)
_ws_connections: set[WebSocket] = set()

# Per-WS-session cancel events — keyed by session_id, set when client sends {"type":"stop"}
_ws_cancel_events: dict[str, asyncio.Event] = {}


async def broadcast(message: dict) -> None:
    """Push a JSON message to all currently connected WebSocket clients."""
    dead: set[WebSocket] = set()
    for ws in list(_ws_connections):  # snapshot to allow safe removal
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    _ws_connections.difference_update(dead)  # in-place, no rebind


class ChatRequest(BaseModel):
    message: str
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    channel: str = "api"
    images: Optional[list] = None  # [{mime, data}] for vision


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
        cancel_event = asyncio.Event()

        async def _watch_disconnect():
            """Poll for client disconnect and set cancel_event when detected."""
            try:
                while not cancel_event.is_set():
                    if await request.is_disconnected():
                        cancel_event.set()
                        logger.info("[CHAT] SSE client disconnected — cancelling")
                        break
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        watcher = asyncio.create_task(_watch_disconnect())
        try:
            async for chunk in orchestrator.handle(
                message=req.message,
                project_id=req.project_id,
                session_id=session_id,
                channel=req.channel,
                memory_context=memory_context,
                cancel_event=cancel_event,
                images=req.images,
            ):
                if cancel_event.is_set():
                    break
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
            if not cancel_event.is_set():
                yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
        finally:
            cancel_event.set()
            watcher.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    """WebSocket chat endpoint."""
    await ws.accept()
    orchestrator = ws.app.state.orchestrator
    retriever = ws.app.state.retriever

    _ws_connections.add(ws)
    current_session_id: Optional[str] = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            # Handle stop signal
            if data.get("type") == "stop":
                sid = data.get("session_id") or current_session_id
                if sid and sid in _ws_cancel_events:
                    _ws_cancel_events[sid].set()
                    logger.info("[CHAT] WS stop requested for session %s", sid)
                continue

            message = data.get("message", "")
            project_id = data.get("project_id")
            session_id = data.get("session_id", str(uuid.uuid4()))
            images = data.get("images")  # optional [{mime, data}] for vision
            current_session_id = session_id

            if not message:
                continue

            # Create a cancel event for this session
            cancel_event = asyncio.Event()
            _ws_cancel_events[session_id] = cancel_event

            try:
                chunks = retriever.retrieve(message, project_id=project_id)
                memory_context = retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            await ws.send_json({"type": "start", "session_id": session_id})

            try:
                async for chunk in orchestrator.handle(
                    message=message,
                    project_id=project_id,
                    session_id=session_id,
                    channel="websocket",
                    memory_context=memory_context,
                    cancel_event=cancel_event,
                    images=images,
                ):
                    if cancel_event.is_set():
                        break
                    await ws.send_json({"type": "chunk", "content": chunk})
            finally:
                _ws_cancel_events.pop(session_id, None)

            await ws.send_json({"type": "done", "session_id": session_id})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        # Cancel any in-flight session for this connection
        if current_session_id and current_session_id in _ws_cancel_events:
            _ws_cancel_events[current_session_id].set()
    finally:
        _ws_connections.discard(ws)
