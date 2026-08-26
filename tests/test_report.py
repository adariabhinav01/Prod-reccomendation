"""Unit tests for render/report.py — the §5 output contract (build order
step 6)."""

from product_scout.models import CoverageReport, Verdict
from product_scout.render import report
from tests.conftest import make_product, make_run_record, make_scored, make_sourced_value


# -- section order ------------------------------------------------------------


def test_five_sections_appear_in_spec_order():
    run = make_run_record()
    html = report.render_report_html(run)
    ids = [
        "section-table",
        "section-scale",
        "section-written",
        "section-sources",
        "section-caveats",
    ]
    positions = [html.index(f'id="{i}"') for i in ids]
    assert positions == sorted(positions)


# -- low-evidence banner --------------------------------------------------------


def test_low_evidence_banner_present_when_enabled():
    run = make_run_record(
        low_evidence_mode=True,
        coverage=CoverageReport(
            estimated_product_count=2,
            independent_review_sources_found=1,
            has_methodology_backed_testing=False,
            coverage="sparse",
            notes="Thin coverage for this niche category.",
        ),
    )
    html = report.render_report_html(run)
    assert 'class="banner banner-low-evidence"' in html
    assert "Thin coverage for this niche category." in html


def test_low_evidence_banner_absent_when_disabled():
    run = make_run_record(low_evidence_mode=False)
    html = report.render_report_html(run)
    # The CSS rule for .banner-low-evidence is always present in the static
    # template <style> block; assert the *rendered element* is absent, not
    # the class-name substring (which the stylesheet always contains).
    assert 'class="banner banner-low-evidence"' not in html


# -- scope shift note ------------------------------------------------------------


def test_scope_shift_note_present_when_category_broadened():
    run = make_run_record(
        original_product_type="vintage film scanners",
        product_type="film scanners",
    )
    html = report.render_report_html(run)
    assert "vintage film scanners" in html
    assert 'class="scope-shift-note"' in html


def test_scope_shift_note_absent_when_not_broadened():
    run = make_run_record(original_product_type=None)
    html = report.render_report_html(run)
    # Same caveat as the low-evidence banner test: the CSS rule for
    # .scope-shift-note always exists in the static template.
    assert 'class="scope-shift-note"' not in html


# -- verdict shown regardless of ranking -----------------------------------------


def test_verdict_and_top_picks_render_even_for_insufficient_evidence():
    scored = make_scored(
        product_name="Widget Pro", rationale="Best available given thin coverage."
    )
    run = make_run_record(
        products=[make_product(name="Widget Pro")],
        scores=[scored],
        verdict=Verdict(
            action="INSUFFICIENT_EVIDENCE",
            reasoning="Coverage is too thin for a confident call.",
            timing_note=None,
        ),
    )
    html = report.render_report_html(run)
    assert "INSUFFICIENT_EVIDENCE" in html
    assert "Coverage is too thin for a confident call." in html
    # top picks / written recommendations must still render, regardless of
    # verdict action (§6.1 / invariant 5) — nothing is suppressed here.
    assert "Widget Pro" in html
    assert "Best available given thin coverage." in html


# -- §8.3 resolution-path section for INSUFFICIENT_EVIDENCE ---------------


def test_resolution_path_section_present_for_insufficient_evidence():
    run = make_run_record(
        verdict=Verdict(
            action="INSUFFICIENT_EVIDENCE",
            reasoning="Try r/standingdesks, or a retailer with a 90-day return window.",
            timing_note=None,
        ),
    )
    html = report.render_report_html(run)
    assert 'id="section-resolution-path"' in html
    assert "Try r/standingdesks, or a retailer with a 90-day return window." in html


def test_resolution_path_section_absent_for_other_verdicts():
    run = make_run_record(
        verdict=Verdict(action="BUY", reasoning="Clear winner.", timing_note=None)
    )
    html = report.render_report_html(run)
    assert 'id="section-resolution-path"' not in html


