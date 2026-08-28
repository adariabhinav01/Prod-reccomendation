"""Unit tests for phases/discovery.py — Phase 2 DISCOVERY (build order
step 5). `SdkDiscoverer` (the real SDK-calling adapter) is intentionally not
exercised here — see the module docstring; only `run_discovery`'s
merge/dedup/cap orchestration (against a fake) and `_parse_candidate_list`
(pure, plain string fixtures) are tested.
"""

import asyncio

from product_scout.phases.discovery import (
    MAX_DISCOVERY_CANDIDATES,
    Discoverer,
    _parse_candidate_list,
    run_discovery,
)
from tests.conftest import make_intake_answers as make_intake


def run(coro):
    return asyncio.run(coro)


class FakeDiscoverer:
    """Discoverer test double: one canned candidate list, regardless of
    product_type. Mirrors test_probe.py's FakeProber style."""

    def __init__(self, candidates: list[str]):
        self._candidates = candidates
        self.calls: list[str] = []

    async def discover(self, product_type: str) -> list[str]:
        self.calls.append(product_type)
        return self._candidates


# -- protocol conformance ------------------------------------------------------


def test_fake_discoverer_satisfies_discoverer():
    assert isinstance(FakeDiscoverer([]), Discoverer)


# -- run_discovery: merge / dedup / cap ----------------------------------------


def test_merges_named_candidates_ahead_of_discovered():
    intake = make_intake(candidates_under_consideration=["X"])
    discoverer = FakeDiscoverer(["Y", "Z"])
    result = run(run_discovery("widgets", intake, discoverer))
    assert result == ["X", "Y", "Z"]


def test_dedupes_case_insensitive_exact_match_first_casing_wins():
    intake = make_intake(candidates_under_consideration=["Acme Pro"])
    discoverer = FakeDiscoverer(["acme pro", "Other"])
    result = run(run_discovery("widgets", intake, discoverer))
    assert result == ["Acme Pro", "Other"]


def test_caps_at_max_discovery_candidates_named_never_dropped():
    intake = make_intake(candidates_under_consideration=["Named A", "Named B"])
    discovered = [f"Discovered {i}" for i in range(10)]
    discoverer = FakeDiscoverer(discovered)
    result = run(run_discovery("widgets", intake, discoverer))
    assert len(result) == MAX_DISCOVERY_CANDIDATES
    assert "Named A" in result
    assert "Named B" in result


def test_empty_named_and_discovered_returns_empty():
    intake = make_intake(candidates_under_consideration=[])
    discoverer = FakeDiscoverer([])
    result = run(run_discovery("widgets", intake, discoverer))
    assert result == []


def test_blank_named_candidates_are_skipped():
    intake = make_intake(candidates_under_consideration=["  ", ""])
    discoverer = FakeDiscoverer(["Real Candidate"])
    result = run(run_discovery("widgets", intake, discoverer))
    assert result == ["Real Candidate"]


# -- _parse_candidate_list ------------------------------------------------------


def test_parses_clean_json_array():
    assert _parse_candidate_list('["Acme Pro", "Zenith 4"]') == [
        "Acme Pro",
        "Zenith 4",
    ]


def test_parses_json_array_wrapped_in_fence_and_prose():
    text = (
        "Here are the candidates I found:\n"
        "```json\n"
        '["Acme Pro", "Zenith 4"]\n'
        "```\n"
        "Let me know if you need more."
    )
    assert _parse_candidate_list(text) == ["Acme Pro", "Zenith 4"]


def test_malformed_json_returns_empty():
    assert _parse_candidate_list("[Acme Pro, Zenith 4]") == []


def test_non_array_json_returns_empty():
    assert _parse_candidate_list('{"candidates": ["Acme Pro"]}') == []


def test_empty_string_returns_empty():
    assert _parse_candidate_list("") == []
    assert _parse_candidate_list("   ") == []


def test_array_of_non_strings_returns_empty():
    assert _parse_candidate_list("[1, 2, 3]") == []


def test_array_with_blank_strings_filtered():
    assert _parse_candidate_list('["Acme Pro", "  ", ""]') == ["Acme Pro"]
