"""
MCP server via SSE — exposes memory_retrieve, memory_record, agent_run, skill_execute.
Compatible with Claude Code MCP configuration.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

import asyncio

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])


# -- MCP JSON-RPC models --

class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    method: str
    params: dict = {}


def mcp_result(request_id: Optional[str], result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def mcp_error(request_id: Optional[str], code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


# -- Tool definitions for MCP --
_MCP_TOOLS = [
    {
        "name": "memory_retrieve",
        "description": "Retrieve relevant memory chunks from Remnant's vector store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query"},
                "project_id": {"type": "string", "description": "Optional project scope"},
                "max_chunks": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_record",
        "description": "Record a new memory chunk in Remnant's persistent store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Memory content"},
                "chunk_type": {"type": "string", "default": "log"},
                "project_id": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "agent_run",
        "description": "Run a Remnant agent with a message and return the response.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "project_id": {"type": "string"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "skill_execute",
        "description": "Execute a registered Remnant skill by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "args": {"type": "object"},
            },
            "required": ["skill_name"],
        },
    },
]


@router.post("/mcp")
async def mcp_handler(req: MCPRequest, request: Request) -> dict:
    """JSON-RPC 2.0 MCP endpoint."""
    method = req.method
    params = req.params
    rid = req.id

    if method == "tools/list":
        return mcp_result(rid, {"tools": _MCP_TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        return await _dispatch_tool(rid, tool_name, args, request)

    elif method == "initialize":
        return mcp_result(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "remnant", "version": "1.0.0"},
        })

    else:
        return mcp_error(rid, -32601, f"Method not found: {method}")


@router.post("/mcp/stream")
async def mcp_stream(request: Request) -> StreamingResponse:
    """Streaming MCP endpoint — SSE for agent_run."""
    body = await request.json()
    tool_name = body.get("name", "")
    args = body.get("arguments", {})

    async def _stream():
        if tool_name == "agent_run":
            orchestrator = request.app.state.orchestrator
            retriever = request.app.state.retriever
            message = args.get("message", "")
            project_id = args.get("project_id")

            try:
                chunks = retriever.retrieve(message, project_id=project_id)
                memory_context = retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            async for chunk in orchestrator.handle(
                message=message,
                project_id=project_id,
                session_id=str(uuid.uuid4()),
                channel="mcp",
                memory_context=memory_context,
            ):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Internal WhatsApp webhook (called by the Node.js sidecar)
# ---------------------------------------------------------------------------

async def _process_whatsapp(
    sender: str,
    message: str,
    orchestrator,
    retriever,
    broadcast_fn,
) -> None:
    """Background task: run agent, reply on WhatsApp, push to web UI."""
    try:
        chunks = retriever.retrieve(message)
        memory_context = retriever.format_for_prompt(chunks)
    except Exception:
        memory_context = ""

    response_parts: list[str] = []
    async for chunk in orchestrator.handle(
        message=message,
        session_id=f"wa-{sender}",
        channel="whatsapp",
        memory_context=memory_context,
    ):
        response_parts.append(chunk)

    response_text = "".join(response_parts)
    if not response_text:
        return

    # 1. Send reply back to WhatsApp via sidecar.
    #    Pass the full sender ID (e.g. "351912345678@lid") so the sidecar
    #    can use it directly — avoids the "No LID for user" error that happens
    #    when we strip the suffix and reconstruct "@c.us".
    sidecar_url = os.environ.get("WHATSAPP_SIDECAR_URL", "http://remnant-whatsapp:3000")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{sidecar_url}/send",
                json={"phone": sender, "message": response_text},
            )
        logger.info("[WHATSAPP] Response sent to %s (%d chars)", sender, len(response_text))
    except Exception as exc:
        logger.error("[WHATSAPP] Failed to send response via sidecar: %s", exc)

    # 2. Push the conversation to open web UI tabs.
    if broadcast_fn:
        try:
            await broadcast_fn({
                "type": "wa_message",
                "session_id": f"wa-{sender}",
                "sender": sender,
                "user_message": message,
                "response": response_text,
            })
        except Exception as exc:
            logger.error("[WHATSAPP] Broadcast failed: %s", exc)


@router.post("/internal/whatsapp", status_code=202)
async def whatsapp_incoming(
    body: dict,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive incoming WhatsApp messages from the Node.js sidecar.

    Returns 202 immediately so the sidecar's HTTP timeout never fires,
    then processes the message (LLM + reply + broadcast) in the background.
    """
    sender = body.get("from", "unknown")
    message = body.get("body", "")

    if not message:
        return {"status": "ignored"}

    logger.info("[WHATSAPP] Message from %s: %s", sender, message[:80])

    # Capture state now — don't hold the request object in the background task
    orchestrator = request.app.state.orchestrator
    retriever = request.app.state.retriever
    broadcast_fn = getattr(request.app.state, "broadcast", None)

    background_tasks.add_task(
        _process_whatsapp,
        sender=sender,
        message=message,
        orchestrator=orchestrator,
        retriever=retriever,
        broadcast_fn=broadcast_fn,
    )
    return {"status": "accepted"}


# -- Tool dispatch helpers --

async def _dispatch_tool(rid, tool_name: str, args: dict, request: Request) -> dict:
    if tool_name == "memory_retrieve":
        retriever = request.app.state.retriever
        try:
            chunks = retriever.retrieve(
                args["query"],
                project_id=args.get("project_id"),
                max_chunks=args.get("max_chunks", 10),
            )
            formatted = retriever.format_for_prompt(chunks)
            return mcp_result(rid, {"content": [{"type": "text", "text": formatted}]})
        except Exception as exc:
            return mcp_error(rid, -32000, str(exc))

    elif tool_name == "memory_record":
        recorder = request.app.state.recorder
        try:
            ids = recorder.record(
                args["text"],
                chunk_type=args.get("chunk_type", "log"),
                project_id=args.get("project_id"),
                source="mcp",
            )
            if ids is None:
                return mcp_error(rid, -32000, "Content blocked by security filter")
            return mcp_result(rid, {"content": [{"type": "text", "text": f"Recorded {len(ids)} chunks"}]})
        except Exception as exc:
            return mcp_error(rid, -32000, str(exc))

    elif tool_name == "agent_run":
        orchestrator = request.app.state.orchestrator
        retriever = request.app.state.retriever
        message = args.get("message", "")
        project_id = args.get("project_id")

        try:
            chunks = retriever.retrieve(message, project_id=project_id)
            memory_context = retriever.format_for_prompt(chunks)
        except Exception:
            memory_context = ""

        parts = []
        async for chunk in orchestrator.handle(
            message=message,
            project_id=project_id,
            session_id=str(uuid.uuid4()),
            channel="mcp",
            memory_context=memory_context,
        ):
            parts.append(chunk)

        return mcp_result(rid, {"content": [{"type": "text", "text": "".join(parts)}]})

    elif tool_name == "skill_execute":
        skill_registry = request.app.state.skill_registry
        tool_registry = request.app.state.tool_registry
        result = await skill_registry.invoke(
            args["skill_name"],
            args.get("args", {}),
            tool_registry,
        )
        return mcp_result(rid, {"content": [{"type": "text", "text": json.dumps(result)}]})

    else:
        return mcp_error(rid, -32601, f"Unknown tool: {tool_name}")
