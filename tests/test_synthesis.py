"""Unit tests for phases/synthesis.py — Phase 6b SYNTHESIS (build order
step 9). `SdkSynthesizer` (the real SDK-calling adapter) is intentionally
not exercised here — see the module docstring; only `_select_top_picks`,
`_apply_top_pick_rationales`, and `run_synthesis`'s pass-through/short-
circuit logic (against a fake `Synthesizer`) are.
"""

import asyncio

from product_scout.phases.synthesis import (
    RawSynthesis,
    Synthesizer,
    _apply_top_pick_rationales,
    _select_top_picks,
    run_synthesis,
)
from tests.conftest import (
    make_intake_answers,
    make_product,
    make_scored,
    make_survey_report,
    make_timing_assessment,
)


def run(coro):
    return asyncio.run(coro)


class FakeSynthesizer:
    """Synthesizer test double: returns a fixed `RawSynthesis`, regardless
    of input, and records every call it actually received (including
    `top_picks`, so tests can assert on ranking-independent selection)."""

    def __init__(self, raw: RawSynthesis):
        self._raw = raw
        self.calls: list[tuple] = []

    async def synthesize(self, products, scores, survey, intake, timing, low_evidence_mode, top_picks):
        self.calls.append((products, scores, survey, intake, timing, low_evidence_mode, top_picks))
        return self._raw


def test_fake_synthesizer_satisfies_synthesizer():
    raw = RawSynthesis(verdict_action="BUY", verdict_reasoning="x", verdict_timing_note=None)
    assert isinstance(FakeSynthesizer(raw), Synthesizer)


# -- _select_top_picks --------------------------------------------------


def test_select_top_picks_sorts_by_score_and_filters_role():
    products = [
        make_product(name="Low", role="recommendation"),
        make_product(name="High", role="recommendation"),
        make_product(name="Baseline", role="baseline_current"),
        make_product(name="AboveBudget", role="reference_above_budget"),
    ]
    scores = [
        make_scored(product_name="Low", score=5.0),
        make_scored(product_name="High", score=9.0),
        make_scored(product_name="Baseline", score=7.8),
        make_scored(product_name="AboveBudget", score=9.9),
    ]
    picks = _select_top_picks(products, scores)
    assert [p.name for p in picks] == ["High", "Low"]


def test_select_top_picks_caps_at_three():
    products = [make_product(name=n, role="recommendation") for n in ("A", "B", "C", "D")]
    scores = [make_scored(product_name=n, score=float(i)) for i, n in enumerate(("A", "B", "C", "D"))]
    picks = _select_top_picks(products, scores)
    assert len(picks) == 3
    assert [p.name for p in picks] == ["D", "C", "B"]


def test_select_top_picks_ignores_verdict_entirely():
    """Invariant 7: the selection function takes no verdict input at all —
    it cannot special-case on Verdict.action because it never sees one."""
    import inspect

    assert "verdict" not in inspect.signature(_select_top_picks).parameters


# -- _apply_top_pick_rationales ------------------------------------------


def test_apply_top_pick_rationales_upgrades_matching_entries_only():
    scores = [
        make_scored(product_name="A", rationale="short judgment"),
        make_scored(product_name="B", rationale="another short judgment"),
    ]
    updated = _apply_top_pick_rationales(scores, {"A": "A full two-sentence prose paragraph."})

    by_name = {s.product_name: s for s in updated}
    assert by_name["A"].rationale == "A full two-sentence prose paragraph."
    assert by_name["B"].rationale == "another short judgment"  # untouched


def test_apply_top_pick_rationales_empty_mapping_is_noop():
    scores = [make_scored(product_name="A")]
    assert _apply_top_pick_rationales(scores, {}) == scores


def test_apply_top_pick_rationales_ignores_unknown_names():
    scores = [make_scored(product_name="A", rationale="short")]
    updated = _apply_top_pick_rationales(scores, {"Nonexistent": "prose"})
    assert updated == scores


# -- run_synthesis --------------------------------------------------------


def test_run_synthesis_short_circuits_to_insufficient_evidence_on_no_products():
    raw = RawSynthesis(verdict_action="BUY", verdict_reasoning="unused", verdict_timing_note=None)
    synthesizer = FakeSynthesizer(raw)

    outcome = run(
        run_synthesis(
            [], [], make_survey_report(), make_intake_answers(), make_timing_assessment(), False, synthesizer
        )
    )

    assert outcome.verdict.action == "INSUFFICIENT_EVIDENCE"
    assert outcome.scores == []
    assert synthesizer.calls == []  # no model call spent on nothing to reason about


def test_run_synthesis_passes_top_picks_and_merges_rationale():
    products = [
        make_product(name="Widget Pro", role="recommendation"),
        make_product(name="Widget Basic", role="recommendation"),
    ]
    scores = [
        make_scored(product_name="Widget Pro", score=8.5, rationale="short"),
        make_scored(product_name="Widget Basic", score=6.0, rationale="short"),
    ]
    raw = RawSynthesis(
        verdict_action="BUY",
        verdict_reasoning="Widget Pro is the clear winner.",
        verdict_timing_note=None,
        top_pick_rationales={
            "Widget Pro": "Widget Pro wins on build quality despite a higher price.",
            "Widget Basic": "Widget Basic is fine for occasional use only.",
        },
    )
    synthesizer = FakeSynthesizer(raw)

    outcome = run(
        run_synthesis(
            products,
            scores,
            make_survey_report(),
            make_intake_answers(),
            make_timing_assessment(),
            False,
            synthesizer,
        )
    )

    assert len(synthesizer.calls) == 1
    passed_top_picks = synthesizer.calls[0][6]
    assert [p.name for p in passed_top_picks] == ["Widget Pro", "Widget Basic"]

    assert outcome.verdict.action == "BUY"
    assert outcome.verdict.reasoning == "Widget Pro is the clear winner."
    by_name = {s.product_name: s for s in outcome.scores}
    assert by_name["Widget Pro"].rationale == "Widget Pro wins on build quality despite a higher price."
    assert outcome.caveats == []


def test_run_synthesis_logs_caveat_for_missing_top_pick_rationale():
    products = [make_product(name="Widget Pro", role="recommendation")]
    scores = [make_scored(product_name="Widget Pro", score=8.0, rationale="short")]
    raw = RawSynthesis(
        verdict_action="BUY", verdict_reasoning="x", verdict_timing_note=None, top_pick_rationales={}
    )
    synthesizer = FakeSynthesizer(raw)

    outcome = run(
        run_synthesis(
            products,
            scores,
            make_survey_report(),
            make_intake_answers(),
            make_timing_assessment(),
            False,
            synthesizer,
        )
    )

    assert any("Widget Pro" in c for c in outcome.caveats)
    # Falls back to the Phase 6a rationale rather than dropping it.
    assert outcome.scores[0].rationale == "short"
