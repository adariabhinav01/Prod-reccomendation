"""Per-tool-call progress ticks for the four Haiku research phases (spec
docs/handoff.md §16.2). Mirrors hooks/ledger.py's
`make_ledger_post_tool_use_hook` / `ledger_hook_matchers` factory pattern
exactly, parameterized by a progress callback instead of a `FetchLedger`.

### Why this exists

Each research phase already streams live SDK messages
(`async for _message in query(...): pass`) and discards every one of them,
so `orchestrator.py`'s one `port.report_progress(<phase name>)` call per
phase is the only feedback the user ever sees — silent for however long a
phase's fetches/searches take. §16.2: "A CLI that asks the user several
questions and then goes silent for minutes is the worst available
experience, and this is cheap to prevent." This hook is that cheap fix,
reusing the `PostToolUse` seam every phase already wires for the ledger and
cost cap rather than adding a new one.

### Reads `tool_input`, never `tool_response`

Unlike `ledger.py`'s `_extract_urls`/`_extract_status`, there is no
best-effort/unverified-shape caveat needed here: `tool_input["url"]` /
`tool_input["query"]` are exactly what the model asked to fetch/search —
its own call arguments, well-defined regardless of SDK version — not a
guess at an undocumented response shape.

### No extra rate-limiting

`PostToolUse` only fires once a tool call has genuinely executed (see
hooks/budget.py's module docstring on why its own counting lives in
`PostToolUse`, not `PreToolUse`) — so this is naturally already
deduplicated to real fetches/searches.

### Callback, not `QuestionPort`

Parameterized by a narrow `Callable[[str], Awaitable[None]]` rather than the
5-method `QuestionPort` — these phases have no other reason to know about
the port, and `port.report_progress` (a bound coroutine method) already
satisfies this type with zero wrapping at every call site.

### `format_result_progress` — diagnosing a phase that produced nothing

A live run surfaced a real case this hook alone can't explain: EXTRACTION
made 20 real `WebFetch` calls (visible via the ticks above) and zero
`record_product` calls — not zero *successful* calls, zero calls at all —
yet nothing in that phase's own `query()` stream was ever inspected beyond
`AssistantMessage`/`UserMessage` text, so there was no way to tell whether
the model's turn budget ran out mid-task versus it simply stopping on its
own. Every `query()` call's stream ends in exactly one `ResultMessage`
carrying `terminal_reason` (e.g. `"completed"`, `"max_turns"`,
`"aborted_streaming"`) and `num_turns` — `format_result_progress` renders
that into one more tick per phase so a rerun answers the question directly
instead of needing another round of guessing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from claude_agent_sdk import HookMatcher, ResultMessage

ProgressFn = Callable[[str], Awaitable[None]]


def make_progress_post_tool_use_hook(progress: ProgressFn):
    """Factory mirroring `hooks/ledger.py`'s
    `make_ledger_post_tool_use_hook` pattern. Returns a `HookCallback` that
    reports one line per genuinely-executed `WebFetch`/`WebSearch` call;
    every other event this hook might be matched against (it shouldn't be,
    given `progress_hook_matchers`' matcher string, but hooks are best kept
    defensive) is a no-op."""

    async def _progress_hook(hook_input, tool_use_id, context):  # noqa: ARG001
        tool_name = hook_input.get("tool_name")
        tool_input = hook_input.get("tool_input") or {}

        if tool_name == "WebFetch":
            url = tool_input.get("url")
            if url:
                await progress(f"  fetched {url}")
        elif tool_name == "WebSearch":
            query = tool_input.get("query")
            if query:
                await progress(f"  searched: {query}")

        return {}

    return _progress_hook


def format_result_progress(result: ResultMessage) -> str:
    """One tick summarizing how a phase's `query()` call ended: turn count,
    `terminal_reason` (why the loop stopped — `"completed"` is the healthy
    case; `"max_turns"`/`"aborted_streaming"`/`"aborted_tools"` all mean it
    was cut off before the model chose to stop), and `is_error`. Pure
    formatting — callers are responsible for spotting `ResultMessage` in
    their own `query()` stream and calling `progress(...)` with this."""
    reason = result.terminal_reason or result.subtype
    status = "error" if result.is_error else "ok"
    return f"  [{status}] {result.num_turns} turns, terminal_reason={reason}"


def progress_hook_matchers(progress: ProgressFn | None) -> list[HookMatcher]:
    """Ready-to-use `list[HookMatcher]` for
    `ClaudeAgentOptions(hooks={"PostToolUse": [...]})`. Returns `[]` when
    `progress` is `None` so every `Sdk*` class can unconditionally append
    this to its hooks list without branching."""
    if progress is None:
        return []
    return [
        HookMatcher(matcher="WebFetch|WebSearch", hooks=[make_progress_post_tool_use_hook(progress)])
    ]
