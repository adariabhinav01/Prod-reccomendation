"""Unit tests for render/scale.py — the §5.2 SVG recommendation scale
(build order step 6)."""

import pytest

from product_scout.models import FLIP_POINT_CONFIDENCE_FLOOR
from product_scout.render import scale
from tests.conftest import make_product, make_scored


# -- band_half_width formula (§5.2's exact table) ---------------------------


@pytest.mark.parametrize(
    "confidence,expected",
    [
        (1.00, 0.00),
        (0.85, 0.30),
        (0.65, 0.70),
        (0.40, 1.20),
        (0.00, 2.00),
    ],
)
def test_band_half_width_matches_spec_table(confidence, expected):
    assert scale.band_half_width(confidence) == expected


# -- datapoint rendering -----------------------------------------------------


def test_render_scale_svg_includes_name_price_score_label():
    product = make_product(name="Widget Pro", price_usd=249.0)
    scored = make_scored(product_name="Widget Pro", score=8.2, confidence=0.8)
    svg = scale.render_scale_svg([product], [scored])
    assert "Widget Pro" in svg
    assert "$249" in svg
    assert "8.2" in svg


def test_svg_is_well_formed_root_element():
    product = make_product()
    scored = make_scored(product_name=product.name)
    svg = scale.render_scale_svg([product], [scored])
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_unmatched_scored_entries_are_skipped_defensively():
    product = make_product(name="Widget Pro")
    scored = make_scored(product_name="Nonexistent Product")
    svg = scale.render_scale_svg([product], [scored])
    assert "Nonexistent Product" not in svg


# -- flip points --------------------------------------------------------------


def test_flip_point_present_renders_ghost_marker_and_price():
    top = make_product(name="Top Pick")
    top_scored = make_scored(
        product_name="Top Pick", score=9.0, confidence=0.9, flip_point_usd=None
    )
    other = make_product(name="Runner Up")
    other_scored = make_scored(
        product_name="Runner Up", score=7.5, confidence=0.9, flip_point_usd=199.0
    )
    svg = scale.render_scale_svg([top, other], [top_scored, other_scored])
    assert "flip-ghost" in svg
    assert "flip-connector" in svg
    assert "$199" in svg


def test_flip_point_suppressed_by_low_confidence_renders_fallback_text():
    """The fallback text is reserved for a sub-#1 row whose flip point is
    None — needs a genuine #1 pick above it, otherwise the "thin evidence"
    product would itself be the top pick and get no flip-point element."""
    top = make_product(name="Top Pick")
    top_scored = make_scored(product_name="Top Pick", score=9.0, confidence=0.9)
    thin = make_product(name="Thin Evidence Product")
    thin_scored = make_scored(
        product_name="Thin Evidence Product",
        score=6.0,
        confidence=FLIP_POINT_CONFIDENCE_FLOOR - 0.05,
        flip_point_usd=None,
    )
    svg = scale.render_scale_svg([top, thin], [top_scored, thin_scored])
    assert scale.NO_FLIP_POINT_TEXT in svg


def test_top_pick_gets_no_flip_point_element_at_all():
    """§5.2 only defines flip points 'for every product below the #1
    pick' — the top row must get neither a ghost marker nor the fallback
    sentence, since that sentence specifically claims 'evidence too thin,'
    which would be false for a well-evidenced #1 pick."""
    product = make_product(name="Clear Winner")
    scored = make_scored(product_name="Clear Winner", confidence=0.95, flip_point_usd=None)
    svg = scale.render_scale_svg([product], [scored])
    assert scale.NO_FLIP_POINT_TEXT not in svg
    assert "flip-ghost" not in svg
    assert "flip-connector" not in svg


def test_no_flip_point_text_matches_spec_wording_verbatim():
    assert (
        scale.NO_FLIP_POINT_TEXT
        == "No flip point — evidence too thin to price the comparison."
    )


# -- scoring anchor legend ----------------------------------------------------


def test_scoring_anchor_legend_includes_all_six_bands_verbatim():
    product = make_product()
    scored = make_scored(product_name=product.name)
    svg = scale.render_scale_svg([product], [scored])
    for _lo, _hi, meaning in scale.SCORING_ANCHORS:
        assert meaning in svg


# -- band legend text ---------------------------------------------------------


def test_band_legend_present_and_never_framed_as_statistic():
    product = make_product()
    scored = make_scored(product_name=product.name)
    svg = scale.render_scale_svg([product], [scored])
    assert scale.BAND_LEGEND_TEXT in svg
    assert "interval" not in svg.lower()
    assert "probability" not in svg.lower()


# -- escaping -------------------------------------------------------------------


def test_malicious_product_name_is_escaped():
    product = make_product(name="Widget</text><script>alert(1)</script>")
    scored = make_scored(product_name=product.name)
    svg = scale.render_scale_svg([product], [scored])
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


# -- geometry sanity --------------------------------------------------------------


def test_score_to_x_is_monotonic_and_bounds_correctly():
    assert scale._score_to_x(0.0) == scale.AXIS_LEFT_PX
    assert scale._score_to_x(10.0) == scale.AXIS_RIGHT_PX
    assert scale._score_to_x(5.0) > scale._score_to_x(4.0)
