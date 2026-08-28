"""Unit tests for phases/scoring.py — Phase 6a SCORING (build order step 9;
`_demote_for_row_cap` added build order step 10). `SdkScorer` (the real
SDK-calling adapter) is intentionally not exercised here — see the module
docstring; only the pure §5.1/§5.2 issue-detection functions, the
flip-point interpolation, the row-cap demotion, and `run_scoring`'s
re-prompt state machine (against a fake `Scorer`) are.
"""

import asyncio

from product_scout.phases.scoring import (
    ROW_CAP,
    SCORING_REPROMPT_MAX,
    RawProductScore,
    RawScoring,
    Scorer,
    _above_budget_standout_issue,
    _apply_raw_scores,
    _archetype_diversity_issue,
    _compute_flip_point,
    _demote_for_row_cap,
    _find_issues,
    _is_round_number,
    _round_number_issue,
    _top_pick_score,
    run_scoring,
)
from tests.conftest import (
    make_evidence_profile,
    make_intake_answers,
    make_pricing_model,
    make_product,
    make_scored,
    make_survey_report,
    make_topic_answer,
)


def run(coro):
    return asyncio.run(coro)


def _raw_score(**overrides) -> RawProductScore:
    defaults = dict(
        product_name="Widget Pro",
        score=6.0,
        rationale="Solid mid-tier pick.",
        role="recommendation",
        strength_archetype="value",
        score_at_minus_10pct=6.5,
        score_at_minus_20pct=7.0,
        score_at_minus_30pct=7.5,
    )
    defaults.update(overrides)
    return RawProductScore(**defaults)


class FakeScorer:
    """Scorer test double: returns each `RawScoring` in `responses`, in
    order (repeating the last one past the end), and records every call
    it actually received, including `feedback`."""

    def __init__(self, responses: list[RawScoring]):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    async def propose_scores(self, products, survey, intake, topics, low_evidence_mode, feedback):
        self.calls.append((products, survey, intake, topics, low_evidence_mode, feedback))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


def test_fake_scorer_satisfies_scorer():
    assert isinstance(FakeScorer([]), Scorer)


# -- _is_round_number / _round_number_issue ----------------------------------


def test_is_round_number():
    assert _is_round_number(7.0)
    assert _is_round_number(7.5)
    assert not _is_round_number(7.3)
    assert not _is_round_number(6.85)


def test_round_number_issue_fires_over_half():
    raw = [_raw_score(score=s) for s in (6.0, 7.0, 7.5, 6.3)]  # 3 of 4 round
    issue = _round_number_issue(raw)
    assert issue is not None
    assert "3 of 4" in issue


def test_round_number_issue_silent_at_half_or_under():
    raw = [_raw_score(score=s) for s in (6.0, 7.0, 6.3, 7.4)]  # 2 of 4 round
    assert _round_number_issue(raw) is None


def test_round_number_issue_silent_on_empty():
    assert _round_number_issue([]) is None


# -- _archetype_diversity_issue -----------------------------------------------


def test_archetype_diversity_ok_when_fewer_than_min_qualify():
    products_by_name = {
        "A": make_product(name="A", in_budget=True),
        "B": make_product(name="B", in_budget=True),
    }
    raw = [
        _raw_score(product_name="A", role="recommendation", strength_archetype="value"),
        _raw_score(product_name="B", role="recommendation", strength_archetype="value"),
    ]
    # Only 2 qualifying in-budget recommendation rows exist — the "when 3
    # qualifying products exist" clause never binds.
    assert _archetype_diversity_issue(products_by_name, raw) is None


def test_archetype_diversity_flags_undifferentiated_set():
    products_by_name = {
        name: make_product(name=name, in_budget=True) for name in ("A", "B", "C")
    }
    raw = [
        _raw_score(product_name=n, role="recommendation", strength_archetype="value")
        for n in ("A", "B", "C")
    ]
    issue = _archetype_diversity_issue(products_by_name, raw)
    assert issue is not None
    assert "1 distinct archetype" in issue


