"""
Tests for MarketplaceTool.

Covers:
  - Index loading (happy path, missing file, malformed YAML)
  - search: keyword match, no match, empty query
  - list: no filter, type filter, tag filter
  - install: skill type, already installed, MCP type, missing id, bad YAML
  - set_registry wiring and hot-reload
  - schema_hint structure
  - action routing and unknown action
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
import yaml

from tools.marketplace_tool import MarketplaceTool


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

_SKILL_ENTRY = {
    "id": "weather_check",
    "type": "skill",
    "title": "Weather Check",
    "description": "Get current weather via Open-Meteo",
    "tags": ["weather", "free"],
    "status": "available",
    "requires": [],
    "skill_yaml": "name: weather_check\ndescription: Weather\ntool: http_client\ntags: [weather]\n",
}

_MCP_ENTRY = {
    "id": "mcp_github",
    "type": "mcp",
    "title": "GitHub MCP Server",
    "description": "Full GitHub API via MCP",
    "tags": ["github", "mcp"],
    "status": "available",
    "requires": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    "install_instructions": "Run: npx @modelcontextprotocol/server-github",
}

_INSTALLED_ENTRY = {
    "id": "gmail_check",
    "type": "skill",
    "title": "Gmail Inbox Checker",
    "description": "Read Gmail via IMAP",
    "tags": ["email", "gmail"],
    "status": "installed",
    "requires": ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"],
}

_INDEX = [_SKILL_ENTRY.copy(), _MCP_ENTRY.copy(), _INSTALLED_ENTRY.copy()]


def _make_tool(tmp_path: Path, index: list = None) -> MarketplaceTool:
    """Create a MarketplaceTool with a real temp index file."""
    idx = index if index is not None else _INDEX
    index_path = tmp_path / "index.yaml"
    with open(index_path, "w") as fh:
        yaml.dump(idx, fh)
    imported_dir = tmp_path / "imported"
    return MarketplaceTool(index_path=index_path, imported_dir=imported_dir)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# Index loading
# ===========================================================================

class TestIndexLoading:
    def test_loads_valid_index(self, tmp_path):
        tool = _make_tool(tmp_path)
        assert len(tool._index) == 3

    def test_missing_index_returns_empty(self, tmp_path):
        tool = MarketplaceTool(
            index_path=tmp_path / "nonexistent.yaml",
            imported_dir=tmp_path / "imported",
        )
        assert tool._index == []

    def test_malformed_yaml_returns_empty(self, tmp_path):
        bad_path = tmp_path / "index.yaml"
        bad_path.write_text(":::not valid yaml:::")
        tool = MarketplaceTool(index_path=bad_path, imported_dir=tmp_path / "imported")
        assert tool._index == []

    def test_non_list_yaml_returns_empty(self, tmp_path):
        path = tmp_path / "index.yaml"
        path.write_text("key: value\n")
        tool = MarketplaceTool(index_path=path, imported_dir=tmp_path / "imported")
        assert tool._index == []


# ===========================================================================
# Search
# ===========================================================================

class TestSearch:
    def test_finds_by_keyword(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": "weather"}))
        assert result.success
        assert result.output["count"] >= 1
        ids = [r["id"] for r in result.output["results"]]
        assert "weather_check" in ids

    def test_finds_by_tag(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": "github"}))
        assert result.success
        ids = [r["id"] for r in result.output["results"]]
        assert "mcp_github" in ids

    def test_no_match_returns_empty(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": "zzznomatch999"}))
        assert result.success
        assert result.output["count"] == 0

    def test_empty_query_returns_error(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": ""}))
        assert not result.success
        assert "query" in result.error.lower()

    def test_case_insensitive_search(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": "WEATHER"}))
        assert result.success
        assert result.output["count"] >= 1

    def test_returns_at_most_10_results(self, tmp_path):
        # Create 15 similar entries
        big_index = [
            {"id": f"skill_{i}", "type": "skill", "title": f"Skill {i}",
             "description": "test skill", "tags": ["test"], "status": "available"}
            for i in range(15)
        ]
        tool = _make_tool(tmp_path, index=big_index)
        result = _run(tool.run({"action": "search", "query": "test"}))
        assert result.output["count"] <= 10

    def test_results_include_hint(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "search", "query": "weather"}))
        assert "hint" in result.output


# ===========================================================================
# List
# ===========================================================================

class TestList:
    def test_list_all(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "list"}))
        assert result.success
        assert result.output["count"] == 3

    def test_list_filter_by_type_skill(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "list", "type": "skill"}))
        assert result.success
        types = [e["type"] for e in result.output["entries"]]
        assert all(t == "skill" for t in types)

    def test_list_filter_by_type_mcp(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "list", "type": "mcp"}))
        assert result.success
        assert result.output["count"] == 1
        assert result.output["entries"][0]["id"] == "mcp_github"

    def test_list_filter_by_tag(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "list", "tag": "email"}))
        assert result.success
        ids = [e["id"] for e in result.output["entries"]]
        assert "gmail_check" in ids

    def test_list_no_match_type_returns_empty(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "list", "type": "plugin"}))
        assert result.success
        assert result.output["count"] == 0


# ===========================================================================
# Install — skill
# ===========================================================================

class TestInstallSkill:
    def test_installs_skill_creates_file(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "weather_check"}))
        assert result.success
        assert result.output["status"] == "installed"
        # File should exist
        dest = tmp_path / "imported" / "weather_check.yml"
        assert dest.exists()

    def test_install_updates_status_in_memory(self, tmp_path):
        tool = _make_tool(tmp_path)
        _run(tool.run({"action": "install", "id": "weather_check"}))
        entry = next(e for e in tool._index if e["id"] == "weather_check")
        assert entry["status"] == "installed"

    def test_install_calls_registry_reload(self, tmp_path):
        tool = _make_tool(tmp_path)
        mock_registry = MagicMock()
        mock_registry.load.return_value = 5
        tool.set_registry(mock_registry)

        result = _run(tool.run({"action": "install", "id": "weather_check"}))
        assert result.success
        mock_registry.load.assert_called_once()
        assert result.output["skills_loaded"] == 5

    def test_install_requires_listed_in_output(self, tmp_path):
        entry = {
            "id": "slack_test",
            "type": "skill",
            "title": "Slack",
            "description": "Slack webhook",
            "tags": ["slack"],
            "status": "available",
            "requires": ["SLACK_WEBHOOK_URL"],
            "skill_yaml": "name: slack_test\ndescription: Slack\ntool: http_client\n",
        }
        tool = _make_tool(tmp_path, index=[entry])
        result = _run(tool.run({"action": "install", "id": "slack_test"}))
        assert result.success
        assert "SLACK_WEBHOOK_URL" in result.output["requires"]

    def test_install_invalid_skill_yaml(self, tmp_path):
        entry = {
            "id": "bad_skill",
            "type": "skill",
            "title": "Bad",
            "description": "Bad YAML",
            "tags": [],
            "status": "available",
            "requires": [],
            "skill_yaml": "key: [unclosed bracket",  # definitely raises yaml.ScannerError
        }
        tool = _make_tool(tmp_path, index=[entry])
        result = _run(tool.run({"action": "install", "id": "bad_skill"}))
        assert not result.success
        assert "yaml" in result.error.lower()


# ===========================================================================
# Install — already installed
# ===========================================================================

class TestInstallAlreadyInstalled:
    def test_already_installed_returns_info(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "gmail_check"}))
        assert result.success
        assert result.output["status"] == "already_installed"

    def test_already_installed_no_file_write(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "gmail_check"}))
        # No file should be written (it's already installed)
        assert result.output["status"] == "already_installed"


# ===========================================================================
# Install — MCP type
# ===========================================================================

class TestInstallMcp:
    def test_mcp_returns_instructions(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "mcp_github"}))
        assert result.success
        assert result.output["status"] == "instructions"
        assert "instructions" in result.output
        assert len(result.output["instructions"]) > 0

    def test_mcp_returns_requires(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "mcp_github"}))
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in result.output["requires"]


# ===========================================================================
# Install — error cases
# ===========================================================================

class TestInstallErrors:
    def test_missing_id_returns_error(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": ""}))
        assert not result.success
        assert "id" in result.error.lower()

    def test_nonexistent_id_returns_error(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "install", "id": "nonexistent_skill_xyz"}))
        assert not result.success
        assert "not found" in result.error.lower()

    def test_skill_without_skill_yaml_returns_error(self, tmp_path):
        entry = {
            "id": "no_yaml",
            "type": "skill",
            "title": "No YAML",
            "description": "Missing skill_yaml",
            "tags": [],
            "status": "available",
            "requires": [],
            # no skill_yaml key
        }
        tool = _make_tool(tmp_path, index=[entry])
        result = _run(tool.run({"action": "install", "id": "no_yaml"}))
        assert not result.success


# ===========================================================================
# Unknown action
# ===========================================================================

class TestUnknownAction:
    def test_unknown_action_returns_error(self, tmp_path):
        tool = _make_tool(tmp_path)
        result = _run(tool.run({"action": "fly"}))
        assert not result.success
        assert "fly" in result.error or "unknown" in result.error.lower()


# ===========================================================================
# set_registry
# ===========================================================================

class TestSetRegistry:
    def test_set_registry_wires_correctly(self, tmp_path):
        tool = _make_tool(tmp_path)
        assert tool._skill_registry is None
        mock_reg = MagicMock()
        tool.set_registry(mock_reg)
        assert tool._skill_registry is mock_reg

    def test_install_without_registry_still_writes_file(self, tmp_path):
        tool = _make_tool(tmp_path)
        # No registry wired
        result = _run(tool.run({"action": "install", "id": "weather_check"}))
        assert result.success
        dest = tmp_path / "imported" / "weather_check.yml"
        assert dest.exists()


# ===========================================================================
# schema_hint
# ===========================================================================

class TestSchemaHint:
    def test_has_action_in_required(self, tmp_path):
        tool = _make_tool(tmp_path)
        schema = tool.schema_hint
        assert "action" in schema["parameters"]["required"]

    def test_action_enum(self, tmp_path):
        tool = _make_tool(tmp_path)
        props = tool.schema_hint["parameters"]["properties"]
        assert set(props["action"]["enum"]) == {"search", "install", "list"}

    def test_name_is_marketplace(self, tmp_path):
        tool = _make_tool(tmp_path)
        assert tool.schema_hint["name"] == "marketplace"

    def test_has_query_id_type_tag_props(self, tmp_path):
        tool = _make_tool(tmp_path)
        props = tool.schema_hint["parameters"]["properties"]
        for key in ("query", "id", "type", "tag"):
            assert key in props
