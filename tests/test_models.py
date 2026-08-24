"""Unit tests for models.py — the §4.1 assertion table plus key validators."""

import pytest
from pydantic import ValidationError

from product_scout.models import (
    SourcedValue,
    TimingAssessment,
    TimingSignal,
    compute_confidence,
)
from tests.conftest import make_evidence_profile, make_product, make_scored


# -- §4.1 assertion table --------------------------------------------------


def test_confidence_full_evidence_is_1_000():
    e = make_evidence_profile(
        independent_review_count=4,
        has_tier1_specs=True,
        has_methodology_backed_source=True,
        corroboration_ratio=1.0,
        conflict_ratio=0.0,
    )
    assert compute_confidence(e) == 1.000


def test_confidence_partial_evidence_is_0_725():
    e = make_evidence_profile(
        independent_review_count=2,
        has_tier1_specs=True,
        has_methodology_backed_source=True,
        corroboration_ratio=0.5,
        conflict_ratio=0.0,
    )
    assert compute_confidence(e) == 0.725


def test_confidence_minimal_evidence_is_0_200():
    e = make_evidence_profile(
        independent_review_count=1,
        has_tier1_specs=False,
        has_methodology_backed_source=False,
        corroboration_ratio=0.0,
        conflict_ratio=0.0,
    )
    assert compute_confidence(e) == 0.200


def test_confidence_review_count_clamped_above_four():
    e = make_evidence_profile(
        independent_review_count=99,
        corroboration_ratio=1.0,
        conflict_ratio=0.0,
        has_tier1_specs=True,
        has_methodology_backed_source=True,
    )
    assert compute_confidence(e) == 1.000


def test_confidence_result_within_bounds():
    e = make_evidence_profile(
        independent_review_count=0,
        has_tier1_specs=False,
        has_methodology_backed_source=False,
        corroboration_ratio=0.0,
        conflict_ratio=1.0,
    )
    c = compute_confidence(e)
    assert 0.0 <= c <= 1.0


# -- invariant 2: no source, no field ---------------------------------------


def test_sourced_value_rejects_empty_source_url():
    with pytest.raises(ValidationError):
        SourcedValue(value="x", source_url="", source_tier=1)


def test_sourced_value_rejects_non_url_placeholder():
    with pytest.raises(ValidationError):
        SourcedValue(value="x", source_url="N/A", source_tier=1)


def test_sourced_value_accepts_real_url():
    sv = SourcedValue(value="x", source_url="https://example.com/spec", source_tier=1)
    assert sv.source_url == "https://example.com/spec"


def test_sourced_value_rejects_out_of_range_tier():
    with pytest.raises(ValidationError):
        SourcedValue(value="x", source_url="https://example.com", source_tier=6)


# -- invariant 4: cons/pros minimums ----------------------------------------


def test_product_requires_at_least_one_con():
    with pytest.raises(ValidationError):
        make_product(cons=[])


def test_product_requires_at_least_one_pro():
    with pytest.raises(ValidationError):
        make_product(pros=[])


def test_product_rejects_placeholder_price_source_url():
    with pytest.raises(ValidationError):
        make_product(price_source_url="unknown")


# -- §5.2: flip point suppressed below 0.70 confidence -----------------------


def test_scored_rejects_flip_point_below_confidence_floor():
    with pytest.raises(ValidationError):
        make_scored(confidence=0.5, flip_point_usd=249.0)


def test_scored_allows_null_flip_point_below_confidence_floor():
    s = make_scored(
        confidence=0.5,
        flip_point_usd=None,
        flip_point_note="No flip point — evidence too thin to price the comparison.",
    )
    assert s.flip_point_usd is None


def test_scored_allows_flip_point_at_or_above_confidence_floor():
    s = make_scored(confidence=0.70, flip_point_usd=249.0, flip_point_note="Overtakes at $249.")
    assert s.flip_point_usd == 249.0


def test_scored_allows_null_flip_point_above_floor_for_top_pick():
    # Legitimate: this is the #1 pick, nothing to flip to.
    s = make_scored(confidence=0.95, flip_point_usd=None, flip_point_note=None)
    assert s.flip_point_usd is None


# -- §6.5 gap-fill: TimingAssessment consistency ------------------------------


def test_timing_assessment_no_signal_forbids_signals_list():
    with pytest.raises(ValidationError):
        TimingAssessment(
            has_signal=False,
            signals=[TimingSignal(kind="price_trend", claim="x", basis="y")],
            summary="no timing signal found",
        )


def test_timing_assessment_has_signal_requires_at_least_one_signal():
    with pytest.raises(ValidationError):
        TimingAssessment(has_signal=True, signals=[], summary="something is coming")


def test_timing_assessment_no_signal_ok_when_no_signals():
    t = TimingAssessment(has_signal=False, signals=[], summary="no timing signal found")
    assert t.summary == "no timing signal found"
