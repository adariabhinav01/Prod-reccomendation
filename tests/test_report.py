"""Unit tests for render/report.py — the renderer (§5, §8.3, §8.5, build
order step 6): banner, verdict framing, flip-point suppression, caveat
tiering, progressive disclosure, unit conversion at render."""

import json

from product_scout.models import Verdict
from product_scout.render.report import (
    _resolve_flip_point,
    _top_picks,
    render_html,
    render_json,
    write_html_report,
)
from tests.conftest import (
    make_availability,
    make_caveat,
    make_evidence_profile,
    make_pricing_model,
    make_product,
    make_run_record,
    make_scored,
    make_sourced_value,
    make_survey_report,
)


def _index(text: str, needle: str) -> int:
    i = text.find(needle)
    assert i != -1, f"expected to find {needle!r}"
    return i


# -- banner: §8.3 --------------------------------------------------------


def test_no_banner_when_not_low_evidence():
    run = make_run_record(low_evidence_mode=False)
    html = render_html(run)
    assert "low-evidence-banner" not in html


def test_banner_present_when_low_evidence():
    run = make_run_record(low_evidence_mode=True)
    html = render_html(run)
    assert "low-evidence-banner" in html
    assert "Low-evidence mode" in html


def test_banner_appears_before_comparison_table_not_a_footnote():
    run = make_run_record(low_evidence_mode=True)
    html = render_html(run)
    banner_idx = _index(html, "low-evidence-banner")
    table_idx = _index(html, '<section class="comparison">')
    assert banner_idx < table_idx


# -- verdict: always visible, INSUFFICIENT_EVIDENCE framing (§6.1, §8.5) ----


def test_verdict_section_always_present_for_buy():
    run = make_run_record()
    html = render_html(run)
    assert "Verdict: Buy" in html


def test_top_picks_shown_even_when_verdict_is_dont_buy():
    """Invariant 7 / §6.1: top picks render regardless of verdict.action."""
    product = make_product(name="Widget Pro", role="recommendation")
    scored = make_scored(product_name="Widget Pro", score=8.0)
    run = make_run_record(
        products=[product],
        scores=[scored],
        verdict=Verdict(action="KEEP_CURRENT", reasoning="Not worth the upgrade.", timing_note=None),
    )
    html = render_html(run)
    assert "written-recommendations" in html
    assert "Widget Pro" in html


def test_insufficient_evidence_gets_resolution_framing():
    run = make_run_record(
        verdict=Verdict(
            action="INSUFFICIENT_EVIDENCE",
            reasoning="Only three forum posts exist; need a retailer with a return window.",
            timing_note=None,
        )
    )
    html = render_html(run)
    assert "What would resolve this" in html
    assert "retailer with a return window" in html
    # Still ships the full report — comparison table isn't suppressed.
    assert '<section class="comparison">' in html


# -- flip point suppression: §5.2, all three gates --------------------------


def test_flip_point_suppressed_when_low_evidence_mode():
    product = make_product(evidence=make_evidence_profile(confidence=0.9))
    scored = make_scored(flip_point_amount=150.0)
    run = make_run_record(products=[product], low_evidence_mode=True)
    assert _resolve_flip_point(product, scored, run) == (
        "No flip point — evidence too thin to price the comparison."
    )


def test_flip_point_suppressed_when_ineligible_low_confidence():
    product = make_product(evidence=make_evidence_profile(confidence=0.3))
    scored = make_scored(flip_point_amount=150.0)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "too thin" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_when_ineligible_too_few_reviews():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9, independent_review_count=1)
    )
    scored = make_scored(flip_point_amount=150.0)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "too thin" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_for_subscription_only():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(model_type="subscription_only", upfront_amount=None),
    )
    scored = make_scored(flip_point_amount=None, flip_point_note=None)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "no meaningful one-time discount" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_for_usage_based():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(model_type="usage_based", upfront_amount=None),
    )
    scored = make_scored(flip_point_amount=None)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "no stable price" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_for_financed_major_purchase():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(model_type="financed_major_purchase", upfront_amount=None),
    )
    scored = make_scored(flip_point_amount=None)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "negotiated and variable" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_by_upfront_share_gate():
    # $70 upfront against $142 TCO — 49% share, ABOVE the 25% gate, so this
    # case should NOT be suppressed by the gate (sanity check the boundary
    # the next test actually exercises).
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=70.0,
            total_cost_1yr=142.0,
        ),
    )
    scored = make_scored(flip_point_amount=60.0)
    run = make_run_record(products=[product], low_evidence_mode=False)
    result = _resolve_flip_point(product, scored, run)
    assert "too small a share" not in result
    assert "60" in result