def test_archetype_diversity_ok_when_distinct():
    products_by_name = {
        name: make_product(name=name, in_budget=True) for name in ("A", "B", "C")
    }
    raw = [
        _raw_score(product_name="A", role="recommendation", strength_archetype="value"),
        _raw_score(product_name="B", role="recommendation", strength_archetype="durability"),
        _raw_score(product_name="C", role="recommendation", strength_archetype="aesthetic"),
    ]
    assert _archetype_diversity_issue(products_by_name, raw) is None


def test_archetype_diversity_ignores_out_of_budget_rows():
    products_by_name = {
        "A": make_product(name="A", in_budget=True),
        "B": make_product(name="B", in_budget=True),
        "C": make_product(name="C", in_budget=False),
    }
    raw = [
        _raw_score(product_name="A", role="recommendation", strength_archetype="value"),
        _raw_score(product_name="B", role="recommendation", strength_archetype="value"),
        _raw_score(product_name="C", role="recommendation", strength_archetype="performance"),
    ]
    # Only A and B are in-budget — 2 qualifying rows, below the floor of 3.
    assert _archetype_diversity_issue(products_by_name, raw) is None


# -- _above_budget_standout_issue ---------------------------------------------


def test_above_budget_standout_ok_when_none_exist():
    products_by_name = {"A": make_product(name="A", in_budget=True)}
    raw = [_raw_score(product_name="A", role="recommendation")]
    assert _above_budget_standout_issue(products_by_name, raw) is None


def test_above_budget_standout_flags_all_demoted():
    products_by_name = {
        "A": make_product(name="A", in_budget=True),
        "B": make_product(name="B", in_budget=False),
    }
    raw = [
        _raw_score(product_name="A", role="recommendation"),
        _raw_score(product_name="B", role="reference_above_budget"),
    ]
    issue = _above_budget_standout_issue(products_by_name, raw)
    assert issue is not None
    assert "B" in issue


def test_above_budget_standout_ok_when_one_kept():
    products_by_name = {
        "A": make_product(name="A", in_budget=True),
        "B": make_product(name="B", in_budget=False),
    }
    raw = [
        _raw_score(product_name="A", role="recommendation"),
        _raw_score(product_name="B", role="recommendation"),
    ]
    assert _above_budget_standout_issue(products_by_name, raw) is None


# -- _find_issues --------------------------------------------------------


def test_find_issues_empty_when_all_constraints_met():
    products = [make_product(name="A", in_budget=True)]
    raw = RawScoring(scores=[_raw_score(product_name="A", score=6.3)])
    assert _find_issues(products, raw) == []


# -- flip point interpolation --------------------------------------------


def _eligible_product(**overrides) -> object:
    defaults = dict(name="Widget Pro", pricing=make_pricing_model())
    defaults.update(overrides)
    return make_product(**defaults)


def test_flip_point_interpolates_within_range():
    product = _eligible_product()
    raw = _raw_score(
        score=6.0, score_at_minus_10pct=6.5, score_at_minus_20pct=7.0, score_at_minus_30pct=7.5
    )
    amount, note = _compute_flip_point(product, raw, top_score=7.2, low_evidence_mode=False)
    assert note is None
    # 199 * (1 - 0.24) = 151.24 — see module docstring's worked example.
    assert amount == 151.24


def test_flip_point_out_of_sampled_range():
    product = _eligible_product()
    raw = _raw_score(
        score=6.0, score_at_minus_10pct=6.2, score_at_minus_20pct=6.4, score_at_minus_30pct=6.6
    )
    amount, note = _compute_flip_point(product, raw, top_score=8.0, low_evidence_mode=False)
    assert amount is None
    assert "30%" in note


def test_flip_point_already_leads_needs_no_discount():
    product = _eligible_product()
    raw = _raw_score(score=7.5)
    amount, note = _compute_flip_point(product, raw, top_score=7.0, low_evidence_mode=False)
    assert amount is None
    assert "already" in note.lower()


def test_flip_point_suppressed_by_low_evidence_mode_regardless_of_eligibility():
    """§5.2: 'Low-evidence mode suppresses flip points entirely, independent
    of the threshold' — checked even against an otherwise fully-eligible,
    in-range product, since this gate must win regardless of what the
    eligibility predicate or the sampled scores say."""
    product = _eligible_product()
    raw = _raw_score(
        score=6.0, score_at_minus_10pct=6.5, score_at_minus_20pct=7.0, score_at_minus_30pct=7.5
    )
    amount, note = _compute_flip_point(product, raw, top_score=7.2, low_evidence_mode=True)
    assert amount is None
    assert "too thin" in note