# -- cons always rendered ------------------------------------------------------


def test_every_products_cons_render_including_top_pick():
    top = make_product(name="Top Pick", cons=["Runs loud under load"])
    top_scored = make_scored(product_name="Top Pick", score=9.5, confidence=0.9)
    run = make_run_record(products=[top], scores=[top_scored])
    html = report.render_report_html(run)
    assert "Runs loud under load" in html


# -- sources grouped by product with tier labels ---------------------------------


def test_sources_grouped_by_product_with_tier_labels():
    product = make_product(
        name="Widget Pro",
        specs={
            "weight": make_sourced_value(
                source_url="https://mfr.example/spec", source_tier=1
            ),
            "durability": make_sourced_value(
                source_url="https://reviews.example/test", source_tier=3
            ),
        },
        price_source_url="https://retailer.example/widget-pro",
        review_sources=["https://forum.example/thread"],
    )
    scored = make_scored(product_name="Widget Pro")
    run = make_run_record(products=[product], scores=[scored])
    html = report.render_report_html(run)
    assert "https://mfr.example/spec" in html
    assert "Tier 1" in html
    assert "https://reviews.example/test" in html
    assert "Tier 3" in html
    assert "https://retailer.example/widget-pro" in html
    assert "Tier 4" in html  # price sources are always Tier 4 per §11
    assert "https://forum.example/thread" in html
    assert "review source (tier unknown)" in html


# -- caveats: auto-derived + passthrough -----------------------------------------


def test_caveats_include_conflicting_values_and_uncorroborated_specs():
    product = make_product(
        specs={
            "weight_capacity": make_sourced_value(
                value="176 lb",
                conflicting_values=["265 lb"],
                corroborated_by=[],
            ),
        },
    )
    scored = make_scored(product_name=product.name)
    run = make_run_record(products=[product], scores=[scored])
    html = report.render_report_html(run)
    assert "176 lb" in html
    assert "265 lb" in html
    assert "single-source, unverified" in html


def test_caveats_include_passthrough_run_record_caveats_verbatim():
    run = make_run_record(
        caveats=["Relaxed the archetype-distinctness requirement to 2."]
    )
    html = report.render_report_html(run)
    assert "Relaxed the archetype-distinctness requirement to 2." in html


# -- HTML injection / escaping ---------------------------------------------------


def test_malicious_rationale_and_reasoning_are_escaped():
    scored = make_scored(
        product_name="Widget Pro",
        rationale="<img src=x onerror=alert(1)>",
    )
    run = make_run_record(
        products=[make_product(name="Widget Pro")],
        scores=[scored],
        verdict=Verdict(
            action="BUY",
            reasoning="<img src=x onerror=alert(2)>",
            timing_note=None,
        ),
    )
    html = report.render_report_html(run)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


# -- self-contained output --------------------------------------------------------


def test_no_external_resource_tags_or_anchors():
    run = make_run_record()
    html = report.render_report_html(run)
    assert "<script" not in html
    assert "<link" not in html
    assert "<img" not in html
    assert "<a " not in html


def test_no_buy_or_affiliate_language():
    run = make_run_record()
    html = report.render_report_html(run).lower()
    assert "buy now" not in html
    assert "add to cart" not in html
    assert "affiliate" not in html


# -- write_report -----------------------------------------------------------------


def test_write_report_writes_matching_content(tmp_path):
    run = make_run_record()
    path = tmp_path / "report.html"
    returned = report.write_report(run, path)
    assert returned == path
    assert path.read_text(encoding="utf-8") == report.render_report_html(run)


# -- confidence and score never conflated ------------------------------------------


def test_score_and_confidence_render_as_distinct_table_cells():
    scored = make_scored(product_name="Widget Pro", score=8.2, confidence=0.8)
    run = make_run_record(products=[make_product(name="Widget Pro")], scores=[scored])
    html = report.render_report_html(run)
    assert "<td>8.2</td>" in html
    assert "0.80 (moderate)" in html
