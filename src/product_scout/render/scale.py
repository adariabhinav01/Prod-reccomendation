"""The 0–10 recommendation scale (spec docs/handoff.md §5.2, build order
step 6). Inline SVG, no JS, no external assets — the whole report is a
self-contained local HTML file (§0).

Two things this module deliberately does NOT do, both flagged inline where
relevant:

- **Score is never recomputed here.** `Scored.score` is Opus's holistic
  judgment (§5.2, invariant 4) — this module only lays it out spatially.
- **Flip points are not drawn on this scale.** They're a price-axis concept
  (§5.2); this scale's domain is the 0–10 score axis. `render/report.py`
  renders flip points as a per-row text annotation instead — see that
  module's docstring for the full reasoning (the schema stores a single
  interpolated float, not a bracket, which rules out a literal graphical
  range here anyway).
"""

from __future__ import annotations

from html import escape as _esc
from itertools import groupby
from typing import Literal

from pydantic import BaseModel

from product_scout.confidence import confidence_band
from product_scout.models import PricingModel, Product, RunRecord, Scored

# §5.2: "When products fall within 0.2, Opus states the equivalence and
# why... Render near-ties as a visually grouped cluster."
NEAR_TIE_THRESHOLD: float = 0.2

# §5.2's band table, in descending score order — used only for the
# background zones; each product's own dot/band color comes from its
# confidence band (a different axis entirely), not this table.
_SCORE_BANDS: list[tuple[float, float, str, str]] = [
    (9.0, 10.0, "#1b7a3d", "Best fit"),
    (7.5, 9.0, "#4c9a5b", "Strong"),
    (6.0, 7.5, "#8aa53d", "Solid"),
    (4.5, 6.0, "#c9a227", "Situational"),
    (3.0, 4.5, "#c9702b", "Weak"),
    (0.0, 3.0, "#b6432c", "Do not recommend"),
]

# Confidence-band -> dot fill color. Deliberately desaturated/neutral next
# to the score-band strip above, so the two color scales (score quality vs.
# evidence strength) don't visually blur into one signal — legend spells
# out the distinction (§5.2: "never present it as a confidence interval").
_CONFIDENCE_COLOR: dict[str, str] = {
    "high": "#2f6f4f",
    "moderate": "#4f7f9f",
    "low": "#9f7f3f",
    "very_low": "#9f4f4f",
}


def band_half_width(confidence: float) -> float:
    """§5.2's exact rendering-calibration formula. `1.5` is the calibration
    coefficient; `0.15` is the floor — "a zero-width band at confidence ==
    1.0 renders as nothing and reads as a bug." Score units, not confidence
    units — this is why it lives here rather than in confidence.py, which
    only ever works in confidence-space (§4's domain, never §5's)."""
    return max(0.15, 1.5 * (1.0 - confidence))


def format_price(pricing: PricingModel) -> str:
    """The headline price shown next to a product's name — §5.2: "Each
    product plots with name, price, and score." No ISO currency symbol
    guessing (e.g. assuming `$` for every currency) — the code, not a
    locale table, so it's always correct rather than usually correct.

    §16: a `rescore`-overridden price must stay honest in the report — the
    number is user-asserted, not sourced. Every branch below funnels
    through one `base` computation so the `" (user-set)"` suffix applies
    wherever `price_overridden` is set, regardless of which branch produced
    the string."""
    currency = pricing.price_currency
    if pricing.model_type == "subscription_only" and pricing.recurring_amount is not None:
        period = "mo" if pricing.recurring_period == "monthly" else "yr"
        base = f"{pricing.recurring_amount:,.2f} {currency}/{period}"
    elif pricing.model_type == "usage_based":
        if pricing.recurring_amount is not None:
            base = f"~{pricing.recurring_amount:,.2f} {currency} (usage-based)"
        else:
            base = "Usage-based pricing"
    elif pricing.model_type == "freemium":
        if pricing.upfront_amount is not None:
            base = f"{pricing.upfront_amount:,.2f} {currency} (paid tier)"
        else:
            base = "Free tier available"
    elif pricing.model_type == "one_time_plus_subscription" and pricing.total_cost_1yr is not None:
        base = f"{pricing.total_cost_1yr:,.2f} {currency} (1yr TCO)"
    elif pricing.upfront_amount is not None:
        base = f"{pricing.upfront_amount:,.2f} {currency}"
    elif pricing.total_cost_1yr is not None:
        base = f"{pricing.total_cost_1yr:,.2f} {currency} (1yr est.)"
    else:
        return "Price not determined"

    return f"{base} (user-set)" if pricing.price_overridden else base