def test_flip_point_out_of_range_uses_upfront_wording_for_hybrid_pricing():
    """§5.2: the out-of-range bound must read 'a cut to the upfront price,'
    never 'a discount' — the latter would imply the TCO moved by 30% when
    only the upfront component was sampled."""
    product = make_product(
        name="Widget Pro",
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=100.0,
            recurring_amount=5.0,
            recurring_period="monthly",
            total_cost_1yr=160.0,  # 100/160 = 0.625 >= 0.25, clears the gate
        ),
    )
    raw = _raw_score(
        score=6.0, score_at_minus_10pct=6.2, score_at_minus_20pct=6.4, score_at_minus_30pct=6.6
    )
    amount, note = _compute_flip_point(product, raw, top_score=8.0, low_evidence_mode=False)
    assert amount is None
    assert "upfront price" in note
    assert "discount" not in note


def test_flip_point_suppressed_for_thin_evidence():
    product = make_product(
        name="Widget Pro",
        pricing=make_pricing_model(),
        evidence=make_evidence_profile(independent_review_count=1, confidence=0.5),
    )
    raw = _raw_score(score=6.0)
    amount, note = _compute_flip_point(product, raw, top_score=8.0, low_evidence_mode=False)
    assert amount is None
    assert "too thin" in note


def test_flip_point_suppressed_by_model_type():
    product = make_product(
        name="Widget Pro",
        pricing=make_pricing_model(model_type="subscription_only", upfront_amount=None),
    )
    raw = _raw_score(score=6.0)
    amount, note = _compute_flip_point(product, raw, top_score=8.0, low_evidence_mode=False)
    assert amount is None
    assert "subscription" in note.lower()


def test_flip_point_suppressed_by_upfront_share_gate():
    product = make_product(
        name="Widget Pro",
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=20.0,
            recurring_amount=10.0,
            recurring_period="monthly",
            total_cost_1yr=140.0,  # 20/140 ≈ 0.14 < 0.25
        ),
    )
    raw = _raw_score(score=6.0)
    amount, note = _compute_flip_point(product, raw, top_score=8.0, low_evidence_mode=False)
    assert amount is None
    assert "upfront price" in note


def test_flip_point_computed_for_hybrid_above_share_gate():
    product = make_product(
        name="Widget Pro",
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=100.0,
            recurring_amount=5.0,
            recurring_period="monthly",
            total_cost_1yr=160.0,  # 100/160 = 0.625 >= 0.25
        ),
    )
    raw = _raw_score(
        score=6.0, score_at_minus_10pct=6.5, score_at_minus_20pct=7.0, score_at_minus_30pct=7.5
    )
    amount, note = _compute_flip_point(product, raw, top_score=7.2, low_evidence_mode=False)
    assert note is None
    assert amount == 76.0  # 100 * (1 - 0.24)


def test_top_pick_score_prefers_recommendation_role():
    products_by_name = {
        "A": make_product(name="A", role="recommendation"),
        "B": make_product(name="B", role="reference_above_budget"),
    }
    raw = [
        _raw_score(product_name="A", score=7.0),
        _raw_score(product_name="B", score=9.9),  # higher, but not a recommendation
    ]
    assert _top_pick_score(products_by_name, raw) == 7.0


# -- _apply_raw_scores ----------------------------------------------------


def test_apply_raw_scores_updates_role_and_archetype_without_mutating_input():
    original = make_product(name="A", role="recommendation", strength_archetype="value")
    raw = RawScoring(
        scores=[_raw_score(product_name="A", role="reference_above_budget", strength_archetype="performance")]
    )
    updated = _apply_raw_scores([original], raw)

    assert original.role == "recommendation"  # input untouched
    assert original.strength_archetype == "value"
    assert updated[0].role == "reference_above_budget"
    assert updated[0].strength_archetype == "performance"


