"""The 0-10 recommendation scale (spec docs/handoff.md §5.2, build order
step 6). Inline SVG, hand-built as strings — no `svgwrite`/`xml.etree`
dependency; matches the string-template convention already used for prompts
in `phases/discovery.py`/`phases/extraction.py`, and keeps this package
dependency-free (see `pyproject.toml`: only `claude-agent-sdk`,
`python-dotenv`, `pydantic` are installed).

Everything here is a pure function of already-validated `Product`/`Scored`
objects — no file I/O, no model call. `render/report.py` is the only caller.

### The three things §5.2 requires

1. **Datapoints.** Each product plots on the 0-10 axis, labeled with name,
   price, and score.
2. **Confidence bands.** "Render confidence as a horizontal band around each
   datapoint, with the half-width a continuous function of the confidence
   float": `band_half_width()` below is that exact formula, verbatim from
   the spec. The band is explicitly "a legibility device, not a statistic —
   never present it as a confidence interval or attach a probability to
   it" — so `BAND_LEGEND_TEXT` renders only the spec's plain wording, never
   the words "interval" or "probability" (a test guards this).
3. **Flip points**, suppressed below `FLIP_POINT_CONFIDENCE_FLOOR` (0.70,
   `models.py`). "Render as a ghosted marker with a connector to the
   current price" when present; "show the reason inline... 'No flip point —
   evidence too thin to price the comparison.'" when suppressed.

### SPEC GAP-FILL — where does a ghosted flip-point marker sit?

`Scored.flip_point_usd` is a *dollar* figure, but this scale's x-axis is a
0-10 *score*, not a price axis — there's no price scale to place a dollar
figure on. §5.2 doesn't specify pixel placement, only the concept ("the
price at which it would overtake the top pick"). Decision: place the ghost
marker at the **top pick's score position** (read as "this is where you'd
need to land to tie the leader"), draw the connector from the product's own
datapoint to that ghost position, and put the actual `$flip_point_usd`
figure in the adjacent text label. A future implementer with a two-axis
design could do better; this keeps the single-axis scale honest about what
it can and can't show geometrically.

### The #1 pick gets no flip-point element at all

§5.2 defines flip points only "for every product below the #1 pick," and
the fallback sentence is specifically about confidence suppression ("A
price at which a barely-evidenced product would overtake a well-tested one
is not a useful number"). The #1 pick's `flip_point_usd` is also `None`,
but for an unrelated reason — there's nothing above it to flip to — and
showing the "evidence too thin" sentence there would assert a false reason
whenever the #1 pick is itself well-evidenced. `render_scale_svg` therefore
never calls `render_flip_point` for the top-ranked row; the fallback text
is reserved for sub-#1 rows whose `flip_point_usd` is `None`.

### SPEC GAP-FILL — no source URLs on this scale

Sources live only in §5.4 (`report.render_sources_section`). Keeping this
module free of any URL/link rendering avoids any appearance of a "buy"
affordance on the scale itself (invariant: "Buy links: None").
"""

from __future__ import annotations

from html import escape as _esc

from product_scout.models import Product, Scored

# ---------------------------------------------------------------------------
# Layout constants — pixel geometry is entirely unspecified by the spec
# (only the score-unit math is); these are the implementer's choice and are
# free to be retuned without touching the formula or suppression logic.

SCORE_MIN: float = 0.0
SCORE_MAX: float = 10.0

AXIS_LEFT_PX: int = 60
AXIS_RIGHT_PX: int = 480
AXIS_WIDTH_PX: int = AXIS_RIGHT_PX - AXIS_LEFT_PX
_PX_PER_SCORE_UNIT: float = AXIS_WIDTH_PX / (SCORE_MAX - SCORE_MIN)

DATAPOINT_LABEL_X: int = AXIS_RIGHT_PX + 20
FLIP_LABEL_X: int = AXIS_RIGHT_PX + 260
SVG_WIDTH_PX: int = AXIS_RIGHT_PX + 420

