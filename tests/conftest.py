"""Shared fixtures/factories for Product Scout tests (spec v7)."""

from datetime import datetime, timezone

import pytest

from product_scout.confidence import compute_confidence
from product_scout.io.port import AxisSpec, TopicPrompt
from product_scout.models import (
    Availability,
    BroaderCategory,
    Caveat,
    Cluster,
    Dimension,
    EvidenceProfile,
    IntakeAnswers,
    Location,
    PricingModel,
    Product,
    RunRecord,
    Scored,
    SourcedValue,
    SurveyReport,
    TimingAssessment,
    TopicAnswer,
    Verdict,
)

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def make_sourced_value(**overrides) -> SourcedValue:
    defaults = dict(
        value="42 lb",
        native_unit="lb",
        source_url="https://example.com/spec-sheet",
        source_type="manufacturer",
        has_stated_methodology=False,
        observed_at=NOW,
    )
    defaults.update(overrides)
    return SourcedValue(**defaults)


def make_location(**overrides) -> Location:
    defaults = dict(country="US", currency="USD")
    defaults.update(overrides)
    return Location(**defaults)


def make_availability(**overrides) -> Availability:
    defaults = dict(
        sold_in_region=True,
        ships_to_region=None,
        ships_from=None,
        ships_from_signal=None,
        ships_from_confidence=0.0,
    )
    defaults.update(overrides)
    return Availability(**defaults)


def make_pricing_model(**overrides) -> PricingModel:
    defaults = dict(
        model_type="one_time",
        upfront_amount=199.0,
        recurring_amount=None,
        recurring_period=None,
        total_cost_1yr=199.0,
        price_currency="USD",
        price_tax_inclusive=None,
        price_source_url="https://example.com/product",
        price_observed_at=NOW,
    )
    defaults.update(overrides)
    return PricingModel(**defaults)


def make_evidence_profile(**overrides) -> EvidenceProfile:
    """Provisional confidence=0.0 unless the caller overrides it; otherwise
    computed for free so callers building a Product don't need to remember
    the two-step assign pattern themselves (see confidence.py's own
    docstring on why the pattern exists at all)."""
    defaults = dict(
        source_count=2,
        independent_review_count=2,
        extracted_spec_count=1,
        has_tier1_specs=True,
        has_methodology_backed_source=True,
        corroboration_ratio=0.5,
        conflict_ratio=0.0,
        recency_factor=1.0,
        confidence=0.0,
        confidence_note="two independent reviews, half corroborated",
    )
    defaults.update(overrides)
    profile = EvidenceProfile(**defaults)
    if "confidence" not in overrides:
        profile.confidence = compute_confidence(profile)
    return profile


def make_axis_spec(**overrides) -> AxisSpec:
    defaults = dict(
        kind="position",
        low_label="single motor",
        high_label="dual motor",
        why_this_matters="Among your candidates this splits the $200 and $250 tiers.",
    )
    defaults.update(overrides)
    return AxisSpec(**defaults)


def make_topic_prompt(**overrides) -> TopicPrompt:
    defaults = dict(
        topic="motor configuration",
        dimension_name="motor_count",
        gate_question="Do you need dual motors specifically?",
        gate_description="A must-have excludes single-motor desks; a must-avoid excludes dual-motor ones.",
        axis=make_axis_spec(),
        free_text_prompt="Anything else about motors that matters to you?",
    )
    defaults.update(overrides)
    return TopicPrompt(**defaults)


def make_broader_category(**overrides) -> BroaderCategory:
    defaults = dict(
        name="broader widgets",
        rationale="Wider category with an established review economy.",
        estimated_coverage="rich",
    )
    defaults.update(overrides)
    return BroaderCategory(**defaults)


def make_cluster(**overrides) -> Cluster:
    defaults = dict(
        key="mid-tier",
        label="Mid-tier standing desks",
        exemplar_products=["Widget Pro"],
        price_range_native=(150.0, 300.0),
        approximate_member_count=6,
    )
    defaults.update(overrides)
    return Cluster(**defaults)