def test_apply_raw_scores_leaves_unscored_products_untouched():
    original = make_product(name="A", role="recommendation")
    updated = _apply_raw_scores([original], RawScoring(scores=[]))
    assert updated == [original]


# -- _demote_for_row_cap (§5.1, build order step 10) -------------------------


def _row(name: str, *, role="recommendation", generation="current") -> object:
    return make_product(name=name, role=role, generation=generation)


def _scored_at(name: str, score: float):
    return make_scored(product_name=name, score=score)


def _ordinal_score(i: int) -> float:
    """A monotonically increasing score for index `i` that stays within
    `Scored.score`'s [0.0, 10.0] bound for every `i` these tests use
    (up to `ROW_CAP + 1`)."""
    return round(i * 0.5, 1)


def test_demote_for_row_cap_noop_under_the_cap():
    products = [_row(f"P{i}") for i in range(ROW_CAP)]
    scores = [_scored_at(f"P{i}", _ordinal_score(i)) for i in range(ROW_CAP)]

    updated, caveats = _demote_for_row_cap(products, scores)

    assert updated == products
    assert caveats == []


def test_demote_for_row_cap_demotes_lowest_scoring_current_gen_row():
    products = [_row(f"P{i}") for i in range(ROW_CAP)] + [
        _row("Prior1", generation="prior")
    ]
    scores = [_scored_at(f"P{i}", _ordinal_score(i)) for i in range(ROW_CAP)] + [
        _scored_at("Prior1", 9.9)
    ]
    # P0 has the lowest score (0.0) among current-gen recommendation rows.

    updated, caveats = _demote_for_row_cap(products, scores)

    by_name = {p.name: p for p in updated}
    assert by_name["P0"].role == "reference_displaced"
    assert by_name["Prior1"].role == "recommendation"  # never touched
    assert sum(1 for p in updated if p.role == "recommendation") == ROW_CAP
    assert len(caveats) == 1
    assert "P0" in caveats[0]
    assert "not discarded" in caveats[0].lower() or "reference" in caveats[0].lower()


def test_demote_for_row_cap_never_demotes_a_prior_gen_row():
    """Even when a prior-gen row is the lowest scorer overall, it must
    never be the one demoted — §5.1: it's the newly admitted row the cap
    exists to make room for."""
    products = [_row(f"P{i}") for i in range(ROW_CAP)] + [
        _row("Prior1", generation="prior")
    ]
    scores = [_scored_at(f"P{i}", 6.0 + i * 0.2) for i in range(ROW_CAP)] + [
        _scored_at("Prior1", 0.1)  # lowest score of all, but must survive
    ]

    updated, caveats = _demote_for_row_cap(products, scores)

    by_name = {p.name: p for p in updated}
    assert by_name["Prior1"].role == "recommendation"
    assert by_name["P0"].role == "reference_displaced"  # lowest CURRENT-gen row instead


def test_demote_for_row_cap_ignores_non_recommendation_rows():
    """Reference rows already count toward none of the above (§5.1) — an
    already-demoted or otherwise-non-recommendation row must never be
    touched again or double-counted."""
    products = [_row(f"P{i}") for i in range(ROW_CAP)] + [
        _row("Above", role="reference_above_budget"),
        _row("Prior1", generation="prior"),
    ]
    scores = [_scored_at(f"P{i}", _ordinal_score(i)) for i in range(ROW_CAP)] + [
        _scored_at("Above", 9.0),
        _scored_at("Prior1", 8.0),
    ]

    updated, caveats = _demote_for_row_cap(products, scores)

    by_name = {p.name: p for p in updated}
    assert by_name["Above"].role == "reference_above_budget"  # untouched
    assert by_name["P0"].role == "reference_displaced"
    assert len(caveats) == 1


def test_demote_for_row_cap_stops_when_no_current_gen_row_left():
    """Safety stop: never demote a prior-gen row even to relieve overflow
    that only prior-gen rows are causing."""
    products = [_row(f"Prior{i}", generation="prior") for i in range(ROW_CAP + 2)]
    scores = [_scored_at(f"Prior{i}", _ordinal_score(i)) for i in range(ROW_CAP + 2)]

    updated, caveats = _demote_for_row_cap(products, scores)

    assert updated == products  # nothing eligible to demote; left as-is
    assert caveats == []