def test_flip_point_suppressed_by_upfront_share_gate_below_threshold():
    # $20 upfront against $142 TCO — 14% share, below the 25% gate.
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=20.0,
            total_cost_1yr=142.0,
        ),
    )
    scored = make_scored(flip_point_amount=15.0)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "too small a share" in _resolve_flip_point(product, scored, run)


def test_flip_point_suppressed_when_upfront_amount_is_none():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(
            model_type="one_time_plus_subscription",
            upfront_amount=None,
            total_cost_1yr=142.0,
        ),
    )
    scored = make_scored(flip_point_amount=None)
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert "too small a share" in _resolve_flip_point(product, scored, run)


def test_flip_point_amount_rendered_as_hedged_prose_when_eligible():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(model_type="one_time", price_currency="USD"),
    )
    scored = make_scored(flip_point_amount=179.0, flip_point_note=None)
    run = make_run_record(products=[product], low_evidence_mode=False)
    result = _resolve_flip_point(product, scored, run)
    assert "179" in result
    assert "USD" in result
    assert "roughly" in result  # hedged, not a bare precise figure


def test_flip_point_note_rendered_when_amount_is_none_but_eligible():
    product = make_product(
        evidence=make_evidence_profile(confidence=0.9),
        pricing=make_pricing_model(model_type="one_time"),
    )
    scored = make_scored(
        flip_point_amount=None,
        flip_point_note="would need more than a 30% discount to overtake",
    )
    run = make_run_record(products=[product], low_evidence_mode=False)
    assert _resolve_flip_point(product, scored, run) == scored.flip_point_note


# -- caveat tiering: §5.5 --------------------------------------------------


def test_decision_affecting_caveat_anchored_to_product_renders_inline():
    product = make_product(name="Widget Pro")
    caveat = make_caveat(
        tier="decision_affecting", text="Tax convention undetermined.", anchor="Widget Pro"
    )
    run = make_run_record(products=[product], scores=[make_scored(product_name="Widget Pro")], caveats=[caveat])
    html = render_html(run)
    row_idx = _index(html, "Widget Pro")
    caveat_idx = _index(html, "Tax convention undetermined.")
    table_idx = _index(html, '<section class="comparison">')
    # The caveat text appears after the comparison table opens (i.e. inside
    # the table, inline) rather than only in a collapsed section.
    assert caveat_idx > table_idx
    assert 'class="inline-caveats"' in html


def test_decision_affecting_caveat_with_no_matching_anchor_goes_to_notes():
    caveat = make_caveat(tier="decision_affecting", text="Run was truncated at phase 4.", anchor=None)
    run = make_run_record(caveats=[caveat])
    html = render_html(run)
    assert '<section class="general-caveats">' in html
    assert "Run was truncated at phase 4." in html


def test_provenance_caveat_collapses_into_disclosure_section():
    caveat = make_caveat(
        tier="provenance", text="Shipping origin inferred for 9 of 12 products.", anchor=None,
        instance_count=9,
    )
    run = make_run_record(caveats=[caveat])
    html = render_html(run)
    assert "<summary>Provenance notes</summary>" in html
    provenance_idx = _index(html, "Provenance notes")
    caveat_idx = _index(html, "Shipping origin inferred")
    assert caveat_idx > provenance_idx
    assert "(9 instances)" in html


def test_provenance_caveat_never_renders_inline_in_table():
    caveat = make_caveat(
        tier="provenance", text="Skill hash mismatch detected.", anchor="Widget Pro"
    )
    product = make_product(name="Widget Pro")
    run = make_run_record(
        products=[product], scores=[make_scored(product_name="Widget Pro")], caveats=[caveat]
    )
    html = render_html(run)
    # Provenance caveats are never placed by anchor into the table — only
    # decision_affecting ones are (§5.5 rule 1 vs rule 2).
    assert 'class="inline-caveats"' not in html
    assert "<summary>Provenance notes</summary>" in html


# -- progressive disclosure: §5.6 -------------------------------------------


def test_core_sections_are_not_inside_details():
    run = make_run_record()
    html = render_html(run)
    first_details = html.find("<details>")
    scale_idx = _index(html, 'class="scale"')
    table_idx = _index(html, 'class="comparison"')
    written_idx = _index(html, "written-recommendations")
    verdict_idx = _index(html, 'class="verdict')
    assert first_details == -1 or all(
        idx < first_details for idx in (scale_idx, table_idx, written_idx, verdict_idx)
    )