ROW_HEIGHT_PX: int = 64
TOP_MARGIN_PX: int = 50
LEGEND_HEIGHT_PX: int = 190
BAND_STROKE_PX: int = 10
MARKER_RADIUS_PX: int = 7
FLIP_MARKER_RADIUS_PX: int = 5

# §5.2's exact wording — rendered verbatim, never paraphrased into a
# "confidence interval" or "probability" framing.
NO_FLIP_POINT_TEXT: str = "No flip point — evidence too thin to price the comparison."
BAND_LEGEND_TEXT: str = "wider band = thinner evidence."

# §5.2's scoring anchors — "give Opus these verbatim; don't let it invent
# band meanings." Render verbatim here too, as a key under the scale.
SCORING_ANCHORS: list[tuple[float, float, str]] = [
    (9.0, 10.0, "Best fit. Buy this. No material reservation given the stated constraints."),
    (7.5, 8.9, "Strong. Recommend with one named tradeoff."),
    (6.0, 7.4, "Solid and defensible. Real compromises."),
    (4.5, 5.9, "Situational. Only right if one specific preference dominates."),
    (3.0, 4.4, "Weak. Better options exist at similar price."),
    (0.0, 2.9, "Do not recommend."),
]


def band_half_width(confidence: float) -> float:
    """§5.2, verbatim: 'band_half_width = round(2.0 * (1.0 - confidence), 2)
    # in score units'. Single source of truth — tests assert the spec's
    table (1.00->0.00, 0.85->0.30, 0.65->0.70, 0.40->1.20, 0.00->2.00)
    against this function directly."""
    return round(2.0 * (1.0 - confidence), 2)


def _score_to_x(score: float) -> float:
    """Map a 0-10 score to a pixel x-coordinate on the axis, clamped."""
    clamped = min(max(score, SCORE_MIN), SCORE_MAX)
    return AXIS_LEFT_PX + (clamped - SCORE_MIN) * _PX_PER_SCORE_UNIT


def render_axis(y: float) -> str:
    """Baseline line plus integer tick marks/labels for 0..10."""
    parts = [
        f'<line x1="{AXIS_LEFT_PX}" y1="{y}" x2="{AXIS_RIGHT_PX}" y2="{y}" '
        f'class="scale-axis" />'
    ]
    for tick in range(int(SCORE_MIN), int(SCORE_MAX) + 1):
        x = _score_to_x(float(tick))
        parts.append(
            f'<line x1="{x}" y1="{y - 4}" x2="{x}" y2="{y + 4}" class="scale-tick" />'
        )
        parts.append(
            f'<text x="{x}" y="{y - 10}" class="scale-tick-label" '
            f'text-anchor="middle">{tick}</text>'
        )
    return "".join(parts)


def render_datapoint(scored: Scored, product: Product, y: float, *, is_top_pick: bool) -> str:
    """One row's confidence band + marker + label. Score (a model judgment)
    and confidence (a computed float) are rendered as visibly distinct
    elements — a band (confidence) around a point (score) — never collapsed
    into one number (invariant 3)."""
    x = _score_to_x(scored.score)
    half_width_px = band_half_width(scored.confidence) * _PX_PER_SCORE_UNIT
    band_left = max(AXIS_LEFT_PX, x - half_width_px)
    band_right = min(AXIS_RIGHT_PX, x + half_width_px)

    marker_class = "datapoint-marker top-pick" if is_top_pick else "datapoint-marker"
    label = f"{product.name} — ${product.price_usd:,.0f} — {scored.score:.1f}"

    return (
        f'<rect x="{band_left}" y="{y - BAND_STROKE_PX / 2}" '
        f'width="{band_right - band_left}" height="{BAND_STROKE_PX}" '
        f'class="confidence-band" />'
        f'<circle cx="{x}" cy="{y}" r="{MARKER_RADIUS_PX}" class="{marker_class}" />'
        f'<text x="{DATAPOINT_LABEL_X}" y="{y + 4}" class="datapoint-label">'
        f"{_esc(label)}</text>"
    )


