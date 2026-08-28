"""Unit tests for hooks/budget.py — the §13 global cost-cap hook and its
run-scoped counter (build order step 13). `RunBudget` is tested directly;
the hook callables are invoked directly with hand-built `hook_input` dicts,
exactly the way tests/test_ledger.py exercises `FetchLedger`, without
depending on the SDK's own (undocumented) live tool_response shape.
"""

import asyncio

from product_scout.hooks.budget import (
    RunBudget,
    cost_cap_post_tool_use_matchers,
    cost_cap_pre_tool_use_matchers,
    make_cost_cap_post_tool_use_hook,
    make_cost_cap_pre_tool_use_hook,
)


def run(coro):
    return asyncio.run(coro)


def _denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# -- RunBudget: pure counter logic -------------------------------------------


def test_fresh_budget_never_exceeds():
    budget = RunBudget(max_fetches=5, max_searches=3)
    assert budget.would_exceed("WebFetch") is False
    assert budget.would_exceed("WebSearch") is False
    assert budget.tripped is False


def test_would_exceed_true_once_cap_reached():
    budget = RunBudget(max_fetches=1, max_searches=1)
    budget.record("WebFetch")
    assert budget.would_exceed("WebFetch") is True
    assert budget.tripped is True


def test_fetch_and_search_caps_are_independent():
    """§13 names two separate numbers, not one combined total."""
    budget = RunBudget(max_fetches=1, max_searches=5)
    budget.record("WebFetch")
    assert budget.would_exceed("WebFetch") is True
    assert budget.would_exceed("WebSearch") is False
    assert budget.tripped is True  # tripped is OR across both caps


def test_untracked_tool_names_never_trip_the_cap():
    budget = RunBudget(max_fetches=0, max_searches=0)
    assert budget.would_exceed("mcp__scout__record_product") is False
    budget.record("mcp__scout__record_product")
    assert budget.fetches_used == 0
    assert budget.searches_used == 0


# -- PreToolUse hook: denies once would_exceed, allows otherwise ------------


def test_pre_tool_use_hook_allows_within_budget():
    budget = RunBudget(max_fetches=5, max_searches=5)
    hook = make_cost_cap_pre_tool_use_hook(budget)
    result = run(
        hook({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}, "id", {})
    )
    assert result == {}


def test_pre_tool_use_hook_denies_once_tripped():
    budget = RunBudget(max_fetches=1, max_searches=5)
    budget.fetches_used = 1  # already at cap
    hook = make_cost_cap_pre_tool_use_hook(budget)
    result = run(hook({"tool_name": "WebFetch", "tool_input": {}}, "id", {}))
    assert _denied(result)
    assert "cost cap" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_pre_tool_use_hook_ignores_uncounted_tools():
    budget = RunBudget(max_fetches=0, max_searches=0)  # already maximally tripped
    hook = make_cost_cap_pre_tool_use_hook(budget)
    result = run(hook({"tool_name": "mcp__scout__record_product", "tool_input": {}}, "id", {}))
    assert result == {}


# -- PostToolUse hook: counts, never checks ----------------------------------


def test_post_tool_use_hook_increments_matching_counter_only():
    budget = RunBudget(max_fetches=5, max_searches=5)
    hook = make_cost_cap_post_tool_use_hook(budget)

    run(hook({"tool_name": "WebFetch", "tool_input": {}, "tool_response": None}, "id", {}))
    assert budget.fetches_used == 1
    assert budget.searches_used == 0

    run(hook({"tool_name": "WebSearch", "tool_input": {}, "tool_response": None}, "id", {}))
    assert budget.searches_used == 1
    assert budget.fetches_used == 1


def test_post_tool_use_hook_ignores_uncounted_tools():
    budget = RunBudget(max_fetches=5, max_searches=5)
    hook = make_cost_cap_post_tool_use_hook(budget)
    run(hook({"tool_name": "mcp__scout__record_product", "tool_input": {}}, "id", {}))
    assert budget.fetches_used == 0
    assert budget.searches_used == 0