class ScaleRow(BaseModel):
    """One product's plotted position — display data only, joined from a
    `Product` + its `Scored` entry."""

    product_name: str
    price_display: str
    score: float
    band_half_width: float
    confidence_band: Literal["high", "moderate", "low", "very_low"]
    role: str
    near_tie_group: int


def _assign_near_tie_groups(sorted_scores: list[float]) -> list[int]:
    """Chain-linked grouping on a descending-sorted score list: adjacent
    scores within `NEAR_TIE_THRESHOLD` share a group. This is a *chain*
    (each member near its neighbor), not a mutual "all pairs within 0.2"
    guarantee — the natural reading of "when products fall within 0.2" for
    a *sorted* list, and how a visual cluster on a line actually reads."""
    groups: list[int] = []
    current = 0
    for i, score in enumerate(sorted_scores):
        if i > 0 and (sorted_scores[i - 1] - score) < NEAR_TIE_THRESHOLD:
            groups.append(current)
        else:
            if i > 0:
                current += 1
            groups.append(current)
    return groups


def build_scale_rows(run: RunRecord) -> list[ScaleRow]:
    """Join `RunRecord.products` to `RunRecord.scores` by product name and
    lay them out sorted by score descending (highest first — reads like a
    ranking, and is what makes near-tie chaining correct). Products with no
    matching `Scored` entry are silently skipped — nothing to plot without
    a score; `report.py`'s table doesn't depend on this list, so an
    unscored product still appears there."""
    products_by_name: dict[str, Product] = {p.name: p for p in run.products}
    pairs: list[tuple[Product, Scored]] = [
        (products_by_name[s.product_name], s)
        for s in run.scores
        if s.product_name in products_by_name
    ]
    pairs.sort(key=lambda pair: pair[1].score, reverse=True)

    groups = _assign_near_tie_groups([s.score for _, s in pairs])

    rows: list[ScaleRow] = []
    for (product, scored), group in zip(pairs, groups):
        rows.append(
            ScaleRow(
                product_name=product.name,
                price_display=format_price(product.pricing),
                score=scored.score,
                band_half_width=band_half_width(product.evidence.confidence),
                confidence_band=confidence_band(product.evidence.confidence),
                role=product.role,
                near_tie_group=group,
            )
        )
    return rows


# -- SVG geometry --------------------------------------------------------

_AXIS_X0 = 240.0
_AXIS_X1 = 720.0
_ROW_HEIGHT = 56.0
_HEADER_HEIGHT = 70.0
_LEGEND_HEIGHT = 56.0
_BAND_STRIP_Y = 40.0
_BAND_STRIP_HEIGHT = 10.0


def _x_for_score(score: float) -> float:
    clamped = min(max(score, 0.0), 10.0)
    return _AXIS_X0 + (clamped / 10.0) * (_AXIS_X1 - _AXIS_X0)


def _svg_score_band_strip() -> str:
    parts = []
    for lo, hi, color, _label in _SCORE_BANDS:
        x0 = _x_for_score(lo)
        x1 = _x_for_score(hi)
        parts.append(
            f'<rect x="{x0:.1f}" y="{_BAND_STRIP_Y:.1f}" '
            f'width="{(x1 - x0):.1f}" height="{_BAND_STRIP_HEIGHT:.1f}" '
            f'fill="{color}" opacity="0.55"/>'
        )
    return "".join(parts)


def _svg_axis(chart_bottom: float) -> str:
    parts = [
        f'<line x1="{_AXIS_X0:.1f}" y1="{chart_bottom:.1f}" '
        f'x2="{_AXIS_X1:.1f}" y2="{chart_bottom:.1f}" '
        f'stroke="#888" stroke-width="1"/>'
    ]
    for tick in (0.0, 2.5, 5.0, 7.5, 10.0):
        x = _x_for_score(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{chart_bottom:.1f}" '
            f'x2="{x:.1f}" y2="{(chart_bottom + 6):.1f}" stroke="#888"/>'
            f'<text x="{x:.1f}" y="{(chart_bottom + 20):.1f}" '
            f'font-size="11" text-anchor="middle" fill="#666">{tick:g}</text>'
        )
    return "".join(parts)


