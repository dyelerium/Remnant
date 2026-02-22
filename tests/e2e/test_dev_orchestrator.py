"""E2E: Dev Orchestrator sends task to Claude Code via MCP, validates result."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.project_manager import ProjectManager


@pytest.fixture
def mock_redis():
    r = MagicMock()
    store = {}

    r.hset.side_effect = lambda k, k2=None, v=None: store.update({k: {k2: v}} if k2 else {})
    r.hget.side_effect = lambda k, f: None
    r.hgetall.return_value = {}
    r.hdel.return_value = 1

    rc = MagicMock()
    rc.r = r
    return rc


@pytest.fixture
def project_manager(mock_redis, tmp_path):
    config = {"memory_root": str(tmp_path)}
    pm = ProjectManager(mock_redis, config)
    return pm


@pytest.fixture
def mock_mcp():
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value={
        "content": [{"type": "text", "text": "Task completed successfully"}]
    })
    return mcp


class TestDevOrchestrator:
    @pytest.mark.asyncio
    async def test_send_task_to_claude_code(self, project_manager, mock_mcp, tmp_path):
        """Test that Dev Orchestrator sends task via MCP correctly."""
        # Create a test project
        project = {
            "project_id": "test_dev_project",
            "name": "Test Dev Project",
            "description": "A test project",
            "working_dir": str(tmp_path),
        }

        # Manually inject project into redis mock (clear side_effect so return_value takes effect)
        import json
        project_manager.redis.hget.side_effect = None
        project_manager.redis.hget.return_value = json.dumps(project).encode()

        result = await project_manager.send_to_claude_code(
            "test_dev_project",
            "Add a hello world function to the codebase",
            mock_mcp,
        )

        assert result["status"] == "sent"
        assert result["project_id"] == "test_dev_project"
        assert mock_mcp.call_tool.called

        # Verify the MCP call was made with agent_run tool
        call_args = mock_mcp.call_tool.call_args
        assert call_args[0][0] == "agent_run"
        assert "Add a hello world" in call_args[0][1]["message"]

    @pytest.mark.asyncio
    async def test_send_task_mcp_failure(self, project_manager, mock_mcp, tmp_path):
        """Test graceful handling of MCP failures."""
        import json
        project = {
            "project_id": "test_dev_project",
            "name": "Test",
            "description": "Test",
            "working_dir": str(tmp_path),
        }
        project_manager.redis.hget.side_effect = None
        project_manager.redis.hget.return_value = json.dumps(project).encode()
        mock_mcp.call_tool.side_effect = ConnectionError("MCP server unreachable")

        result = await project_manager.send_to_claude_code(
            "test_dev_project",
            "Some task",
            mock_mcp,
        )

        assert result["status"] == "error"
        assert "error" in result

    def test_generate_claude_md(self, project_manager):
        """Test CLAUDE.md generation."""
        project = {
            "name": "My Project",
            "description": "A cool project",
            "working_dir": "/workspace/my_project",
        }
        claude_md = project_manager.generate_claude_md(project)
        assert "My Project" in claude_md
        assert "A cool project" in claude_md
        assert "/workspace/my_project" in claude_md
        assert "Claude Code Instructions" in claude_md
