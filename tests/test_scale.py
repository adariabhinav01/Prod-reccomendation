"""Unit tests for render/scale.py — the 0-10 SVG scale, confidence bands,
the 0.15 floor, and near-tie grouping (§5.2, build order step 6)."""

from product_scout.render.scale import (
    NEAR_TIE_THRESHOLD,
    ScaleRow,
    band_half_width,
    build_scale_rows,
    format_price,
    render_scale_svg,
)
from tests.conftest import (
    make_evidence_profile,
    make_pricing_model,
    make_product,
    make_run_record,
    make_scored,
)


# -- band_half_width: §5.2's exact formula + the 0.15 floor ------------------


def test_band_half_width_floor_at_full_confidence():
    assert band_half_width(1.0) == 0.15


def test_band_half_width_floor_binds_above_09():
    # 1.5 * (1 - 0.9) == 0.15 exactly — the floor and the formula coincide
    # here, both give 0.15.
    assert band_half_width(0.9) == 0.15


def test_band_half_width_floor_binds_below_09():
    # Without the floor this would be 1.5 * (1 - 0.95) == 0.075.
    assert band_half_width(0.95) == 0.15


def test_band_half_width_formula_above_the_floor():
    assert band_half_width(0.5) == 0.75


def test_band_half_width_at_zero_confidence():
    assert band_half_width(0.0) == 1.5


# -- format_price -----------------------------------------------------------


def test_format_price_one_time():
    p = make_pricing_model(model_type="one_time", upfront_amount=199.0, price_currency="USD")
    assert format_price(p) == "199.00 USD"


def test_format_price_subscription_only_monthly():
    p = make_pricing_model(
        model_type="subscription_only",
        upfront_amount=None,
        recurring_amount=9.99,
        recurring_period="monthly",
        total_cost_1yr=None,
        price_currency="USD",
    )
    assert format_price(p) == "9.99 USD/mo"


def test_format_price_subscription_only_annual():
    p = make_pricing_model(
        model_type="subscription_only",
        upfront_amount=None,
        recurring_amount=99.0,
        recurring_period="annual",
        total_cost_1yr=None,
        price_currency="EUR",
    )
    assert format_price(p) == "99.00 EUR/yr"


def test_format_price_freemium_with_paid_tier():
    p = make_pricing_model(model_type="freemium", upfront_amount=49.0, price_currency="USD")
    assert format_price(p) == "49.00 USD (paid tier)"


def test_format_price_freemium_no_paid_tier():
    p = make_pricing_model(
        model_type="freemium", upfront_amount=None, total_cost_1yr=None, price_currency="USD"
    )
    assert format_price(p) == "Free tier available"


def test_format_price_usage_based_with_estimate():
    p = make_pricing_model(
        model_type="usage_based",
        upfront_amount=None,
        total_cost_1yr=None,
        recurring_amount=12.0,
        price_currency="USD",
    )
    assert "usage-based" in format_price(p)


def test_format_price_usage_based_no_estimate():
    p = make_pricing_model(
        model_type="usage_based",
        upfront_amount=None,
        total_cost_1yr=None,
        recurring_amount=None,
        price_currency="USD",
    )
    assert format_price(p) == "Usage-based pricing"


def test_format_price_hybrid_shows_tco():
    p = make_pricing_model(
        model_type="one_time_plus_subscription",
        upfront_amount=70.0,
        recurring_amount=6.0,
        recurring_period="monthly",
        total_cost_1yr=142.0,
        price_currency="USD",
    )
    assert format_price(p) == "142.00 USD (1yr TCO)"


def test_format_price_no_price_at_all():
    p = make_pricing_model(
        model_type="financed_major_purchase",
        upfront_amount=None,
        total_cost_1yr=None,
        price_currency="USD",
    )
    assert format_price(p) == "Price not determined"


# -- build_scale_rows: join + sort + near-tie grouping -----------------------


def test_build_scale_rows_sorted_descending_by_score():
    run = make_run_record(
        products=[make_product(name="A"), make_product(name="B")],
        scores=[make_scored(product_name="A", score=6.0), make_scored(product_name="B", score=8.0)],
    )
    rows = build_scale_rows(run)
    assert [r.product_name for r in rows] == ["B", "A"]


