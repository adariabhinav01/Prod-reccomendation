"""The §13 `PreToolUse` source guard hook (spec docs/handoff.md §13, build
order step 13).

§13, quoted: "**`PreToolUse` on `WebFetch` — source guard.** Check the
domain before spending a fetch. **Must loosen in low-evidence mode**, or it
blocks the community sources that are the only evidence available."

### Why this needs its own domain list rather than reusing something

Nothing else in this codebase classifies a URL by domain before fetching
it. `RunRecord.trusted_sources`/`SourcedValue.from_trusted_source` are an
unrelated, unwired §15 seam (always `[]`/`False` in this version) — trust,
not community-vs-not. The `research-protocol` skill's 5-tier source system
(Tier 5 = community: forums, subreddits, video reviews, owner reviews) is
judged by the MODEL, post-hoc, once it has actually read a page — a
`PreToolUse` hook fires BEFORE any content exists to judge, so it can only
ever see the URL/domain itself. `COMMUNITY_DOMAIN_MARKERS` below is
therefore a new, small, independent, pattern-based approximation of that
same Tier 5 boundary — good enough to check a domain before spending a
fetch on it, not a rederivation of the skill's own (finer-grained,
content-aware) tiering.

### Only `WebFetch` is guarded, never `WebSearch`

§13's own bullet names `WebFetch` specifically. Blocking `WebSearch` too
would prevent even *discovering* that a community source exists — exactly
what low-evidence mode needs to be able to admit once loosened. Denying the
fetch (not the search) is what keeps low-evidence mode's relaxation
meaningful: the model can still see a community result in its search
results and choose to fetch it once the guard allows that.

### Standard mode is `low_evidence_mode=False`, always, for SURVEY's own call

`SdkSurveyor.survey()` has no `low_evidence_mode` parameter — SURVEY is the
phase that DECIDES it (§8.1a's broadening interrupt), so it cannot know the
value of something it hasn't produced yet. `orchestrator.py` wires this
hook with `low_evidence_mode=False` for SURVEY's own call, which is
correct, not an oversight: SURVEY's research genuinely happens before the
mode is known, so it always runs under the strict/standard guard.
"""

from __future__ import annotations

from urllib.parse import urlparse

from claude_agent_sdk import HookMatcher

# Pattern-based, not exhaustive — see module docstring. Matched against the
# URL's hostname, exact or as a subdomain (`old.reddit.com` counts,
# `notreddit.com` does not).
COMMUNITY_DOMAIN_MARKERS: frozenset[str] = frozenset(
    {
        "reddit.com",
        "quora.com",
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "instagram.com",
    }
)

# Catches vendor-agnostic forum software on an otherwise-unlisted domain
# (e.g. a Discourse-hosted `community.somebrand.com` or
# `forum.example.com`) that a fixed domain list would otherwise miss —
# mirrors §14's own wording ("forums, subreddits...") naming the pattern,
# not just specific sites.
_FORUM_MARKER = "forum"


def is_community_domain(url: str) -> bool:
    """True when `url`'s host matches a known community-source domain or
    contains "forum" as a host substring. Empty/unparseable input is never
    treated as community — nothing to block a fetch over."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if _FORUM_MARKER in host:
        return True
    return any(
        host == marker or host.endswith(f".{marker}") for marker in COMMUNITY_DOMAIN_MARKERS
    )


def make_source_guard_pre_tool_use_hook(low_evidence_mode: bool):
    """Factory mirroring `hooks/ledger.py`'s factory pattern.
    `low_evidence_mode=True` makes this hook an unconditional no-op — every
    `WebFetch` passes through untouched, per §13's "must loosen" rule."""

    async def _source_guard_pre_tool_use_hook(hook_input, tool_use_id, context):  # noqa: ARG001
        if low_evidence_mode:
            return {}
        if hook_input.get("tool_name") != "WebFetch":
            return {}
        url = (hook_input.get("tool_input") or {}).get("url") or ""
        if not is_community_domain(url):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{url!r} is a community-type source (§14 Tier 5) — not "
                    "admissible as the sole basis for a spec value in "
                    "standard mode. Use a search result snippet for "
                    "reliability/ownership sentiment instead of fetching "
                    "this page directly; fetching community sources "
                    "outright is only permitted in low-evidence mode."
                ),
            }
        }

    return _source_guard_pre_tool_use_hook


def source_guard_hook_matchers(low_evidence_mode: bool) -> list[HookMatcher]:
    """Ready-to-use `list[HookMatcher]` for
    `ClaudeAgentOptions(hooks={"PreToolUse": [...]})` — combine with
    `hooks/budget.py`'s `cost_cap_pre_tool_use_matchers(budget)` in the
    same list; both are independent, order-agnostic `PreToolUse` hooks."""
    return [
        HookMatcher(
            matcher="WebFetch", hooks=[make_source_guard_pre_tool_use_hook(low_evidence_mode)]
        )
    ]
