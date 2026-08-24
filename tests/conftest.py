"""Shared fixtures/factories for Product Scout tests."""

from datetime import datetime, timezone

import pytest

from product_scout.models import (
    CoverageReport,
    EvidenceProfile,
    Product,
    RunRecord,
    Scored,
    SourcedValue,
    TimingAssessment,
    Verdict,
)


def make_evidence_profile(**overrides) -> EvidenceProfile:
    defaults = dict(
        independent_review_count=2,
        has_tier1_specs=True,
        has_methodology_backed_source=True,
        corroboration_ratio=0.5,
        conflict_ratio=0.0,
        confidence=0.0,  # provisional; caller/compute_confidence fills it in
        confidence_note="two independent reviews, half corroborated",
    )
    defaults.update(overrides)
    return EvidenceProfile(**defaults)


def make_sourced_value(**overrides) -> SourcedValue:
    defaults = dict(
        value="42 lb",
        source_url="https://example.com/spec-sheet",
        source_tier=1,
    )
    defaults.update(overrides)
    return SourcedValue(**defaults)


def make_product(**overrides) -> Product:
    defaults = dict(
        name="Widget Pro",
        brand="Acme",
        generation="current",
        price_usd=199.0,
        price_source_url="https://example.com/product",
        price_observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        specs={"weight": make_sourced_value()},
        pros=["sturdy"],
        cons=["expensive"],
        strength_archetype="value",
        in_budget=True,
        review_sources=["https://example.com/review"],
        evidence=make_evidence_profile(),
    )
    defaults.update(overrides)
    return Product(**defaults)


def make_scored(**overrides) -> Scored:
    defaults = dict(
        product_name="Widget Pro",
        score=8.2,
        confidence=0.8,
        rationale="Strong fit given stated constraints.",
        flip_point_usd=None,
        flip_point_note=None,
        score_at_minus_10pct=8.4,
        score_at_minus_20pct=8.6,
    )
    defaults.update(overrides)
    return Scored(**defaults)


def make_run_record(**overrides) -> RunRecord:
    defaults = dict(
        run_id="test-run-0001",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        product_type="standing desks",
        intake={},
        refine={},
        coverage=CoverageReport(
            estimated_product_count=8,
            independent_review_sources_found=5,
            has_methodology_backed_testing=True,
            coverage="rich",
            notes="Well-covered category.",
        ),
        low_evidence_mode=False,
        original_product_type=None,
        category_broadening_offered=False,
        products=[make_product()],
        timing=TimingAssessment(
            has_signal=False, signals=[], summary="no timing signal found"
        ),
        verdict=Verdict(
            action="BUY",
            reasoning="Best fit for stated constraints.",
            timing_note=None,
        ),
        scores=[make_scored()],
        caveats=[],
        model_ids={"analysis": "claude-opus-4-5-20260101"},
        trusted_sources=[],
    )
    defaults.update(overrides)
    return RunRecord(**defaults)


@pytest.fixture
def run_record_factory():
    return make_run_record
