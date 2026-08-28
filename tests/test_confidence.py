"""Unit tests for confidence.py — spec docs/handoff.md §4.1's assertion
table, §4.1a's property tests, and §5.2's flip-point predicate.
"""

from datetime import datetime, timedelta, timezone

import pytest

from product_scout.confidence import (
    FLIP_FLOOR,
    LOW_EVIDENCE_CONFIDENCE_CLAMP,
    build_evidence_profile,
    compute_confidence,
    confidence_band,
    flip_point_eligible,
    property_test_violations,
)
from product_scout.models import EvidenceProfile
from tests.conftest import NOW, make_product, make_sourced_value, make_survey_report

# -- §4.1 assertion table ----------------------------------------------------
#
# Transcribed from the spec's own (label, t1, meth, reviews, corr, conflict,
# rec, expected, band) tuples, plus two fields the spec's 9-tuple omits but
# §4.0c's coherence validator requires to construct a legal EvidenceProfile:
# `source_count` (must be >1 whenever corr or conflict is nonzero — chosen
# per-row from what the label implies, e.g. "one review + corroborating mfr"
# is source_count=2) and `extracted_spec_count` (must be nonzero whenever
# conflict is nonzero; 1 for every row except the zero-evidence case).

CONFIDENCE_CASES = [
    # label, t1, meth, reviews, corr, conflict, rec, source_count, extracted, expected, band
    ("saturated evidence", 1, 1, 6, 1.0, 0.0, 1.0, 6, 1, 0.950, "high"),
    ("well covered", 1, 1, 4, 1.0, 0.0, 1.0, 4, 1, 0.889, "high"),
    ("typical rich category", 1, 1, 3, 0.8, 0.1, 1.0, 3, 1, 0.777, "moderate"),
    ("solid mainstream", 1, 1, 2, 0.7, 0.0, 1.0, 2, 1, 0.719, "moderate"),
    ("two reviews, half corroborated", 1, 1, 2, 0.5, 0.0, 1.0, 2, 1, 0.696, "moderate"),
    ("no methodology source", 1, 0, 4, 0.8, 0.0, 1.0, 4, 1, 0.620, "low"),
    ("heavy conflict, corroborated", 1, 1, 3, 0.5, 0.5, 1.0, 3, 1, 0.617, "low"),
    ("total conflict, 20 reviews", 1, 1, 20, 1.0, 1.0, 1.0, 20, 1, 0.600, "low"),
    ("one review + corroborating mfr", 1, 1, 1, 1.0, 0.0, 1.0, 2, 1, 0.631, "low"),
    ("single source (corr forced 0)", 1, 1, 1, 0.0, 0.0, 1.0, 1, 1, 0.537, "low"),
    ("total conflict, 3 reviews", 1, 1, 3, 1.0, 1.0, 1.0, 3, 1, 0.501, "low"),
    ("low-evidence: mfr + 2 community", 1, 0, 2, 0.0, 0.2, 0.8, 3, 1, 0.387, "low"),
    ("one community source", 0, 0, 1, 0.0, 0.0, 1.0, 1, 1, 0.154, "very_low"),
    ("no evidence at all", 0, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0.000, "very_low"),
]


def _make_profile(t1, meth, reviews, corr, conflict, rec, source_count, extracted) -> EvidenceProfile:
    """Confidence is left at 0.0 here — callers that want compute_confidence's
    *return value* call it themselves (the assertion-table tests below).
    Callers that need `.confidence` populated on the object itself (flip-point
    tests, which read `product.evidence.confidence`) use `_make_profile_scored`
    instead."""
    return EvidenceProfile(
        source_count=source_count,
        independent_review_count=reviews,
        extracted_spec_count=extracted,
        has_tier1_specs=bool(t1),
        has_methodology_backed_source=bool(meth),
        corroboration_ratio=corr,
        conflict_ratio=conflict,
        recency_factor=rec,
        confidence=0.0,
        confidence_note="test row",
    )


def _make_profile_scored(t1, meth, reviews, corr, conflict, rec, source_count, extracted) -> EvidenceProfile:
    """Same as _make_profile, but with .confidence actually computed and
    assigned — for callers reading the field directly rather than calling
    compute_confidence() themselves."""
    e = _make_profile(t1, meth, reviews, corr, conflict, rec, source_count, extracted)
    e.confidence = compute_confidence(e)
    return e


@pytest.mark.parametrize(
    "label,t1,meth,reviews,corr,conflict,rec,source_count,extracted,expected,band",
    CONFIDENCE_CASES,
    ids=[c[0] for c in CONFIDENCE_CASES],
)
def test_confidence_assertion_table(
    label, t1, meth, reviews, corr, conflict, rec, source_count, extracted, expected, band
):
    e = _make_profile(t1, meth, reviews, corr, conflict, rec, source_count, extracted)
    got = compute_confidence(e)
    assert got == expected
    assert confidence_band(got) == band


