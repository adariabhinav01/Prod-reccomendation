"""Unit tests for phases/timing.py — Phase 4 TIMING (build order step 10).
`SdkTimingResearcher` (the real SDK-calling adapter) is intentionally not
exercised here — see the module docstring; only `_enforce_basis_discipline`
and `run_timing`'s pass-through logic (against a fake) are.
"""

import asyncio

from product_scout.hooks.ledger import FetchLedger
from product_scout.models import TimingAssessment
from product_scout.phases.timing import TimingResearcher, _enforce_basis_discipline, run_timing
from tests.conftest import make_timing_assessment


def run(coro):
    return asyncio.run(coro)


class FakeTimingResearcher:
    """TimingResearcher test double: returns a fixed `TimingAssessment`,
    regardless of input, and records every call it actually received."""

    def __init__(self, assessment: TimingAssessment):
        self._assessment = assessment
        self.calls: list[tuple[str, list[str], FetchLedger, bool]] = []

    async def research(self, product_type, product_names, ledger, low_evidence_mode) -> TimingAssessment:
        self.calls.append((product_type, product_names, ledger, low_evidence_mode))
        return self._assessment


def test_fake_timing_researcher_satisfies_timing_researcher():
    assert isinstance(FakeTimingResearcher(make_timing_assessment()), TimingResearcher)


# -- _enforce_basis_discipline --------------------------------------------


def test_basis_discipline_passes_through_signal_with_basis():
    assessment = make_timing_assessment(
        signal_found=True,
        successor_expected="a Q4 refresh",
        basis_notes=["manufacturer announced a Q4 launch"],
        recommends_wait=True,
    )
    result, caveats = _enforce_basis_discipline(assessment)
    assert result == assessment
    assert caveats == []


def test_basis_discipline_downgrades_unbacked_signal():
    assessment = make_timing_assessment(
        signal_found=True,
        successor_expected="a new model, allegedly",
        price_trend="prices allegedly dropping",
        technology_transition="a new standard, allegedly",
        basis_notes=[],
        recommends_wait=True,
    )
    result, caveats = _enforce_basis_discipline(assessment)

    assert result.signal_found is False
    assert result.successor_expected is None
    assert result.price_trend is None
    assert result.technology_transition is None
    assert result.recommends_wait is False
    assert len(caveats) == 1
    assert "§6.5" in caveats[0]


def test_basis_discipline_leaves_no_signal_found_alone():
    assessment = make_timing_assessment(signal_found=False, basis_notes=[], recommends_wait=False)
    result, caveats = _enforce_basis_discipline(assessment)
    assert result == assessment
    assert caveats == []


# -- run_timing ------------------------------------------------------------


def test_run_timing_passes_through_and_applies_discipline():
    clean = make_timing_assessment(
        signal_found=True, basis_notes=["historically refreshed each September"]
    )
    researcher = FakeTimingResearcher(clean)
    ledger = FetchLedger()

    outcome = run(run_timing("standing desks", ["Widget Pro"], ledger, False, researcher))

    assert outcome.timing == clean
    assert outcome.caveats == []
    assert researcher.calls == [("standing desks", ["Widget Pro"], ledger, False)]


def test_run_timing_downgrades_via_researcher_output():
    unbacked = make_timing_assessment(signal_found=True, basis_notes=[], recommends_wait=True)
    researcher = FakeTimingResearcher(unbacked)

    outcome = run(run_timing("standing desks", [], FetchLedger(), False, researcher))

    assert outcome.timing.signal_found is False
    assert len(outcome.caveats) == 1


def test_run_timing_threads_low_evidence_mode_to_researcher():
    researcher = FakeTimingResearcher(make_timing_assessment())
    run(run_timing("standing desks", [], FetchLedger(), True, researcher))
    assert researcher.calls[0][3] is True
