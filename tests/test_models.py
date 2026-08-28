"""Unit tests for models.py — schema-level validators (spec v7).

compute_confidence()'s assertion table and the coherence validator live in
test_confidence.py now that confidence.py exists as its own module — this
file covers the rest of the schema: URL/shape validation, the
cons/pros hard constraints, and SurveyReport's comparison_specs/Dimension
consistency check.
"""

import pytest
from pydantic import ValidationError

from product_scout.models import Product, SourcedValue, normalize_url
from tests.conftest import make_dimension, make_product, make_run_record, make_survey_report


# -- invariant 3: no source, no field -----------------------------------------


def test_sourced_value_rejects_empty_source_url():
    with pytest.raises(ValidationError):
        SourcedValue(
            value="x",
            source_url="",
            source_type="manufacturer",
            has_stated_methodology=False,
            observed_at="2026-08-01T00:00:00Z",
        )


def test_sourced_value_rejects_non_url_placeholder():
    with pytest.raises(ValidationError):
        SourcedValue(
            value="x",
            source_url="N/A",
            source_type="manufacturer",
            has_stated_methodology=False,
            observed_at="2026-08-01T00:00:00Z",
        )


def test_sourced_value_accepts_real_url():
    sv = SourcedValue(
        value="x",
        source_url="https://example.com/spec",
        source_type="manufacturer",
        has_stated_methodology=False,
        observed_at="2026-08-01T00:00:00Z",
    )
    assert sv.source_url == "https://example.com/spec"


def test_sourced_value_rejects_bad_source_type():
    with pytest.raises(ValidationError):
        SourcedValue(
            value="x",
            source_url="https://example.com",
            source_type="influencer",  # not one of the five §14 types
            has_stated_methodology=False,
            observed_at="2026-08-01T00:00:00Z",
        )


def test_product_rejects_placeholder_price_source_url():
    with pytest.raises(ValidationError):
        make_product(pricing={"model_type": "one_time", "price_currency": "USD",
                               "price_source_url": "unknown",
                               "price_observed_at": "2026-08-01T00:00:00Z"})


# -- §4.3 normalize_url --------------------------------------------------------


def test_normalize_url_discards_query_string():
    assert normalize_url("https://example.com/p?utm_source=x") == normalize_url(
        "https://example.com/p"
    )


def test_normalize_url_discards_fragment():
    assert normalize_url("https://example.com/p#section") == normalize_url(
        "https://example.com/p"
    )


def test_normalize_url_is_case_insensitive_on_scheme_and_host():
    assert normalize_url("HTTPS://Example.com/p") == normalize_url("https://example.com/p")


def test_normalize_url_treats_trailing_slash_as_equivalent():
    assert normalize_url("https://example.com/p/") == normalize_url("https://example.com/p")


def test_normalize_url_distinguishes_different_paths():
    assert normalize_url("https://example.com/a") != normalize_url("https://example.com/b")


# -- invariant 6: cons/pros minimums ------------------------------------------


def test_product_requires_at_least_one_con():
    with pytest.raises(ValidationError):
        make_product(cons=[])


def test_product_requires_at_least_one_pro():
    with pytest.raises(ValidationError):
        make_product(pros=[])


def test_product_requires_at_least_one_review_source():
    with pytest.raises(ValidationError):
        make_product(review_sources=[])


def test_product_role_defaults_to_recommendation():
    # The conftest factory always passes role explicitly, so this checks the
    # schema's own default rather than the factory's.
    assert Product.model_fields["role"].default == "recommendation"


# -- §4.0b: SurveyReport.comparison_specs must contain every Dimension.name --


def test_survey_report_rejects_comparison_specs_missing_a_dimension():
    with pytest.raises(ValidationError):
        make_survey_report(
            dimensions=[make_dimension(name="motor_count")],
            comparison_specs=["weight"],  # missing "motor_count"
        )


def test_survey_report_accepts_comparison_specs_covering_all_dimensions():
    sr = make_survey_report(
        dimensions=[make_dimension(name="motor_count")],
        comparison_specs=["weight", "motor_count"],
    )
    assert "motor_count" in sr.comparison_specs


# -- Dimension.separating_power ------------------------------------------------


def test_separating_power_counts_distinct_positions_among_survivors():
    d = make_dimension(splits={"a": "x", "b": "y", "c": "x"})
    assert d.separating_power({"a", "b", "c"}) == 2  # {x, y}
    assert d.separating_power({"a", "c"}) == 1  # {x, x} -> {x}
    assert d.separating_power({"z"}) == 0  # not present in splits


# -- RunRecord round-trips as a whole ------------------------------------------


def test_run_record_factory_builds_a_valid_record():
    record = make_run_record()
    assert record.verdict.action == "BUY"
    assert record.scores[0].product_name == "Widget Pro"
    # confidence lives once, on Product.evidence — not duplicated on Scored.
    assert not hasattr(record.scores[0], "confidence")


def test_run_record_round_trips_through_json():
    record = make_run_record()
    restored = record.model_validate_json(record.model_dump_json())
    assert restored == record