def test_saturated_evidence_row_is_the_realistic_ceiling():
    """Documents why no row hits 1.000: review_credit(n) is asymptotic, so
    even 6 reviews leaves headroom (0.950), unlike v3's hard-capped curve
    which saturated at review count 4."""
    e = _make_profile(1, 1, 6, 1.0, 0.0, 1.0, 6, 1)
    assert compute_confidence(e) == 0.950


def test_total_conflict_20_reviews_is_the_structural_ceiling_case():
    """§4.1: 'the conflict coefficient is 0.40, which makes the cap
    structural' — at conflict_ratio == 1.0, quality is pinned to 0.60, so
    confidence cannot exceed 0.600 for ANY breadth. This is the row that
    survives curve tuning (§19.2) because it's a ceiling, not a point value
    dependent on the review-credit scale factor."""
    e = _make_profile(1, 1, 20, 1.0, 1.0, 1.0, 20, 1)
    assert compute_confidence(e) == 0.600


def test_no_evidence_at_all_is_exactly_zero():
    """Headline property of the multiplicative form: breadth-zero yields
    exactly 0.000, not some additive-formula residual."""
    e = _make_profile(0, 0, 0, 0.0, 0.0, 0.0, 0, 0)
    assert compute_confidence(e) == 0.000


# -- §4.0c coherence validator ------------------------------------------------


def test_coherence_rejects_nonzero_corroboration_at_single_source():
    with pytest.raises(Exception):
        EvidenceProfile(
            source_count=1,
            independent_review_count=1,
            extracted_spec_count=1,
            has_tier1_specs=True,
            has_methodology_backed_source=True,
            corroboration_ratio=0.5,
            conflict_ratio=0.0,
            recency_factor=1.0,
            confidence=0.0,
            confidence_note="x",
        )


def test_coherence_rejects_nonzero_conflict_at_single_source():
    with pytest.raises(Exception):
        EvidenceProfile(
            source_count=1,
            independent_review_count=1,
            extracted_spec_count=1,
            has_tier1_specs=True,
            has_methodology_backed_source=True,
            corroboration_ratio=0.0,
            conflict_ratio=0.5,
            recency_factor=1.0,
            confidence=0.0,
            confidence_note="x",
        )


def test_coherence_rejects_conflict_with_zero_extracted():
    with pytest.raises(Exception):
        EvidenceProfile(
            source_count=3,
            independent_review_count=3,
            extracted_spec_count=0,
            has_tier1_specs=True,
            has_methodology_backed_source=True,
            corroboration_ratio=0.0,
            conflict_ratio=0.5,
            recency_factor=1.0,
            confidence=0.0,
            confidence_note="x",
        )


def test_coherence_allows_both_corroborated_and_conflicted():
    """§4.0a: 'a single spec can be both.' No validator on the ratio sum."""
    e = _make_profile(1, 1, 3, 1.0, 1.0, 1.0, 3, 1)
    assert e.corroboration_ratio == 1.0
    assert e.conflict_ratio == 1.0


# -- §4.1a property tests -----------------------------------------------------
#
# These assert the SHAPE of compute_confidence, not point values, so they
# survive the weight tuning §19 already schedules (§19.1, §19.2, §19.3).


def test_single_review_cannot_price_a_comparison():
    """A flip point always rests on >= 2 independent reviews. Tests the
    PREDICATE, not the arithmetic — the best single-review profile computes
    to 0.631, only 0.019 under FLIP_FLOOR, so asserting on confidence alone
    would silently break under §19.2's curve tuning."""
    for corr in (0.0, 1.0):  # 1.0 is reachable: mfr page + one review
        product = make_product(
            evidence=_make_profile_scored(1, 1, 1, corr, 0.0, 1.0, source_count=2, extracted=1)
        )
        assert not flip_point_eligible(product)


def test_total_conflict_cannot_price_a_comparison():
    """§4.1: total disagreement is a structural cap, not a margin."""
    for reviews in range(2, 21):
        for corr in (0.0, 0.5, 1.0):
            e = _make_profile(1, 1, reviews, corr, 1.0, 1.0, source_count=reviews, extracted=1)
            assert compute_confidence(e) < FLIP_FLOOR


def test_absence_never_pays():
    """Zero breadth is zero confidence, whatever quality says."""
    for rec in (0.0, 0.5, 1.0):
        e = _make_profile(0, 0, 0, 0.0, 0.0, rec, source_count=0, extracted=0)
        assert compute_confidence(e) == 0.0


