"""Unit tests for phases/probe.py — Phase 1 PROBE + the §8.1/§8.1a coverage
gate (build order step 4)."""

import asyncio

import pytest

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.models import CoverageReport
from product_scout.phases.probe import (
    KEEP_SCOPE_OPTION,
    MAX_BROADER_CATEGORIES_IN_INTERRUPT,
    STOP_OPTION,
    CoverageProber,
    run_probe,
)
from tests.conftest import make_broader_category, make_coverage_report


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """A CLIQuestionPort fed from a fixed script instead of a real terminal.

    Mirrors test_intake.py's / test_cli_port.py's helper.
    """
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


class FakeProber:
    """CoverageProber test double: one canned CoverageReport per category.

    `calls` records every product_type probed, in order, so tests can
    assert exactly how many times (and on what) the probe actually ran —
    the key thing to pin for the "never interrupt twice" edge case.
    """

    def __init__(self, responses: dict[str, CoverageReport]):
        self._responses = responses
        self.calls: list[str] = []

    async def probe(self, product_type: str) -> CoverageReport:
        self.calls.append(product_type)
        return self._responses[product_type]


def _menu_options(printed: list[str]) -> list[str]:
    """Extract just the numbered menu lines' option text from CLI output."""
    options = []
    for line in printed:
        stripped = line.strip()
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] == ". ":
            options.append(stripped[3:])
    return options


# -- protocol conformance ----------------------------------------------------


def test_fake_prober_satisfies_coverage_prober():
    prober = FakeProber({"widgets": make_coverage_report()})
    assert isinstance(prober, CoverageProber)


# -- rich / moderate: proceed without interrupting ---------------------------