def _svg_row(row: ScaleRow, y: float) -> str:
    band_lo = _x_for_score(row.score - row.band_half_width)
    band_hi = _x_for_score(row.score + row.band_half_width)
    dot_x = _x_for_score(row.score)
    dot_color = _CONFIDENCE_COLOR[row.confidence_band]
    dash = "" if row.role == "recommendation" else ' stroke-dasharray="3,2"'

    name = _esc(row.product_name)
    price = _esc(row.price_display)
    role_note = "" if row.role == "recommendation" else f" ({_esc(row.role.replace('_', ' '))})"

    return (
        f'<g class="scale-row" data-product="{name}">'
        f'<text x="8" y="{(y + 4):.1f}" font-size="13" fill="#111">{name}{role_note}</text>'
        f'<text x="8" y="{(y + 20):.1f}" font-size="11" fill="#666">{price}</text>'
        f'<rect class="confidence-band" x="{band_lo:.1f}" y="{(y - 8):.1f}" '
        f'width="{(band_hi - band_lo):.1f}" height="16" rx="8" '
        f'fill="{dot_color}" opacity="0.22"/>'
        f'<circle class="score-dot" cx="{dot_x:.1f}" cy="{y:.1f}" r="5" '
        f'fill="{dot_color}" stroke="#222" stroke-width="1"{dash}/>'
        f'<text x="{(dot_x + 10):.1f}" y="{(y + 4):.1f}" font-size="12" '
        f'fill="#111">{row.score:.1f}</text>'
        f"</g>"
    )


def _svg_near_tie_brackets(rows: list[ScaleRow], row_y: dict[int, float]) -> str:
    """One vertical bracket per group with more than one member, just left
    of the axis — a visual grouping cue only; the *why* is Opus's prose
    (§5.2), never invented here."""
    parts = []
    bracket_x = _AXIS_X0 - 14
    for group_id, members in groupby(enumerate(rows), key=lambda pair: pair[1].near_tie_group):
        members = list(members)
        if len(members) < 2:
            continue
        ys = [row_y[i] for i, _ in members]
        y_top, y_bottom = min(ys), max(ys)
        parts.append(
            f'<path class="near-tie-bracket" '
            f'd="M{(bracket_x + 6):.1f},{y_top:.1f} '
            f"L{bracket_x:.1f},{y_top:.1f} "
            f"L{bracket_x:.1f},{y_bottom:.1f} "
            f'L{(bracket_x + 6):.1f},{y_bottom:.1f}" '
            f'stroke="#888" fill="none" stroke-width="1.5"/>'
        )
    return "".join(parts)


def render_scale_svg(rows: list[ScaleRow]) -> str:
    """The whole scale as one inline `<svg>` string — 0–10 horizontal,
    each product plotted with name, price, and score, a confidence band
    (§5.2's `band_half_width`) around each point, near-ties visually
    grouped, and a legend disclaiming the band as a legibility device, not
    a statistic. Empty `rows` renders a minimal placeholder SVG rather than
    raising — a run with nothing scored yet is a real, displayable state
    (e.g. `INSUFFICIENT_EVIDENCE`, §8.5), not a rendering error."""
    if not rows:
        return (
            '<svg viewBox="0 0 760 80" xmlns="http://www.w3.org/2000/svg" '
            'role="img" aria-label="Recommendation scale, no scored products">'
            '<text x="16" y="40" font-size="13" fill="#666">'
            "No scored products to plot.</text></svg>"
        )

    chart_bottom = _HEADER_HEIGHT + len(rows) * _ROW_HEIGHT
    total_height = chart_bottom + _LEGEND_HEIGHT + 30

    row_y = {i: _HEADER_HEIGHT + i * _ROW_HEIGHT + _ROW_HEIGHT / 2 for i in range(len(rows))}

    body = ["".join(_svg_row(row, row_y[i]) for i, row in enumerate(rows))]
    body.append(_svg_near_tie_brackets(rows, row_y))

    legend_y = chart_bottom + 40
    legend = (
        f'<text x="8" y="{legend_y:.1f}" font-size="11" fill="#666">'
        "Band width reflects evidence strength — wider band = thinner evidence. "
        "Not a confidence interval, no probability implied."
        "</text>"
    )

    return (
        f'<svg viewBox="0 0 760 {total_height:.1f}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Recommendation scale, 0 to 10">'
        f'<title>Recommendation scale</title>'
        f'{_svg_score_band_strip()}'
        f'{_svg_axis(chart_bottom)}'
        f'{"".join(body)}'
        f'{legend}'
        f"</svg>"
    )