def render_flip_point(scored: Scored, y: float, top_pick_score: float) -> str:
    """Ghosted marker + connector when a flip point exists; the exact §5.2
    fallback sentence, verbatim, when it doesn't (see the module-level
    SPEC GAP-FILL note on why both suppression reasons render identically)."""
    if scored.flip_point_usd is None:
        return f'<text x="{FLIP_LABEL_X}" y="{y + 4}" class="flip-note">{_esc(NO_FLIP_POINT_TEXT)}</text>'

    current_x = _score_to_x(scored.score)
    ghost_x = _score_to_x(top_pick_score)
    label = f"${scored.flip_point_usd:,.0f} to tie the top pick"
    return (
        f'<line x1="{current_x}" y1="{y}" x2="{ghost_x}" y2="{y}" '
        f'class="flip-connector" stroke-dasharray="4,3" />'
        f'<circle cx="{ghost_x}" cy="{y}" r="{FLIP_MARKER_RADIUS_PX}" class="flip-ghost" />'
        f'<text x="{FLIP_LABEL_X}" y="{y + 4}" class="flip-label">{_esc(label)}</text>'
    )


def render_scoring_anchor_legend(x: float, y: float) -> str:
    """The six §5.2 scoring-anchor rows, rendered verbatim, plus the plain
    confidence-band legend line."""
    parts = []
    line_height = 20
    for i, (lo, hi, meaning) in enumerate(SCORING_ANCHORS):
        row_y = y + i * line_height
        text = f"{lo:.1f}–{hi:.1f}: {meaning}"
        parts.append(
            f'<text x="{x}" y="{row_y}" class="scoring-anchor">{_esc(text)}</text>'
        )
    band_legend_y = y + len(SCORING_ANCHORS) * line_height + line_height
    parts.append(
        f'<text x="{x}" y="{band_legend_y}" class="band-legend">'
        f"{_esc(BAND_LEGEND_TEXT)}</text>"
    )
    return "".join(parts)


def render_scale_svg(products: list[Product], scores: list[Scored]) -> str:
    """Top-level entry point: pairs `Scored.product_name` to `Product.name`
    (the only shared key in the data model), sorts by score descending, and
    assembles one complete `<svg>...</svg>` string — axis, one row per
    product (band + datapoint + flip point/fallback), then the legends.
    Scored entries with no matching Product are skipped defensively rather
    than raising — a name mismatch here is a Phase 7 data bug, not
    something the renderer should crash the whole report over."""
    by_name = {p.name: p for p in products}
    rows = [(s, by_name[s.product_name]) for s in scores if s.product_name in by_name]
    rows.sort(key=lambda pair: pair[0].score, reverse=True)

    height = TOP_MARGIN_PX + len(rows) * ROW_HEIGHT_PX + LEGEND_HEIGHT_PX
    top_pick_score = rows[0][0].score if rows else SCORE_MAX

    body_parts: list[str] = []
    for i, (scored, product) in enumerate(rows):
        y = TOP_MARGIN_PX + i * ROW_HEIGHT_PX + ROW_HEIGHT_PX / 2
        body_parts.append(render_axis(y) if i == 0 else "")
        body_parts.append(render_datapoint(scored, product, y, is_top_pick=(i == 0)))
        if i != 0:
            # Flip points are only meaningful below the #1 pick (§5.2) — see
            # the module docstring on why the top row gets no element here.
            body_parts.append(render_flip_point(scored, y, top_pick_score))

    legend_y = TOP_MARGIN_PX + len(rows) * ROW_HEIGHT_PX + 30
    body_parts.append(render_scoring_anchor_legend(AXIS_LEFT_PX, legend_y))

    body = "".join(body_parts)
    return (
        f'<svg viewBox="0 0 {SVG_WIDTH_PX} {height}" '
        f'width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Recommendation scale">{body}</svg>'
    )