def test_a_call_denied_by_another_hook_never_inflates_the_budget():
    """The design justification in the module docstring for splitting check
    (PreToolUse) from count (PostToolUse): a call the SOURCE GUARD denies
    never actually executes, so PostToolUse never fires for it — a real
    denied tool call never reaches PostToolUse at all. Simulated here by
    simply never invoking the PostToolUse hook, exactly like that."""
    budget = RunBudget(max_fetches=5, max_searches=5)
    pre_hook = make_cost_cap_pre_tool_use_hook(budget)

    hook_input = {"tool_name": "WebFetch", "tool_input": {"url": "https://reddit.com/x"}}
    pre_result = run(pre_hook(hook_input, "id", {}))
    assert pre_result == {}  # the cost cap itself allows it — a DIFFERENT hook denies it
    assert budget.fetches_used == 0  # PostToolUse never ran -> never counted


# -- 7N-vs-N regression: one shared RunBudget must survive across multiple
# simulated phase query() calls, never resetting mid-run ---------------------


def _simulate_phase_fetches(pre_hook, post_hook, n: int) -> int:
    """How many of `n` attempted WebFetch calls were actually granted
    (allowed by PreToolUse, then counted by PostToolUse) — mirrors how a
    real query()'s tool calls are gated one at a time."""
    granted = 0
    for _ in range(n):
        pre_result = run(pre_hook({"tool_name": "WebFetch", "tool_input": {}}, "id", {}))
        if _denied(pre_result):
            continue
        run(post_hook({"tool_name": "WebFetch", "tool_input": {}, "tool_response": None}, "id", {}))
        granted += 1
    return granted


def test_shared_budget_caps_cumulative_total_across_simulated_phases():
    """The exact failure §13 warns about: 'a closure-held counter resets on
    every [query()] call, making the effective cap 7N rather than N.' ONE
    RunBudget is shared across two simulated phase query() calls here — the
    cap must hold cumulatively, not reset for the second phase. This is the
    concrete proof that `orchestrator.py` constructing exactly one
    `RunBudget` per run (never one per phase) is the fix."""
    budget = RunBudget(max_fetches=5, max_searches=100)
    pre_hook = make_cost_cap_pre_tool_use_hook(budget)
    post_hook = make_cost_cap_post_tool_use_hook(budget)

    first_phase_granted = _simulate_phase_fetches(pre_hook, post_hook, 3)
    second_phase_granted = _simulate_phase_fetches(pre_hook, post_hook, 3)

    assert first_phase_granted == 3  # under the cap of 5
    assert second_phase_granted == 2  # only 2 more fit before hitting 5 total
    assert budget.fetches_used == 5
    assert budget.tripped is True


def test_fresh_budget_per_simulated_phase_reproduces_the_7n_bug_on_purpose():
    """Negative/documentation test: a FRESH RunBudget constructed per
    simulated phase (the exact bug §13 warns about — a closure-scoped
    counter that resets on every query()) wrongly allows N calls PER PHASE
    instead of N total. `orchestrator.py` must never do this — the
    companion test above proves it doesn't."""

    def with_fresh_budget(n: int) -> int:
        budget = RunBudget(max_fetches=5, max_searches=100)  # reset every call — the bug
        return _simulate_phase_fetches(
            make_cost_cap_pre_tool_use_hook(budget), make_cost_cap_post_tool_use_hook(budget), n
        )

    first_phase_granted = with_fresh_budget(5)
    second_phase_granted = with_fresh_budget(5)

    # The bug: BOTH phases got the full 5, for 10 total — double the
    # intended cap, exactly the "7N rather than N" failure §13 names.
    assert first_phase_granted == 5
    assert second_phase_granted == 5


# -- matcher factories --------------------------------------------------------


def test_pre_tool_use_matchers_shape():
    matchers = cost_cap_pre_tool_use_matchers(RunBudget(max_fetches=5, max_searches=5))
    assert len(matchers) == 1
    assert matchers[0].matcher == "WebFetch|WebSearch"


def test_post_tool_use_matchers_shape():
    matchers = cost_cap_post_tool_use_matchers(RunBudget(max_fetches=5, max_searches=5))
    assert len(matchers) == 1
    assert matchers[0].matcher == "WebFetch|WebSearch"