def test_bands_tile():
    """Every representable confidence maps to exactly one band."""
    for i in range(1001):
        assert confidence_band(round(i / 1000, 3)) in {"high", "moderate", "low", "very_low"}


def test_property_test_violations_is_empty():
    """`property_test_violations()` is the same four sweeps above, extracted
    into a reusable function §17.1's golden set also calls (build order
    step 15) — this pins that it agrees with the four tests it was
    refactored out of."""
    assert property_test_violations() == []


# -- §4.2 display bands --------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.00, "high"),
        (0.80, "high"),
        (0.799, "moderate"),
        (0.65, "moderate"),
        (0.649, "low"),
        (0.35, "low"),
        (0.349, "very_low"),
        (0.00, "very_low"),
    ],
)
def test_band_boundaries_are_exact(value, expected):
    assert confidence_band(value) == expected


# -- §5.2 flip_point_eligible --------------------------------------------------


def test_flip_point_eligible_requires_both_clauses():
    high_conf_one_review = make_product(
        evidence=_make_profile_scored(1, 1, 1, 1.0, 0.0, 1.0, source_count=2, extracted=1)
    )
    assert high_conf_one_review.evidence.confidence >= FLIP_FLOOR - 0.02  # close but under
    assert not flip_point_eligible(high_conf_one_review)

    two_reviews_low_conf = make_product(
        evidence=_make_profile_scored(0, 0, 2, 0.0, 1.0, 0.5, source_count=2, extracted=1)
    )
    assert not flip_point_eligible(two_reviews_low_conf)


def test_flip_point_eligible_true_above_both_thresholds():
    product = make_product(
        evidence=_make_profile_scored(1, 1, 4, 1.0, 0.0, 1.0, source_count=4, extracted=1)
    )
    assert flip_point_eligible(product)


# -- §4.0d build_evidence_profile ---------------------------------------------


def test_build_evidence_profile_counts_from_product_and_survey():
    survey = make_survey_report(comparison_specs=["weight", "motor_count"])
    product = make_product(
        specs={
            "weight": make_sourced_value(
                source_type="manufacturer",
                corroborated_by=["https://other.example.com/review"],
            ),
            "motor_count": make_sourced_value(
                source_type="testing_outlet",
                has_stated_methodology=True,
                conflicting_values=["single"],
            ),
        },
        review_sources=["https://example.com/review", "https://example.com/review#frag"],
    )
    profile = build_evidence_profile(product, survey, now=product.specs["weight"].observed_at)

    assert profile.extracted_spec_count == 2
    assert profile.corroboration_ratio == 0.5  # 1 of 2 comparison_specs corroborated
    assert profile.conflict_ratio == 0.5  # 1 of 2 extracted specs conflicted
    assert profile.has_tier1_specs is True
    assert profile.has_methodology_backed_source is True
    # review_sources has one duplicate after URL normalization (§4.3: query/
    # fragment-insensitive) — independent_review_count counts it once.
    assert profile.independent_review_count == 1
    assert 0.0 <= profile.confidence <= 1.0


def test_build_evidence_profile_recency_uses_injected_now():
    anchor = datetime(2020, 1, 1, tzinfo=timezone.utc)
    survey = make_survey_report(
        dimensions=[], comparison_specs=["weight"], category_kind="software_service"
    )
    stale = make_sourced_value(observed_at=anchor)
    product = make_product(specs={"weight": stale})
    profile = build_evidence_profile(product, survey, now=anchor + timedelta(days=365 * 5))
    assert profile.recency_factor == 0.0  # far outside the 9-month software window


# -- §8.3 low-evidence confidence clamp --------------------------------------
#
# "Confidence clamped to 0.75... Note this clamp rarely binds — a typical
# low-evidence profile computes to 0.427." CLAUDE.md invariant 10: exercise
# it against a SYNTHETIC profile that would otherwise exceed it — don't
# assume it fires on a realistic one.


def _saturated_product() -> "object":
    """Mirrors the §4.1a assertion table's "saturated evidence" row
    (t1=1, meth=1, 6 reviews, full corroboration, no conflict, fully
    recent) — computes to 0.950 uncorroborated by any clamp, comfortably
    above LOW_EVIDENCE_CONFIDENCE_CLAMP."""
    return make_product(
        specs={
            "weight": make_sourced_value(
                source_type="manufacturer",
                has_stated_methodology=True,
                corroborated_by=["https://other.example.com/review"],
            )
        },
        review_sources=[f"https://example.com/review{i}" for i in range(6)],
    )