# -- run_scoring ------------------------------------------------------------


def test_run_scoring_short_circuits_on_empty_products():
    scorer = FakeScorer([])
    outcome = run(
        run_scoring([], make_survey_report(), make_intake_answers(), [], False, scorer)
    )
    assert outcome.products == []
    assert outcome.scores == []
    assert outcome.caveats == []
    assert scorer.calls == []


def test_run_scoring_accepts_clean_first_attempt_without_reprompt():
    product = make_product(name="Widget Pro", in_budget=True)
    clean = RawScoring(
        scores=[_raw_score(product_name="Widget Pro", score=6.3, role="recommendation")]
    )
    scorer = FakeScorer([clean])

    outcome = run(
        run_scoring(
            [product],
            make_survey_report(),
            make_intake_answers(),
            [make_topic_answer()],
            False,
            scorer,
        )
    )

    assert len(scorer.calls) == 1
    assert scorer.calls[0][5] is None  # no feedback on the first attempt
    assert outcome.caveats == []
    assert len(outcome.scores) == 1
    assert outcome.scores[0].score == 6.3
    assert outcome.products[0].role == "recommendation"


def test_run_scoring_reprompts_then_accepts_and_logs_unmet_constraint():
    products = [
        make_product(name=n, in_budget=True) for n in ("A", "B", "C")
    ]
    survey = make_survey_report()
    intake = make_intake_answers()

    undifferentiated = RawScoring(
        scores=[
            _raw_score(product_name=n, score=6.0 + i * 0.1, role="recommendation", strength_archetype="value")
            for i, n in enumerate(("A", "B", "C"))
        ]
    )
    # Never fixes the issue — every scripted response is equally
    # undifferentiated, so the bound must actually terminate the loop.
    scorer = FakeScorer([undifferentiated])

    outcome = run(run_scoring(products, survey, intake, [], False, scorer))

    assert len(scorer.calls) == SCORING_REPROMPT_MAX + 1
    # Every re-prompt call after the first carries non-None feedback.
    assert scorer.calls[0][5] is None
    assert all(call[5] is not None for call in scorer.calls[1:])
    assert any("archetype" in c for c in outcome.caveats)


def test_run_scoring_reprompt_resolves_issue_on_second_attempt():
    products = [make_product(name=n, in_budget=True) for n in ("A", "B", "C")]
    bad = RawScoring(
        scores=[
            _raw_score(product_name=n, score=6.0, role="recommendation", strength_archetype="value")
            for n in ("A", "B", "C")
        ]
    )
    good = RawScoring(
        scores=[
            _raw_score(product_name=n, score=6.0 + i * 0.3, role="recommendation", strength_archetype=arch)
            for i, (n, arch) in enumerate(zip(("A", "B", "C"), ("value", "durability", "aesthetic")))
        ]
    )
    scorer = FakeScorer([bad, good])

    outcome = run(
        run_scoring(products, make_survey_report(), make_intake_answers(), [], False, scorer)
    )

    assert len(scorer.calls) == 2
    assert outcome.caveats == []


def test_run_scoring_threads_low_evidence_mode_into_flip_point_suppression():
    """End-to-end check that `run_scoring` actually wires `low_evidence_mode`
    through to `_build_scored`/`_compute_flip_point` — not just that the
    pure function honors it in isolation (already covered above)."""
    product = make_product(name="Widget Pro", in_budget=True, pricing=make_pricing_model())
    clean = RawScoring(
        scores=[
            RawProductScore(
                product_name="Widget Pro",
                score=6.0,
                rationale="x",
                role="recommendation",
                strength_archetype="value",
                score_at_minus_10pct=6.5,
                score_at_minus_20pct=7.0,
                score_at_minus_30pct=7.5,
            )
        ]
    )
    scorer = FakeScorer([clean])

    outcome = run(
        run_scoring([product], make_survey_report(), make_intake_answers(), [], True, scorer)
    )

    assert scorer.calls[0][4] is True  # low_evidence_mode reached the scorer too
    assert outcome.scores[0].flip_point_amount is None
    assert "too thin" in outcome.scores[0].flip_point_note