def test_availability_secondhand_sources_are_collapsed():
    product = make_product(
        name="Widget Pro",
        availability=make_availability(sold_in_region=True),
    )
    run = make_run_record(
        products=[product],
        scores=[make_scored(product_name="Widget Pro")],
        survey=make_survey_report(
            secondhand_risk_factors=[
                make_sourced_value(value="Batteries degrade after 2 years.")
            ]
        ),
    )
    html = render_html(run)
    assert "<summary>Availability detail</summary>" in html
    assert "<summary>Secondhand / prior-generation risk factors</summary>" in html
    assert "<summary>Sources</summary>" in html
    assert "Batteries degrade after 2 years." in html


def test_availability_detail_shows_shipping_duty_and_landed_price():
    """Build order step 11: shipping/duty estimates render (previously
    dropped entirely), and a sub-0.95 ships_from_confidence reads as
    'uncertain' in words, not just a bare number."""
    product = make_product(
        name="Cross-Border Widget",
        availability=make_availability(
            sold_in_region=True,
            ships_from="DE",
            ships_from_signal="cctld",
            ships_from_confidence=0.90,
            shipping_estimate_native=15.0,
            duty_estimate_native=5.0,
            landed_price_native=219.0,
        ),
    )
    run = make_run_record(
        products=[product], scores=[make_scored(product_name="Cross-Border Widget")]
    )
    html = render_html(run)
    assert "estimated shipping: 15.00" in html
    assert "estimated duty: 5.00" in html
    assert "landed price: 219.00" in html
    assert "uncertain" in html


def test_availability_detail_shows_confirmed_for_high_confidence_signal():
    product = make_product(
        name="Confirmed Widget",
        availability=make_availability(
            sold_in_region=True,
            ships_from="DE",
            ships_from_signal="shipping_policy",
            ships_from_confidence=0.98,
        ),
    )
    run = make_run_record(
        products=[product], scores=[make_scored(product_name="Confirmed Widget")]
    )
    html = render_html(run)
    assert "confirmed" in html


def test_secondhand_section_absent_when_no_risk_factors():
    run = make_run_record()  # default survey has secondhand_risk_factors=[]
    html = render_html(run)
    assert "Secondhand / prior-generation risk factors" not in html


# -- unit conversion at render: §10.5 ----------------------------------------


def test_spec_value_converted_when_units_mismatch_native():
    product = make_product(
        name="Widget Pro",
        specs={"weight": make_sourced_value(value="42 lb", native_unit="lb")},
    )
    run = make_run_record(
        products=[product],
        scores=[make_scored(product_name="Widget Pro")],
        units="metric",
        survey=make_survey_report(comparison_specs=["weight"], dimensions=[]),
    )
    html = render_html(run)
    assert "42 lb (19.1 kg)" in html


def test_spec_value_unchanged_when_units_match_native():
    product = make_product(
        name="Widget Pro",
        specs={"weight": make_sourced_value(value="42 lb", native_unit="lb")},
    )
    run = make_run_record(
        products=[product],
        scores=[make_scored(product_name="Widget Pro")],
        units="imperial",
        survey=make_survey_report(comparison_specs=["weight"], dimensions=[]),
    )
    html = render_html(run)
    assert "42 lb (19.1 kg)" not in html
    assert "42 lb" in html


# -- top picks helper ---------------------------------------------------------


def test_top_picks_excludes_reference_rows():
    rec = make_product(name="Rec", role="recommendation")
    ref = make_product(name="Ref", role="reference_above_budget")
    run = make_run_record(
        products=[rec, ref],
        scores=[
            make_scored(product_name="Rec", score=7.0),
            make_scored(product_name="Ref", score=9.5),
        ],
    )
    picks = _top_picks(run)
    assert [p.name for p, _ in picks] == ["Rec"]


def test_top_picks_limited_to_three_sorted_by_score():
    products = [make_product(name=f"P{i}") for i in range(5)]
    scores = [make_scored(product_name=f"P{i}", score=float(i)) for i in range(5)]
    run = make_run_record(products=products, scores=scores)
    picks = _top_picks(run)
    assert [p.name for p, _ in picks] == ["P4", "P3", "P2"]


# -- render_json / write_html_report ------------------------------------------


def test_render_json_round_trips_run_id():
    run = make_run_record()
    data = json.loads(render_json(run))
    assert data["run_id"] == run.run_id


def test_write_html_report_writes_file(tmp_path):
    run = make_run_record()
    path = write_html_report(run, tmp_path / "some-run")
    assert path.exists()
    assert path.name == "report.html"
    assert "<html" in path.read_text(encoding="utf-8")