def test_clamp_binds_on_synthetic_high_confidence_profile_in_low_evidence_mode():
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"])
    product = _saturated_product()

    unclamped = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)
    clamped = build_evidence_profile(product, survey, low_evidence_mode=True, now=NOW)

    assert unclamped.confidence > LOW_EVIDENCE_CONFIDENCE_CLAMP  # would otherwise exceed it
    assert clamped.confidence == LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert "clamped for low-evidence mode" in clamped.confidence_note
    assert f"{unclamped.confidence:.3f}" in clamped.confidence_note  # both values recorded


def test_clamp_does_not_bind_below_the_ceiling():
    """Rarely binds in practice — a typical low-evidence profile stays
    well under 0.75, so the clamp must be a no-op there."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"])
    product = make_product(
        specs={"weight": make_sourced_value(source_type="community")},
        review_sources=["https://forum.example.com/thread/1"],
    )
    profile = build_evidence_profile(product, survey, low_evidence_mode=True, now=NOW)
    assert profile.confidence < LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert "clamped" not in profile.confidence_note


def test_clamp_inactive_when_low_evidence_mode_is_false():
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"])
    product = _saturated_product()
    profile = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)
    assert profile.confidence > LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert "clamped" not in profile.confidence_note


def test_clamp_defaults_to_off():
    """low_evidence_mode has a default (§8.3 is opt-in, not the base case)
    — calling without it must behave identically to explicitly False."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"])
    product = _saturated_product()
    default_profile = build_evidence_profile(product, survey, now=NOW)
    explicit_profile = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)
    assert default_profile.confidence == explicit_profile.confidence
    assert default_profile.confidence_note == explicit_profile.confidence_note


# -- §8.1's per-product moderate-coverage clamp (no run-level flag involved) -


def _n_review_saturated_product(n: int):
    """Otherwise-saturated (tier-1, methodology, corroborated, fully
    recent) but with exactly `n` independent reviews — isolates review
    count as the only lever."""
    return make_product(
        specs={
            "weight": make_sourced_value(
                source_type="manufacturer",
                has_stated_methodology=True,
                corroborated_by=["https://other.example.com/review"],
            )
        },
        review_sources=[f"https://example.com/review{i}" for i in range(n)],
    )


def test_review_count_one_can_never_exceed_the_clamp_even_unclamped():
    """Documents WHY the moderate-coverage threshold below is 4, not 2:
    review_credit(1) caps breadth at 0.6313 regardless of every other
    factor maxing out — a threshold of `< 2` would gate a clamp that could
    never actually fire, i.e. dead code."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"])
    profile = build_evidence_profile(
        _n_review_saturated_product(1), survey, low_evidence_mode=False, now=NOW
    )
    assert profile.confidence < LOW_EVIDENCE_CONFIDENCE_CLAMP


def test_moderate_coverage_clamps_an_undercovered_product_even_without_the_flag():
    """§8.1: 'moderate | ... | Proceed; low-evidence for under-covered
    products only.' §8.3: the clamp 'exists for the moderate coverage
    path, where a well-covered product sits inside a partially-low-
    evidence run.' The RUN-level low_evidence_mode flag is False the
    whole time — coverage="moderate" plus this product's own review count
    (3, below the §8.1 moderate-row ceiling of 4) is what triggers it."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"], coverage="moderate")
    product = _n_review_saturated_product(3)

    profile = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)

    assert profile.confidence == LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert "under-covered product in a moderate-coverage run" in profile.confidence_note


def test_moderate_coverage_boundary_four_reviews_is_not_undercovered():
    """`< 4`, not `<= 4` — the §8.1 row's own ceiling counts as adequately
    covered, not under-covered."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"], coverage="moderate")
    profile = build_evidence_profile(
        _n_review_saturated_product(4), survey, low_evidence_mode=False, now=NOW
    )
    assert "clamped" not in profile.confidence_note


def test_moderate_coverage_does_not_clamp_a_well_covered_product():
    """The nuance is 'under-covered products ONLY' — a product with
    plenty of its own reviews stays unclamped even in a moderate run."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"], coverage="moderate")
    product = _saturated_product()  # 6 independent reviews
    profile = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)
    assert profile.confidence > LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert "clamped" not in profile.confidence_note


def test_rich_coverage_does_not_apply_the_moderate_undercovered_clamp():
    """The §8.1 nuance is stated only for the moderate row — a thinly-
    cited product in a RICH-coverage run isn't covered by this clause."""
    survey = make_survey_report(dimensions=[], comparison_specs=["weight"], coverage="rich")
    product = _n_review_saturated_product(3)
    profile = build_evidence_profile(product, survey, low_evidence_mode=False, now=NOW)
    assert "clamped" not in profile.confidence_note
