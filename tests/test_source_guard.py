"""Unit tests for hooks/source_guard.py — the §13 source guard hook (build
order step 13). `is_community_domain` is tested directly; the hook callable
is invoked directly with hand-built `hook_input` dicts, exactly the way
tests/test_ledger.py exercises `FetchLedger` and tests/test_budget.py
exercises the cost-cap hooks.
"""

import asyncio

from product_scout.hooks.source_guard import (
    is_community_domain,
    make_source_guard_pre_tool_use_hook,
    source_guard_hook_matchers,
)


def run(coro):
    return asyncio.run(coro)


def _denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# -- is_community_domain ------------------------------------------------------


def test_reddit_is_a_community_domain():
    assert is_community_domain("https://www.reddit.com/r/desks/comments/1") is True


def test_old_subdomain_of_a_marker_still_matches():
    assert is_community_domain("https://old.reddit.com/r/desks") is True


def test_youtube_and_short_link_are_community_domains():
    assert is_community_domain("https://www.youtube.com/watch?v=x") is True
    assert is_community_domain("https://youtu.be/abc123") is True


def test_generic_forum_substring_matches_even_off_the_fixed_list():
    """Catches vendor-agnostic forum software on an otherwise-unlisted
    domain — see module docstring."""
    assert is_community_domain("https://forum.somebrand.com/thread/1") is True
    assert is_community_domain("https://community.example-forums.net/t/1") is True


def test_manufacturer_domain_is_not_community():
    assert is_community_domain("https://www.acme.com/products/widget-pro") is False


def test_lookalike_domain_is_not_falsely_matched():
    """'notreddit.com' must not match the 'reddit.com' marker — only an
    exact host or a genuine subdomain should."""
    assert is_community_domain("https://www.notreddit.com/page") is False


def test_empty_or_unparseable_url_is_never_community():
    assert is_community_domain("") is False
    assert is_community_domain("not a url") is False


# -- PreToolUse hook: standard mode denies, low-evidence mode never does ----


def test_standard_mode_denies_a_community_fetch():
    hook = make_source_guard_pre_tool_use_hook(low_evidence_mode=False)
    result = run(
        hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://www.reddit.com/r/desks"}},
            "id",
            {},
        )
    )
    assert _denied(result)
    assert "low-evidence" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_standard_mode_allows_a_manufacturer_fetch():
    hook = make_source_guard_pre_tool_use_hook(low_evidence_mode=False)
    result = run(
        hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://www.acme.com/widget-pro"}},
            "id",
            {},
        )
    )
    assert result == {}


def test_low_evidence_mode_allows_a_community_fetch():
    """§13: 'Must loosen in low-evidence mode, or it blocks the community
    sources that are the only evidence available.'"""
    hook = make_source_guard_pre_tool_use_hook(low_evidence_mode=True)
    result = run(
        hook(
            {"tool_name": "WebFetch", "tool_input": {"url": "https://www.reddit.com/r/desks"}},
            "id",
            {},
        )
    )
    assert result == {}


def test_hook_never_matches_web_search():
    """§13's bullet names WebFetch specifically — blocking WebSearch too
    would prevent even discovering that a community source exists."""
    hook = make_source_guard_pre_tool_use_hook(low_evidence_mode=False)
    result = run(
        hook(
            {"tool_name": "WebSearch", "tool_input": {"query": "reddit desk reviews"}},
            "id",
            {},
        )
    )
    assert result == {}


def test_hook_tolerates_missing_url():
    hook = make_source_guard_pre_tool_use_hook(low_evidence_mode=False)
    result = run(hook({"tool_name": "WebFetch", "tool_input": {}}, "id", {}))
    assert result == {}


# -- matcher factory -----------------------------------------------------------


def test_matchers_target_web_fetch_only():
    matchers = source_guard_hook_matchers(low_evidence_mode=False)
    assert len(matchers) == 1
    assert matchers[0].matcher == "WebFetch"