def test_rich_coverage_proceeds_without_interrupt():
    coverage = make_coverage_report(coverage="rich")
    prober = FakeProber({"widgets": coverage})
    port, _ = make_port([])  # would raise StopIteration if ask() were called

    outcome = run(run_probe("widgets", prober, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.product_type == "widgets"
    assert outcome.original_product_type is None
    assert outcome.category_broadening_offered is False
    assert outcome.caveats == []
    assert prober.calls == ["widgets"]


def test_moderate_coverage_proceeds_without_interrupt():
    coverage = make_coverage_report(
        coverage="moderate", estimated_product_count=6, independent_review_sources_found=3
    )
    prober = FakeProber({"widgets": coverage})
    port, _ = make_port([])

    outcome = run(run_probe("widgets", prober, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.category_broadening_offered is False


# -- sparse / barren: the one interrupt --------------------------------------


def test_sparse_coverage_interrupts_and_user_can_stop():
    coverage = make_coverage_report(
        coverage="sparse",
        estimated_product_count=3,
        independent_review_sources_found=1,
        has_methodology_backed_testing=False,
        suggested_broader_categories=[make_broader_category(name="broader widgets")],
    )
    prober = FakeProber({"widgets": coverage})
    port, printed = make_port([STOP_OPTION])

    outcome = run(run_probe("widgets", prober, port))

    assert outcome.proceed is False
    assert outcome.low_evidence_mode is False
    assert outcome.category_broadening_offered is True
    assert outcome.original_product_type is None
    assert prober.calls == ["widgets"]  # no re-probe after stopping
    assert any("Coverage check:" in line for line in printed)


def test_sparse_coverage_user_keeps_original_scope():
    coverage = make_coverage_report(
        coverage="sparse",
        estimated_product_count=3,
        independent_review_sources_found=1,
        suggested_broader_categories=[],
    )
    prober = FakeProber({"widgets": coverage})
    port, _ = make_port([KEEP_SCOPE_OPTION])

    outcome = run(run_probe("widgets", prober, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert outcome.product_type == "widgets"
    assert outcome.original_product_type is None  # never broadened
    assert outcome.category_broadening_offered is True
    assert any("kept the original scope" in c for c in outcome.caveats)
    assert prober.calls == ["widgets"]


def test_zero_suggested_categories_still_offers_exactly_keep_and_stop():
    coverage = make_coverage_report(coverage="barren", suggested_broader_categories=[])
    prober = FakeProber({"widgets": coverage})
    port, printed = make_port([STOP_OPTION])

    run(run_probe("widgets", prober, port))

    assert _menu_options(printed) == [KEEP_SCOPE_OPTION, STOP_OPTION]


def test_barren_coverage_user_broadens_to_a_rich_category():
    barren = make_coverage_report(
        coverage="barren",
        estimated_product_count=1,
        independent_review_sources_found=0,
        suggested_broader_categories=[
            make_broader_category(name="broader widgets", estimated_coverage="rich")
        ],
    )
    rich = make_coverage_report(coverage="rich")
    prober = FakeProber({"vintage widgets": barren, "broader widgets": rich})
    port, _ = make_port(['Research "broader widgets" instead (rich coverage)'])

    outcome = run(run_probe("vintage widgets", prober, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.product_type == "broader widgets"
    assert outcome.original_product_type == "vintage widgets"
    assert outcome.category_broadening_offered is True
    assert prober.calls == ["vintage widgets", "broader widgets"]


# -- the key edge case (§8.1a): broadening into ANOTHER sparse category -----


def test_broadening_into_another_sparse_category_does_not_reinterrupt():
    original = make_coverage_report(
        coverage="sparse",
        estimated_product_count=3,
        independent_review_sources_found=1,
        suggested_broader_categories=[
            make_broader_category(name="broader widgets", estimated_coverage="moderate")
        ],
    )
    also_sparse = make_coverage_report(
        coverage="sparse",
        estimated_product_count=4,
        independent_review_sources_found=1,
        notes="Still thin even after broadening.",
    )
    prober = FakeProber({"widgets": original, "broader widgets": also_sparse})
    # Only ONE scripted answer: if run_probe asked a second time, make_port's
    # StopIteration would fail the test outright.
    port, printed = make_port(['Research "broader widgets" instead (moderate coverage)'])

    outcome = run(run_probe("widgets", prober, port))

    assert prober.calls == ["widgets", "broader widgets"]  # re-probed, cheap
    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True  # degraded, not asked again
    assert outcome.product_type == "broader widgets"
    assert outcome.original_product_type == "widgets"
    assert outcome.category_broadening_offered is True
    assert any(
        "also probed as sparse" in c and "without asking again" in c
        for c in outcome.caveats
    )
    # Only one "Coverage check:" interrupt was ever printed.
    assert sum("Coverage check:" in line for line in printed) == 1


def test_broadening_into_a_barren_category_also_does_not_reinterrupt():
    sparse = make_coverage_report(
        coverage="sparse",
        suggested_broader_categories=[make_broader_category(name="broader widgets")],
    )
    barren = make_coverage_report(coverage="barren", independent_review_sources_found=0)
    prober = FakeProber({"widgets": sparse, "broader widgets": barren})
    port, _ = make_port(['Research "broader widgets" instead (rich coverage)'])

    outcome = run(run_probe("widgets", prober, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert prober.calls == ["widgets", "broader widgets"]


# -- option-count cap (§8.1a says "all at once"; QuestionPort caps at 4) ----


def test_more_than_two_broader_categories_are_capped_and_logged():
    coverage = make_coverage_report(
        coverage="sparse",
        suggested_broader_categories=[
            make_broader_category(name="cat sparse", estimated_coverage="sparse"),
            make_broader_category(name="cat rich", estimated_coverage="rich"),
            make_broader_category(name="cat moderate", estimated_coverage="moderate"),
        ],
    )
    prober = FakeProber({"widgets": coverage})
    port, printed = make_port([STOP_OPTION])

    outcome = run(run_probe("widgets", prober, port))

    options = _menu_options(printed)
    assert len(options) == MAX_BROADER_CATEGORIES_IN_INTERRUPT + 2  # + keep + stop
    # Best-covered suggestions shown first: rich, then moderate — sparse dropped.
    assert options[0] == 'Research "cat rich" instead (rich coverage)'
    assert options[1] == 'Research "cat moderate" instead (moderate coverage)'
    assert any('"cat sparse"' in c for c in outcome.caveats)


def test_duplicate_broader_category_names_are_deduped():
    coverage = make_coverage_report(
        coverage="sparse",
        suggested_broader_categories=[
            make_broader_category(name="broader widgets", estimated_coverage="moderate"),
            make_broader_category(name="Broader Widgets", estimated_coverage="rich"),
        ],
    )
    prober = FakeProber({"widgets": coverage})
    port, printed = make_port([STOP_OPTION])

    run(run_probe("widgets", prober, port))

    options = _menu_options(printed)
    # First occurrence wins the dedup (moderate), so its annotation survives.
    assert options.count('Research "broader widgets" instead (moderate coverage)') == 1
    assert not any("rich coverage" in opt for opt in options)


# -- QuestionPort.ask's 2-4 option contract is never violated ---------------


def test_interrupt_option_count_always_within_ask_contract():
    for n in (0, 1, 2, 3, 5):
        cats = [
            make_broader_category(name=f"cat {i}", estimated_coverage="moderate")
            for i in range(n)
        ]
        coverage = make_coverage_report(coverage="sparse", suggested_broader_categories=cats)
        prober = FakeProber({"widgets": coverage})
        port, printed = make_port([STOP_OPTION])
        run(run_probe("widgets", prober, port))
        assert 2 <= len(_menu_options(printed)) <= 4
