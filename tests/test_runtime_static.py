"""
Tests: AgentRuntime static methods and helpers.

Covers:
  - _smart_use_case()        — budget mode use-case selection
  - _strip_think_blocks()    — <think>…</think> removal
  - _should_execute_tools()  — tool call detection
  - _strip_tool_blocks()     — tool block stripping
  - _parse_tool_calls()      — multi-format tool parsing
  - _build_tool_docs()       — prompt tool docs generation
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from core.runtime import AgentRuntime, _COMPLEX_KW, _CODER_KW, _SEARCH_KW


# ---------------------------------------------------------------------------
# Minimal fixture to build a runtime without real deps
# ---------------------------------------------------------------------------

@pytest.fixture
def runtime():
    mock_tool = MagicMock()
    mock_tool.schema_hint = {
        "description": "A test tool",
        "parameters": {
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    return AgentRuntime(
        memory_retriever=MagicMock(),
        memory_recorder=MagicMock(),
        llm_client=MagicMock(),
        security_manager=MagicMock(),
        curator_agent=None,
        config={"agents": {"default": {"system_prompt": "You are a helper."}}},
        tool_registry={"test_tool": mock_tool},
    )


# ===========================================================================
# _smart_use_case
# ===========================================================================

class TestSmartUseCase:
    """At least 3 tests per code path: fast, chat, planning."""

    # ---- "fast" path (short, no keywords) ----

    def test_fast_for_greeting(self):
        assert AgentRuntime._smart_use_case("hi there") == "fast"

    def test_fast_for_short_question(self):
        assert AgentRuntime._smart_use_case("What time is it?") == "fast"

    def test_fast_for_empty_message(self):
        assert AgentRuntime._smart_use_case("") == "fast"

    def test_fast_ignores_non_matching_words(self):
        assert AgentRuntime._smart_use_case("The cat sat on the mat") == "fast"

    # ---- "chat" path (code or search keywords) ----

    def test_chat_for_code_keyword(self):
        assert AgentRuntime._smart_use_case("write some code for me") == "chat"

    def test_chat_for_implement_keyword(self):
        assert AgentRuntime._smart_use_case("implement a login function") == "chat"

    def test_chat_for_search_keyword(self):
        assert AgentRuntime._smart_use_case("search for the latest news") == "chat"

    def test_chat_for_debug_keyword(self):
        assert AgentRuntime._smart_use_case("debug this script") == "chat"

    def test_chat_for_refactor_keyword(self):
        assert AgentRuntime._smart_use_case("refactor this class") == "chat"

    def test_chat_for_find_keyword(self):
        assert AgentRuntime._smart_use_case("find the answer online") == "chat"

    def test_chat_for_fetch_keyword(self):
        assert AgentRuntime._smart_use_case("fetch the latest data") == "chat"

    def test_chat_for_summarize_keyword(self):
        assert AgentRuntime._smart_use_case("summarize this article") == "chat"

    # ---- "planning" path (complex keywords or long message) ----

    def test_planning_for_analyze_keyword(self):
        assert AgentRuntime._smart_use_case("analyze the architecture") == "planning"

    def test_planning_for_compare_keyword(self):
        assert AgentRuntime._smart_use_case("compare these two approaches") == "planning"

    def test_planning_for_evaluate_keyword(self):
        assert AgentRuntime._smart_use_case("evaluate the system design") == "planning"

    def test_planning_for_design_keyword(self):
        assert AgentRuntime._smart_use_case("design a new feature") == "planning"

    def test_planning_for_long_message_exactly_301_words(self):
        long = " ".join(["word"] * 301)
        assert AgentRuntime._smart_use_case(long) == "planning"

    def test_planning_boundary_at_300_words(self):
        # Exactly 300 words → NOT long → falls through to keyword checks
        msg = " ".join(["word"] * 300)
        # No keywords → should be "fast"
        assert AgentRuntime._smart_use_case(msg) == "fast"

    def test_planning_for_301_words_no_keywords(self):
        # 301 words, none matching keywords → still "planning" due to word count
        msg = " ".join(["word"] * 301)
        assert AgentRuntime._smart_use_case(msg) == "planning"

    # ---- Case-insensitivity check ----

    def test_case_insensitive_code_keyword(self):
        assert AgentRuntime._smart_use_case("Please CODE this for me") == "chat"

    def test_case_insensitive_analyze_keyword(self):
        assert AgentRuntime._smart_use_case("ANALYZE this deeply") == "planning"

    # ---- Priority: planning > chat ----

    def test_planning_overrides_chat_when_both_keywords(self):
        # "analyze" (planning) AND "code" (chat) — planning wins
        result = AgentRuntime._smart_use_case("analyze and code this up")
        assert result == "planning"


# ===========================================================================
# _strip_think_blocks
# ===========================================================================

class TestStripThinkBlocks:
    """Remove <think>…</think> reasoning blocks."""

    def test_strips_single_block(self):
        text = "<think>internal reasoning</think>Hello!"
        assert AgentRuntime._strip_think_blocks(text) == "Hello!"

    def test_strips_multiple_blocks(self):
        text = "<think>thought 1</think>answer<think>thought 2</think>"
        assert AgentRuntime._strip_think_blocks(text) == "answer"

    def test_strips_multiline_block(self):
        text = "<think>\nline1\nline2\n</think>Final answer"
        assert AgentRuntime._strip_think_blocks(text) == "Final answer"

    def test_no_think_block_unchanged(self):
        text = "This is just a normal response."
        assert AgentRuntime._strip_think_blocks(text) == text

    def test_empty_think_block(self):
        text = "<think></think>Result"
        assert AgentRuntime._strip_think_blocks(text) == "Result"

    def test_nested_content_stripped(self):
        text = "<think><b>bold reasoning</b>\n- step 1\n- step 2</think>Done"
        assert AgentRuntime._strip_think_blocks(text) == "Done"

    def test_strips_and_strips_surrounding_whitespace(self):
        text = "  <think>stuff</think>  answer  "
        # re.sub + .strip()
        assert AgentRuntime._strip_think_blocks(text) == "answer"

    def test_empty_string_input(self):
        assert AgentRuntime._strip_think_blocks("") == ""

    def test_only_think_block_returns_empty(self):
        text = "<think>only reasoning here</think>"
        assert AgentRuntime._strip_think_blocks(text) == ""

    def test_think_block_in_middle_of_sentence(self):
        text = "start <think>middle</think> end"
        result = AgentRuntime._strip_think_blocks(text)
        assert "middle" not in result
        assert "start" in result
        assert "end" in result


# ===========================================================================
# _should_execute_tools
# ===========================================================================

class TestShouldExecuteTools:
    def _rt(self):
        return AgentRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            None, {"agents": {}},
        )

    def test_no_tool_markers(self):
        rt = self._rt()
        assert rt._should_execute_tools("plain response") is False

    def test_backtick_format_detected(self):
        rt = self._rt()
        response = '```tool\n{"name": "web_search", "args": {}}\n```'
        assert rt._should_execute_tools(response) is True

    def test_xml_tool_call_detected(self):
        rt = self._rt()
        response = "<tool_call><function=search><parameter=q>test</parameter></function></tool_call>"
        assert rt._should_execute_tools(response) is True

    def test_simple_tool_tag_detected(self):
        rt = self._rt()
        response = '<tool>{"name": "calculator", "args": {"expr": "2+2"}}</tool>'
        assert rt._should_execute_tools(response) is True

    def test_case_insensitive_backtick(self):
        rt = self._rt()
        # backtick format with uppercase letters in surrounding text
        response = 'Here is the call:\n```TOOL\n{"name":"x","args":{}}\n```'
        # _should_execute_tools lowercases the response
        assert rt._should_execute_tools(response) is True

    def test_partial_marker_not_triggered(self):
        rt = self._rt()
        # "tool" as a word in plain text should not trigger
        assert rt._should_execute_tools("This is a useful tool description.") is False

    def test_empty_string_not_triggered(self):
        rt = self._rt()
        assert rt._should_execute_tools("") is False

    def test_multiple_markers_still_true(self):
        rt = self._rt()
        response = (
            '```tool\n{"name": "a", "args": {}}\n```\n'
            '```tool\n{"name": "b", "args": {}}\n```'
        )
        assert rt._should_execute_tools(response) is True


# ===========================================================================
# _strip_tool_blocks
# ===========================================================================

class TestStripToolBlocks:
    def _rt(self):
        return AgentRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            None, {"agents": {}},
        )

    def test_strips_backtick_block(self):
        rt = self._rt()
        response = 'I will search.\n```tool\n{"name":"web_search","args":{}}\n```\nDone.'
        stripped = rt._strip_tool_blocks(response)
        assert "```tool" not in stripped
        assert "web_search" not in stripped
        assert "Done." in stripped

    def test_strips_xml_tool_call_block(self):
        rt = self._rt()
        response = "Calling tool. <tool_call>content here</tool_call> End."
        stripped = rt._strip_tool_blocks(response)
        assert "<tool_call>" not in stripped
        assert "End." in stripped

    def test_strips_simple_tool_tag(self):
        rt = self._rt()
        response = "Result: <tool>{\"name\":\"calc\",\"args\":{}}</tool> done"
        stripped = rt._strip_tool_blocks(response)
        assert "<tool>" not in stripped
        assert "done" in stripped

    def test_no_blocks_unchanged(self):
        rt = self._rt()
        response = "Just a plain response."
        assert rt._strip_tool_blocks(response) == "Just a plain response."

    def test_strips_multiple_tool_blocks(self):
        rt = self._rt()
        response = (
            '```tool\n{"name":"a","args":{}}\n```\n'
            'middle text\n'
            '```tool\n{"name":"b","args":{}}\n```'
        )
        stripped = rt._strip_tool_blocks(response)
        assert "middle text" in stripped
        assert "```tool" not in stripped

    def test_strips_multiline_backtick_block(self):
        rt = self._rt()
        response = (
            "Before.\n"
            "```tool\n"
            '{"name": "search",\n "args": {"q": "hello"}}\n'
            "```\n"
            "After."
        )
        stripped = rt._strip_tool_blocks(response)
        assert "Before." in stripped
        assert "After." in stripped
        assert "search" not in stripped

    def test_empty_string_returns_empty(self):
        rt = self._rt()
        assert rt._strip_tool_blocks("") == ""

    def test_strips_all_three_formats_in_one_response(self):
        rt = self._rt()
        response = (
            '```tool\n{"name":"a","args":{}}\n```\n'
            "<tool_call>call_b</tool_call>\n"
            '<tool>{"name":"c","args":{}}</tool>'
        )
        stripped = rt._strip_tool_blocks(response)
        assert "```tool" not in stripped
        assert "<tool_call>" not in stripped
        assert "<tool>" not in stripped


# ===========================================================================
# _parse_tool_calls
# ===========================================================================

class TestParseToolCalls:
    def _rt(self):
        return AgentRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            None, {"agents": {}},
        )

    def test_parse_backtick_format(self):
        rt = self._rt()
        response = '```tool\n{"name": "web_search", "args": {"q": "python"}}\n```'
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 1
        assert calls[0][0] == "web_search"
        assert calls[0][1] == {"q": "python"}

    def test_parse_xml_function_call_format(self):
        rt = self._rt()
        response = (
            "<tool_call>"
            "<function=calculator>"
            "<parameter=expr>2+2</parameter>"
            "</function>"
            "</tool_call>"
        )
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 1
        assert calls[0][0] == "calculator"
        assert calls[0][1]["expr"] == "2+2"

    def test_parse_simple_tool_tag_format(self):
        rt = self._rt()
        response = '<tool>{"name": "memory_retrieve", "args": {"query": "test"}}</tool>'
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 1
        assert calls[0][0] == "memory_retrieve"
        assert calls[0][1]["query"] == "test"

    def test_parse_multiple_backtick_blocks(self):
        rt = self._rt()
        response = (
            '```tool\n{"name":"a","args":{"x":1}}\n```\n'
            '```tool\n{"name":"b","args":{"y":2}}\n```'
        )
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 2
        names = {c[0] for c in calls}
        assert "a" in names
        assert "b" in names

    def test_parse_invalid_json_skipped(self):
        rt = self._rt()
        response = "```tool\nnot valid json\n```"
        calls = rt._parse_tool_calls(response)
        assert calls == []

    def test_parse_missing_name_returns_empty_string(self):
        rt = self._rt()
        response = '```tool\n{"args": {"x": 1}}\n```'
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 1
        assert calls[0][0] == ""  # no "name" key → defaults to ""

    def test_parse_empty_response_returns_empty_list(self):
        rt = self._rt()
        assert rt._parse_tool_calls("") == []

    def test_parse_plain_text_returns_empty_list(self):
        rt = self._rt()
        assert rt._parse_tool_calls("Just some response text.") == []

    def test_parse_xml_multiple_parameters(self):
        rt = self._rt()
        response = (
            "<tool_call>"
            "<function=search>"
            "<parameter=query>hello</parameter>"
            "<parameter=limit>5</parameter>"
            "</function>"
            "</tool_call>"
        )
        calls = rt._parse_tool_calls(response)
        assert len(calls) == 1
        assert calls[0][1]["query"] == "hello"
        assert calls[0][1]["limit"] == "5"


# ===========================================================================
# _build_tool_docs
# ===========================================================================

class TestBuildToolDocs:
    def test_includes_tool_name(self, runtime):
        docs = runtime._build_tool_docs()
        assert "test_tool" in docs

    def test_includes_description(self, runtime):
        docs = runtime._build_tool_docs()
        assert "A test tool" in docs

    def test_includes_parameter_info(self, runtime):
        docs = runtime._build_tool_docs()
        assert "query" in docs

    def test_required_param_marked_with_star(self, runtime):
        docs = runtime._build_tool_docs()
        # query is in required list, should have *
        assert "query*" in docs

    def test_no_tools_returns_header_only(self):
        rt = AgentRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            None, {"agents": {}},
            tool_registry={},
        )
        docs = rt._build_tool_docs()
        assert "## Tools" in docs
        assert "Available tools:" in docs

    def test_multiple_tools_all_listed(self):
        tool_a = MagicMock()
        tool_a.schema_hint = {
            "description": "Tool A",
            "parameters": {"properties": {}, "required": []},
        }
        tool_b = MagicMock()
        tool_b.schema_hint = {
            "description": "Tool B",
            "parameters": {"properties": {}, "required": []},
        }
        rt = AgentRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            None, {"agents": {}},
            tool_registry={"tool_a": tool_a, "tool_b": tool_b},
        )
        docs = rt._build_tool_docs()
        assert "tool_a" in docs
        assert "tool_b" in docs
