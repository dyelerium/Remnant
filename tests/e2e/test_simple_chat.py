"""E2E: single lane — Orchestrator → LLM → memory round-trip."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.agent_graph import AgentGraph, AgentNode
from core.lane_manager import LaneManager, LanePriority
from core.runtime import AgentRuntime


@pytest.fixture
def mock_retriever():
    r = MagicMock()
    r.retrieve.return_value = []
    r.format_for_prompt.return_value = ""
    return r


@pytest.fixture
def mock_recorder():
    r = MagicMock()
    r.record.return_value = ["chunk-1", "chunk-2"]
    return r


@pytest.fixture
def mock_llm():
    llm = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "Hello! "
        yield "This is Remnant. "

    llm.chat_stream = fake_stream
    llm.chat.return_value = {
        "content": "Hello! This is Remnant.",
        "tokens_in": 50,
        "tokens_out": 20,
        "model": "claude-test",
        "provider": "test",
    }
    return llm


@pytest.fixture
def mock_security():
    s = MagicMock()
    s.is_safe.return_value = (True, "")
    s.redact_prompt.side_effect = lambda t: t
    s.sanitise_memory.side_effect = lambda chunks: chunks
    s.check_tool_policy.return_value = True
    return s


@pytest.fixture
def mock_curator():
    c = MagicMock()
    c.score_async = AsyncMock()
    return c


@pytest.fixture
def simple_runtime(mock_retriever, mock_recorder, mock_llm, mock_security, mock_curator):
    config = {
        "agents": {
            "default": {
                "name": "Remnant",
                "system_prompt": "You are Remnant.",
            }
        }
    }
    return AgentRuntime(
        memory_retriever=mock_retriever,
        memory_recorder=mock_recorder,
        llm_client=mock_llm,
        security_manager=mock_security,
        curator_agent=mock_curator,
        config=config,
        tool_registry={},
    )


@pytest.mark.asyncio
async def test_simple_chat_stream(simple_runtime, mock_recorder):
    """Test that run_stream yields response chunks and records memory."""
    agent = AgentNode(name="test", agent_type="default", depth=0)
    chunks = []

    async for chunk in simple_runtime.run_stream(
        message="Hello, Remnant!",
        agent_node=agent,
        project_id="test_project",
        session_id="test-session",
    ):
        chunks.append(chunk)

    # Should have received streaming chunks
    assert len(chunks) > 0
    full_response = "".join(chunks)
    assert "Hello" in full_response or "[GEN]" in full_response

    # Recorder should have been called
    assert mock_recorder.record.called


@pytest.mark.asyncio
async def test_memory_recall_on_chat(simple_runtime, mock_retriever):
    """Verify memory retrieval is called before LLM."""
    agent = AgentNode(name="test", agent_type="default", depth=0)

    async for _ in simple_runtime.run_stream(
        message="What do you know about me?",
        agent_node=agent,
    ):
        pass

    assert mock_retriever.retrieve.called
    call_args = mock_retriever.retrieve.call_args
    assert "What do you know" in call_args[0][0]


@pytest.mark.asyncio
async def test_security_sanitise_called(simple_runtime, mock_security):
    """Verify memory sanitisation is applied before prompt injection."""
    agent = AgentNode(name="test", agent_type="default", depth=0)

    async for _ in simple_runtime.run_stream(
        message="Tell me about security",
        agent_node=agent,
    ):
        pass

    assert mock_security.sanitise_memory.called
    assert mock_security.redact_prompt.called
