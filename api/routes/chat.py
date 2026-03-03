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

# Wired during startup for channel-aware routing
_redis = None
_telegram_bot = None

_PROACTIVE_QUEUE_KEY = "remnant:proactive_queue"


def init_broadcast(redis_client, telegram_bot) -> None:
    """Wire Redis and Telegram bot so broadcast can route and queue messages."""
    global _redis, _telegram_bot
    _redis = redis_client
    _telegram_bot = telegram_bot


async def broadcast(message: dict) -> None:
    """Push a message to all reachable channels.

    For proactive messages (reminders / scheduled tasks):
      1. Originating channel — Telegram or WhatsApp get the result back directly
      2. All connected WebSocket clients — web UI sees it immediately if open
      3. Redis queue — web UI delivers it on next reconnect if offline

    For all other message types: WebSocket broadcast only (existing behaviour).
    """
    if message.get("type") != "proactive":
        # Non-proactive: WebSocket only
        dead: set[WebSocket] = set()
        for ws in list(_ws_connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        _ws_connections.difference_update(dead)
        return

    # ------------------------------------------------------------------ #
    # Proactive fan-out
    # ------------------------------------------------------------------ #
    session_id = message.get("session_id", "")
    content = message.get("content", "")
    label = message.get("label") or message.get("task", "")[:40]

    # 1. Originating channel -----------------------------------------
    if session_id.startswith("tg-") and _telegram_bot:
        text = f"*{label}*\n\n{content}" if label else content
        if text:
            try:
                await _telegram_bot.send_message(session_id[3:], text)
                logger.info("[BROADCAST] Proactive → Telegram %s", session_id)
            except Exception as exc:
                logger.warning("[BROADCAST] Telegram send failed (%s): %s", session_id, exc)

    elif session_id.startswith("wa-"):
        import os
        import httpx
        phone = session_id[3:]
        text = f"{label}\n\n{content}" if label else content
        if text:
            sidecar = os.environ.get("WHATSAPP_SIDECAR_URL", "http://remnant-whatsapp:3000")
            try:
                async with httpx.AsyncClient(timeout=15.0) as _c:
                    await _c.post(f"{sidecar}/send", json={"phone": phone, "message": text})
                logger.info("[BROADCAST] Proactive → WhatsApp %s", phone)
            except Exception as exc:
                logger.warning("[BROADCAST] WhatsApp send failed (%s): %s", phone, exc)

    # 2. WebSocket push (all live clients, regardless of originating channel) ---
    dead: set[WebSocket] = set()
    ws_sent = 0
    for ws in list(_ws_connections):
        try:
            await ws.send_json(message)
            ws_sent += 1
        except Exception:
            dead.add(ws)
    _ws_connections.difference_update(dead)

    # 3. Redis queue — deliver to web UI on next reconnect if nobody was online -
    if ws_sent == 0 and _redis:
        try:
            _redis.r.lpush(_PROACTIVE_QUEUE_KEY, json.dumps(message))
            _redis.r.expire(_PROACTIVE_QUEUE_KEY, 86400)  # 24-hour TTL
            logger.info("[BROADCAST] No WS clients online — queued for reconnect")
        except Exception as exc:
            logger.warning("[BROADCAST] Redis queue failed: %s", exc)


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

    # Drain any queued proactive messages that fired while no client was connected
    try:
        redis = ws.app.state.redis
        queued_raw = redis.r.lrange(_PROACTIVE_QUEUE_KEY, 0, -1)
        if queued_raw:
            redis.r.delete(_PROACTIVE_QUEUE_KEY)
            for raw_entry in reversed(queued_raw):  # LPUSH = newest first → deliver oldest first
                try:
                    await ws.send_json(json.loads(raw_entry))
                except Exception:
                    pass
    except Exception:
        pass

    # Push any queued Telegram inbox messages to this new connection
    try:
        redis = ws.app.state.redis
        inbox_raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: redis.r.lrange("remnant:telegram_inbox", 0, 49)
        )
        for raw_entry in reversed(inbox_raw):
            try:
                entry = json.loads(raw_entry)
                await ws.send_json({"type": "tg_message", **entry})
            except Exception:
                pass
    except Exception:
        pass

    # Server-side keepalive — ping every 25s to prevent proxy/browser timeouts
    async def _heartbeat():
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                break

    heartbeat = asyncio.create_task(_heartbeat())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            # Handle pong (keepalive reply from client)
            if data.get("type") == "pong":
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
            budget_mode = bool(data.get("budget_mode", False))
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
                    budget_mode=budget_mode,
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
        heartbeat.cancel()
        _ws_connections.discard(ws)