def test_build_scale_rows_skips_products_without_a_score():
    run = make_run_record(
        products=[make_product(name="A"), make_product(name="B")],
        scores=[make_scored(product_name="A", score=6.0)],
    )
    rows = build_scale_rows(run)
    assert [r.product_name for r in rows] == ["A"]


def test_build_scale_rows_near_tie_grouping():
    run = make_run_record(
        products=[make_product(name="A"), make_product(name="B"), make_product(name="C")],
        scores=[
            make_scored(product_name="A", score=8.2),
            make_scored(product_name="B", score=8.1),  # within 0.2 of A
            make_scored(product_name="C", score=6.0),  # not within 0.2 of B
        ],
    )
    rows = build_scale_rows(run)
    groups = {r.product_name: r.near_tie_group for r in rows}
    assert groups["A"] == groups["B"]
    assert groups["C"] != groups["B"]


def test_build_scale_rows_no_false_grouping_across_a_gap():
    assert NEAR_TIE_THRESHOLD == 0.2
    run = make_run_record(
        products=[make_product(name="A"), make_product(name="B")],
        scores=[
            make_scored(product_name="A", score=8.2),
            make_scored(product_name="B", score=7.9),  # clearly outside the 0.2 threshold
        ],
    )
    rows = build_scale_rows(run)
    groups = {r.product_name: r.near_tie_group for r in rows}
    assert groups["A"] != groups["B"]


def test_build_scale_rows_uses_confidence_band_and_half_width():
    product = make_product(name="A", evidence=make_evidence_profile(confidence=0.9))
    run = make_run_record(products=[product], scores=[make_scored(product_name="A", score=7.0)])
    row = build_scale_rows(run)[0]
    assert row.confidence_band == "high"
    assert row.band_half_width == band_half_width(0.9)


# -- render_scale_svg ---------------------------------------------------------


def test_render_scale_svg_empty_rows_renders_placeholder():
    svg = render_scale_svg([])
    assert svg.startswith("<svg")
    assert "No scored products to plot" in svg


def test_render_scale_svg_contains_expected_elements():
    rows = [
        ScaleRow(
            product_name="Widget Pro",
            price_display="199.00 USD",
            score=8.2,
            band_half_width=0.15,
            confidence_band="high",
            role="recommendation",
            near_tie_group=0,
        )
    ]
    svg = render_scale_svg(rows)
    assert svg.startswith("<svg")
    assert "Widget Pro" in svg
    assert "199.00 USD" in svg
    assert "8.2" in svg
    assert 'class="confidence-band"' in svg
    assert 'class="score-dot"' in svg
    assert "wider band = thinner evidence" in svg
    assert "confidence interval" in svg


def test_render_scale_svg_escapes_product_name():
    rows = [
        ScaleRow(
            product_name="A & <B>",
            price_display="1.00 USD",
            score=5.0,
            band_half_width=0.15,
            confidence_band="low",
            role="recommendation",
            near_tie_group=0,
        )
    ]
    svg = render_scale_svg(rows)
    assert "A &amp; &lt;B&gt;" in svg
    assert "<B>" not in svg


def test_render_scale_svg_near_tie_bracket_only_for_grouped_rows():
    grouped = [
        ScaleRow(
            product_name="A",
            price_display="1 USD",
            score=8.2,
            band_half_width=0.15,
            confidence_band="high",
            role="recommendation",
            near_tie_group=0,
        ),
        ScaleRow(
            product_name="B",
            price_display="1 USD",
            score=8.1,
            band_half_width=0.15,
            confidence_band="high",
            role="recommendation",
            near_tie_group=0,
        ),
    ]
    svg = render_scale_svg(grouped)
    assert "near-tie-bracket" in svg

    ungrouped = [ScaleRow(**{**grouped[0].model_dump(), "near_tie_group": 0})]
    svg_solo = render_scale_svg(ungrouped)
    assert "near-tie-bracket" not in svg_solo


def test_render_scale_svg_reference_row_gets_dashed_marker():
    rows = [
        ScaleRow(
            product_name="Old Model",
            price_display="150.00 USD",
            score=6.5,
            band_half_width=0.2,
            confidence_band="moderate",
            role="reference_displaced",
            near_tie_group=0,
        )
    ]
    svg = render_scale_svg(rows)
    assert "stroke-dasharray" in svg
    assert "reference displaced" in svg