def make_dimension(**overrides) -> Dimension:
    defaults = dict(
        name="motor_count",
        splits={"mid-tier": "dual", "budget": "single"},
        axis_kind="position",
    )
    defaults.update(overrides)
    return Dimension(**defaults)


def make_survey_report(**overrides) -> SurveyReport:
    defaults = dict(
        category_kind="physical",
        coverage="rich",
        differentiation="moderate",
        estimated_product_count=8,
        independent_review_sources_found=5,
        has_methodology_backed_testing=True,
        clusters=[make_cluster()],
        dimensions=[make_dimension()],
        comparison_specs=["weight", "motor_count"],
        pricing_complexity="simple",
        secondhand_risk_factors=[],
        products_unavailable_in_region=0,
        suggested_broader_categories=[],
        notes="Well-covered category.",
    )
    defaults.update(overrides)
    return SurveyReport(**defaults)


def make_intake_answers(**overrides) -> IntakeAnswers:
    defaults = dict(
        owns_current_version=False,
        current_model=None,
        budget_ceiling=300.0,
        budget_note="flexible if quality justifies it",
        required_features=[],
        candidates_under_consideration=[],
    )
    defaults.update(overrides)
    return IntakeAnswers(**defaults)


def make_timing_assessment(**overrides) -> TimingAssessment:
    defaults = dict(
        signal_found=False,
        successor_expected=None,
        price_trend=None,
        technology_transition=None,
        basis_notes=[],
        recommends_wait=False,
    )
    defaults.update(overrides)
    return TimingAssessment(**defaults)


def make_topic_answer(**overrides) -> TopicAnswer:
    defaults = dict(
        topic="motor configuration",
        dimension_name="motor_count",
        gate_answer="persuadable",
        axis_kind="position",
        axis_value=0.5,
        axis_skipped=False,
        free_text="",
        became_filter=False,
        assumption_logged=None,
    )
    defaults.update(overrides)
    return TopicAnswer(**defaults)


def make_caveat(**overrides) -> Caveat:
    defaults = dict(
        tier="provenance",
        text="Single-source, unverified.",
        anchor="Widget Pro",
        instance_count=1,
    )
    defaults.update(overrides)
    return Caveat(**defaults)


def make_product(**overrides) -> Product:
    defaults = dict(
        name="Widget Pro",
        brand="Acme",
        generation="current",
        role="recommendation",
        cluster_key="mid-tier",
        cluster_rationale="Dual-motor mid-tier desks under $300.",
        strength_archetype="value",
        pricing=make_pricing_model(),
        availability=make_availability(),
        specs={"weight": make_sourced_value()},
        pros=["sturdy"],
        cons=["expensive"],
        ownership_notes=[],
        review_sources=["https://example.com/review"],
        in_budget=True,
        evidence=make_evidence_profile(),
    )
    defaults.update(overrides)
    return Product(**defaults)


def make_scored(**overrides) -> Scored:
    defaults = dict(
        product_name="Widget Pro",
        score=8.2,
        rationale="Strong fit given stated constraints.",
        score_at_minus_10pct=8.4,
        score_at_minus_20pct=8.6,
        score_at_minus_30pct=8.8,
        flip_point_amount=None,
        flip_point_note=None,
    )
    defaults.update(overrides)
    return Scored(**defaults)


def make_run_record(**overrides) -> RunRecord:
    defaults = dict(
        run_id="test-run-0001",
        created_at=NOW,
        product_type="standing desks",
        original_product_type=None,
        location=make_location(),
        units="imperial",
        intake=make_intake_answers(),
        survey=make_survey_report(),
        topics=[make_topic_answer()],
        low_evidence_mode=False,
        commodity_category=False,
        category_broadening_offered=False,
        truncated_at_phase=None,
        products=[make_product()],
        timing=make_timing_assessment(),
        verdict=Verdict(
            action="BUY",
            reasoning="Best fit for stated constraints.",
            timing_note=None,
        ),
        scores=[make_scored()],
        caveats=[],
        model_ids={"analysis": "claude-opus-4-5-20260101"},
        skill_hashes={},
        trusted_sources=[],
    )
    defaults.update(overrides)
    return RunRecord(**defaults)


@pytest.fixture
def run_record_factory():
    return make_run_record
