"""Unit tests for hooks/progress.py — the §16.2 per-tool-call progress
ticks emitted during the four Haiku research phases. The hook callable is
invoked directly with hand-built `hook_input` dicts, exactly the way
tests/test_budget.py and tests/test_ledger.py exercise their own hooks,
without depending on the SDK's own (undocumented) live tool_response shape
— this hook reads `tool_input` only, never `tool_response`, so there's
nothing undocumented to hedge around here in the first place.
"""

import asyncio

from claude_agent_sdk import ResultMessage

from product_scout.hooks.progress import (
    format_result_progress,
    make_progress_post_tool_use_hook,
    progress_hook_matchers,
)


def make_result_message(**overrides) -> ResultMessage:
    defaults = dict(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=5,
        session_id="test-session",
    )
    defaults.update(overrides)
    return ResultMessage(**defaults)


def run(coro):
    return asyncio.run(coro)


class Recorder:
    """Stand-in for `QuestionPort.report_progress` — an async callable that
    remembers every message it was given, in order."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


# -- PostToolUse hook: one tick per genuinely-executed WebFetch/WebSearch ---


def test_web_fetch_produces_one_fetched_tick():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(
        hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/x"}},
            "id",
            {},
        )
    )
    assert recorder.messages == ["  fetched https://example.com/x"]


def test_web_search_produces_one_searched_tick():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(
        hook(
            {"tool_name": "WebSearch", "tool_input": {"query": "reddit desk reviews"}},
            "id",
            {},
        )
    )
    assert recorder.messages == ["  searched: reddit desk reviews"]


def test_unrelated_tool_produces_no_tick():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(
        hook(
            {"tool_name": "mcp__scout__record_product", "tool_input": {"name": "Widget"}},
            "id",
            {},
        )
    )
    assert recorder.messages == []


def test_missing_url_produces_no_tick_and_does_not_crash():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(hook({"tool_name": "WebFetch", "tool_input": {}}, "id", {}))
    assert recorder.messages == []


def test_missing_query_produces_no_tick_and_does_not_crash():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(hook({"tool_name": "WebSearch", "tool_input": {}}, "id", {}))
    assert recorder.messages == []


def test_multiple_calls_tick_in_order():
    recorder = Recorder()
    hook = make_progress_post_tool_use_hook(recorder)
    run(hook({"tool_name": "WebSearch", "tool_input": {"query": "a"}}, "id", {}))
    run(hook({"tool_name": "WebFetch", "tool_input": {"url": "https://a.com"}}, "id", {}))
    assert recorder.messages == ["  searched: a", "  fetched https://a.com"]


# -- matcher factory ----------------------------------------------------------


def test_progress_hook_matchers_shape():
    matchers = progress_hook_matchers(Recorder())
    assert len(matchers) == 1
    assert matchers[0].matcher == "WebFetch|WebSearch"


def test_progress_hook_matchers_empty_when_no_progress_fn():
    assert progress_hook_matchers(None) == []


# -- format_result_progress: diagnosing a phase that produced nothing -------


def test_format_result_progress_prefers_terminal_reason():
    result = make_result_message(terminal_reason="max_turns", num_turns=12)
    assert format_result_progress(result) == "  [ok] 12 turns, terminal_reason=max_turns"


def test_format_result_progress_falls_back_to_subtype_when_no_terminal_reason():
    result = make_result_message(subtype="success", terminal_reason=None, num_turns=3)
    assert format_result_progress(result) == "  [ok] 3 turns, terminal_reason=success"


def test_format_result_progress_flags_errors():
    result = make_result_message(is_error=True, terminal_reason="aborted_streaming", num_turns=1)
    assert format_result_progress(result) == "  [error] 1 turns, terminal_reason=aborted_streaming"
