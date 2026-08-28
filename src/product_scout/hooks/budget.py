"""The §13 `PreToolUse` global cost-cap hook and its run-scoped counter
(spec docs/handoff.md §13/§13.1, build order step 13).

§13, quoted: "**`PreToolUse` global — cost cap.** Hard-stop at N fetches / M
searches... **The counter must live in orchestrator state, not in the hook
closure.** §3 issues a separate `query()` per phase with `hooks=HOOKS`; a
closure-held counter resets on every one of them, making the effective cap
7N rather than N. The hook reads a counter the orchestrator owns. This fails
in the direction of spending money rather than erroring, so it will not
announce itself."

`RunBudget` is that counter — a plain, mutable, non-persisted class, exactly
like `hooks/ledger.py`'s `FetchLedger`, and for the same reason: throwaway
per-run state with nowhere in `RunRecord` (models.py, locked) for it to
live. `orchestrator.py` constructs exactly ONE `RunBudget` per run and
passes it into every research phase's `Sdk*` adapter, the same required-
parameter-no-default treatment `ledger: FetchLedger` already gets — the
concrete fix for the 7N-vs-N failure this module's own docstring quotes
above.

### Counting happens in `PostToolUse`; checking happens in `PreToolUse`

Not both in the same hook. The source guard (`hooks/source_guard.py`) is
ALSO a `PreToolUse` hook on `WebFetch`, and both hooks can be wired onto the
same call. If this module incremented its counter inside `PreToolUse` (i.e.
"spend budget just for being asked"), a call the source guard denies —
which never actually executes, never actually costs anything — would still
consume budget for money never spent. Counting in `PostToolUse` instead
means a tool call only ever counts once it has genuinely run, mirroring how
`FetchLedger` itself is only ever populated in `PostToolUse`, never in
`PreToolUse`.

### Both `WebFetch` and `WebSearch` count, with independent caps

§13 names two separate numbers ("N fetches / M searches"), not one combined
total — `RunBudget` tracks `fetches_used`/`searches_used` against
`max_fetches`/`max_searches` independently. `tripped` is true once EITHER
cap is reached; `orchestrator.py` polls it after every research phase to
decide whether to keep issuing new ones (§13.1).
"""

from __future__ import annotations

from claude_agent_sdk import HookMatcher

_COUNTED_TOOLS = ("WebFetch", "WebSearch")


class RunBudget:
    """Run-scoped hard cap on total `WebFetch`/`WebSearch` calls across
    every phase's `query()` this run issues. See module docstring for why
    this must be constructed once by the orchestrator and shared, never
    rebuilt per phase."""

    def __init__(self, max_fetches: int, max_searches: int) -> None:
        self.max_fetches = max_fetches
        self.max_searches = max_searches
        self.fetches_used = 0
        self.searches_used = 0

    def would_exceed(self, tool_name: str) -> bool:
        """Read-only check for `PreToolUse`: would granting one more call
        of `tool_name` exceed its cap? Any tool name other than the two
        counted ones is never capped here."""
        if tool_name == "WebFetch":
            return self.fetches_used >= self.max_fetches
        if tool_name == "WebSearch":
            return self.searches_used >= self.max_searches
        return False

    def record(self, tool_name: str) -> None:
        """Write for `PostToolUse`: a call of `tool_name` actually ran."""
        if tool_name == "WebFetch":
            self.fetches_used += 1
        elif tool_name == "WebSearch":
            self.searches_used += 1

    @property
    def tripped(self) -> bool:
        """§13.1's trigger: true once either cap has been reached. Polled
        by the orchestrator between phases, not by the hooks themselves —
        the hooks only ever answer "would THIS call exceed," never "has the
        run as a whole tripped.\""""
        return self.fetches_used >= self.max_fetches or self.searches_used >= self.max_searches


def make_cost_cap_pre_tool_use_hook(budget: RunBudget):
    """Factory mirroring `hooks/ledger.py`'s `make_ledger_post_tool_use_hook`
    pattern. Denies a `WebFetch`/`WebSearch` call once `budget.would_exceed`
    says granting it would break the cap; every other tool, and every call
    within budget, is allowed through untouched."""

    async def _cost_cap_pre_tool_use_hook(hook_input, tool_use_id, context):  # noqa: ARG001
        tool_name = hook_input.get("tool_name")
        if tool_name in _COUNTED_TOOLS and budget.would_exceed(tool_name):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Run-scoped cost cap reached for {tool_name} "
                        f"({budget.fetches_used}/{budget.max_fetches} fetches, "
                        f"{budget.searches_used}/{budget.max_searches} searches "
                        "used across this run so far, §13) — the orchestrator "
                        "will complete this phase with what it already has "
                        "and stop issuing new research calls (§13.1)."
                    ),
                }
            }
        return {}

    return _cost_cap_pre_tool_use_hook


def make_cost_cap_post_tool_use_hook(budget: RunBudget):
    """The counting half — see module docstring's "counting happens in
    `PostToolUse`" section for why this is a separate hook event from the
    one above rather than the same callback doing both."""

    async def _cost_cap_post_tool_use_hook(hook_input, tool_use_id, context):  # noqa: ARG001
        tool_name = hook_input.get("tool_name")
        if tool_name in _COUNTED_TOOLS:
            budget.record(tool_name)
        return {}

    return _cost_cap_post_tool_use_hook


def cost_cap_pre_tool_use_matchers(budget: RunBudget) -> list[HookMatcher]:
    """Ready-to-use `list[HookMatcher]` for
    `ClaudeAgentOptions(hooks={"PreToolUse": [...]})`."""
    return [
        HookMatcher(matcher="WebFetch|WebSearch", hooks=[make_cost_cap_pre_tool_use_hook(budget)])
    ]


def cost_cap_post_tool_use_matchers(budget: RunBudget) -> list[HookMatcher]:
    """Ready-to-use `list[HookMatcher]` for
    `ClaudeAgentOptions(hooks={"PostToolUse": [...]})` — combine with
    `hooks/ledger.py`'s `ledger_hook_matchers(ledger)` in the same list;
    both target `WebFetch|WebSearch` and are independent, order-agnostic."""
    return [
        HookMatcher(matcher="WebFetch|WebSearch", hooks=[make_cost_cap_post_tool_use_hook(budget)])
    ]
