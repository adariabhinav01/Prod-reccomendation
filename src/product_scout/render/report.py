"""HTML report assembly (spec docs/handoff.md §5, build order step 6).

Five sections, in the exact order §5 specifies: comparison table (5.1),
recommendation scale (5.2, delegated to `render/scale.py`), written
recommendations (5.3), sources (5.4), data reliability caveats (5.5).
Three cross-cutting pieces render *before* those five: the low-evidence
banner (§8.2: "a banner above the table... not a footnote"), the verdict
block, which — per §6.1 / CLAUDE.md invariant 5 — renders unconditionally
on `Verdict.action`, never suppressed or reordered based on its value, so
the top picks stay visible even when the verdict is "don't buy," and (only
for `INSUFFICIENT_EVIDENCE`) the §8.3 resolution-path section.

Every model-derived string is passed through `html.escape()` before
interpolation — this becomes a local HTML file opened in a real browser, so
a product name, rationale, or spec value is untrusted input as far as this
module is concerned.

### SPEC GAP-FILL — §5.3 written recommendations, MVP

No dedicated free-text field for Opus-authored prose exists yet (that's
Phase 7 / build order step 7). `render_written_recommendations` reuses
`Verdict.reasoning` plus each top-pick's `Scored.rationale` as a stand-in.
This is flagged with an HTML *comment* (invisible to the reader) rather
than a visible disclaimer banner — a user-facing "this is fake" caveat in
what's meant to be a real report would be its own kind of misleading.

### SPEC GAP-FILL — §5.4 sources without a tier

`Product.review_sources` and `Product.price_source_url` are bare `str`
fields, not `SourcedValue`s, so neither carries a `source_tier` value on
the model. `review_sources` genuinely has no way to know its tier (could be
Tier 2/3, or Tier 5 in low-evidence mode) and is labeled "review source
(tier unknown)". `price_source_url` is different: §11 assigns it a tier by
definition — "Tier 4 — Retailer listings... price from Tier 4 with a
timestamp" — so it's labeled with `TIER_LABELS[4]` directly rather than
"tier unknown," even though the model doesn't carry the number explicitly.

### SPEC GAP-FILL — source URLs render as plain text, never `<a href>`

Not a correctness requirement — a citation link isn't a "buy link" by
itself. It's a deliberate style choice: a plain, inert, monospace-styled
URL string can't be mistaken for a call-to-action, which makes "no buy
links, no affiliate tagging, ever" visually unambiguous rather than a
matter of link styling. A future implementer could reasonably choose `<a>`
instead.

### SPEC GAP-FILL — §8.3's required resolution-path section

When `Verdict.action == "INSUFFICIENT_EVIDENCE"`, §8.3 requires the report
to add "a section naming what would actually resolve the question — a
specific community that would know, a retailer with a generous return
window, a spec the user could measure themselves." No dedicated field for
that content exists yet (it's Phase 7 authorship, not built until step 7).
`render_insufficient_evidence_resolution` renders the section structurally
— so §8.3's requirement that it *exist* is met now — using `Verdict.reasoning`
as its MVP content, the same reuse pattern as §5.3's written recommendations
above. Empty string for every other verdict action.

### Confidence display labels (§4.1)

`_confidence_label` reproduces §4.1's own display-band table (high
0.85-1.00, moderate 0.65-0.84, low 0.40-0.64, very low 0.00-0.39) verbatim
— "Display bands are derived for readability; the stored value is always
the float." Used only for presentation text; never written back onto the
model, and never confused with `FLIP_POINT_CONFIDENCE_FLOOR` (0.70), a
different threshold for a different purpose.
"""

from __future__ import annotations

from html import escape as _esc
from pathlib import Path

from product_scout.config import TIER_LABELS
from product_scout.models import Product, RunRecord, Scored
from product_scout.render import scale

_TEMPLATE_PATH = Path(__file__).resolve().parent / "template.html"


def _confidence_label(confidence: float) -> str:
    """§4.1's display bands, for prose/table presentation only."""
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.65:
        return "moderate"
    if confidence >= 0.40:
        return "low"
    return "very low"


def _pair_products_with_scores(
    products: list[Product], scores: list[Scored]
) -> list[tuple[Product, Scored]]:
    """Match by name — the only shared key between `Product` and `Scored`
    — sorted by score descending. A `Scored` with no matching `Product` is
    dropped defensively rather than raising (a Phase 7 data bug shouldn't
    crash rendering). Deliberately duplicated in `render/scale.py` rather
    than shared, since the two modules pair for different purposes (SVG
    layout vs. table rows) and the logic is a three-line dict lookup."""
    by_name = {s.product_name: s for s in scores}
    pairs = [(p, by_name[p.name]) for p in products if p.name in by_name]
    pairs.sort(key=lambda pair: pair[1].score, reverse=True)
    return pairs


def render_low_evidence_banner(run: RunRecord) -> str:
    """§8.2: leads the report, not a footnote. Empty string when the run
    isn't in low-evidence mode."""
    if not run.low_evidence_mode:
        return ""
    return (
        '<div class="banner banner-low-evidence">'
        "<strong>Low-evidence mode.</strong> Coverage: "
        f"{_esc(run.coverage.coverage)}. {_esc(run.coverage.notes)}"
        "</div>"
    )


def render_scope_shift_note(run: RunRecord) -> str:
    """§8.1a: the report says plainly when the researched category isn't
    the one the user first typed. Empty string otherwise."""
    if run.original_product_type is None:
        return ""
    return (
        '<p class="scope-shift-note">Originally asked about '
        f'"{_esc(run.original_product_type)}"; researched '
        f'"{_esc(run.product_type)}" instead.</p>'
    )


def render_verdict_block(run: RunRecord) -> str:
    """§6.1 / invariant 5: renders unconditionally on `action`'s value —
    never gated or hidden based on it."""
    verdict = run.verdict
    timing = (
        f'<p class="verdict-timing">{_esc(verdict.timing_note)}</p>'
        if verdict.timing_note
        else ""
    )
    return (
        '<section id="section-verdict"><h2>Verdict: '
        f"{_esc(verdict.action)}</h2>"
        f"<p>{_esc(verdict.reasoning)}</p>{timing}</section>"
    )


def render_insufficient_evidence_resolution(run: RunRecord) -> str:
    """§8.3: names what would actually resolve the question when the
    verdict is INSUFFICIENT_EVIDENCE. See module SPEC GAP-FILL — reuses
    `Verdict.reasoning` as MVP content until Phase 7 authors this
    dedicated. Empty string for every other verdict action."""
    if run.verdict.action != "INSUFFICIENT_EVIDENCE":
        return ""
    return (
        "<!-- SPEC GAP-FILL: §8.3 requires this section to name what would "
        "resolve the question (a specific community, a retailer's return "
        "window, a measurable spec); no dedicated field exists yet, so this "
        "reuses Verdict.reasoning as an MVP. -->"
        '<section id="section-resolution-path"><h2>What would resolve this</h2>'
        f"<p>{_esc(run.verdict.reasoning)}</p></section>"
    )


def render_comparison_table(run: RunRecord) -> str:
    """§5.1. Displays whatever `products`/`scores` it's given — row-count
    and archetype-distinctness constraints are enforced upstream (Phase 7),
    not re-validated here. Cons are always rendered, including for the
    top-ranked row (invariant 4: no product is exempt from having one)."""
    pairs = _pair_products_with_scores(run.products, run.scores)
    if not pairs:
        return (
            '<section id="section-table"><h2>Comparison table</h2>'
            "<p>No products to compare.</p></section>"
        )
    header = (
        "<tr><th>Name</th><th>Brand</th><th>Generation</th><th>Price</th>"
        "<th>Budget</th><th>Archetype</th><th>Score</th><th>Confidence</th>"
        "<th>Pros</th><th>Cons</th></tr>"
    )
    rows = []
    for product, scored in pairs:
        pros = "".join(f"<li>{_esc(p)}</li>" for p in product.pros)
        cons = "".join(f"<li>{_esc(c)}</li>" for c in product.cons)
        budget_label = "in budget" if product.in_budget else "above budget"
        rows.append(
            "<tr>"
            f"<td>{_esc(product.name)}</td>"
            f"<td>{_esc(product.brand)}</td>"
            f"<td>{_esc(product.generation)}</td>"
            f"<td>${product.price_usd:,.0f}</td>"
            f"<td>{budget_label}</td>"
            f"<td>{_esc(product.strength_archetype)}</td>"
            f"<td>{scored.score:.1f}</td>"
            f"<td>{scored.confidence:.2f} ({_confidence_label(scored.confidence)})</td>"
            f"<td><ul>{pros}</ul></td>"
            f"<td><ul>{cons}</ul></td>"
            "</tr>"
        )
    return (
        '<section id="section-table"><h2>Comparison table</h2>'
        f'<table class="comparison-table">{header}{"".join(rows)}</table>'
        "</section>"
    )


def render_recommendation_scale_section(run: RunRecord) -> str:
    """§5.2, delegated to `render/scale.py`."""
    svg = scale.render_scale_svg(run.products, run.scores)
    return f'<section id="section-scale"><h2>Recommendation scale</h2>{svg}</section>'


def render_written_recommendations(run: RunRecord) -> str:
    """§5.3 MVP — see module SPEC GAP-FILL. Low/very-low confidence on a
    top pick must be stated in the prose, not left to the scale's visual
    encoding (§5.3's own wording)."""
    pairs = _pair_products_with_scores(run.products, run.scores)
    top = pairs[:3]
    paragraphs = [
        "<!-- SPEC GAP-FILL: prose below reuses Verdict.reasoning + top-pick "
        "Scored.rationale as an MVP; real Phase 7 prose generation lands in "
        "build order step 7. -->",
        f'<p class="verdict-reasoning">{_esc(run.verdict.reasoning)}</p>',
    ]
    for product, scored in top:
        label = _confidence_label(scored.confidence)
        confidence_note = (
            f" Confidence here is {label} — treat this pick with that in mind."
            if label in ("low", "very low")
            else ""
        )
        paragraphs.append(
            f'<p class="pick-rationale">{_esc(product.name)} '
            f"({scored.score:.1f}/10): {_esc(scored.rationale)}"
            f"{confidence_note}</p>"
        )
    return (
        '<section id="section-written"><h2>Written recommendations</h2>'
        f'{"".join(paragraphs)}</section>'
    )


def render_sources_section(run: RunRecord) -> str:
    """§5.4: every URL used, grouped by product, with tier. Rendered as
    plain escaped text, never `<a href>` (see module SPEC GAP-FILL)."""
    blocks = []
    for product in run.products:
        items = []
        for spec_name, sv in product.specs.items():
            tier_label = TIER_LABELS.get(sv.source_tier, f"Tier {sv.source_tier}")
            items.append(
                f"<li>{_esc(spec_name)}: {_esc(sv.source_url)} ({_esc(tier_label)})</li>"
            )
        items.append(
            f"<li>Price: {_esc(product.price_source_url)} ({_esc(TIER_LABELS[4])})</li>"
        )
        for review_url in product.review_sources:
            items.append(f"<li>{_esc(review_url)} (review source (tier unknown))</li>")
        blocks.append(f"<h3>{_esc(product.name)}</h3><ul>{''.join(items)}</ul>")
    return (
        '<section id="section-sources"><h2>Sources</h2>'
        f'{"".join(blocks)}</section>'
    )


def render_caveats_section(run: RunRecord) -> str:
    """§5.5 — auto-derived from the data model (conflicting values,
    uncorroborated specs, price timestamps), plus `RunRecord.caveats`
    rendered verbatim: that list is already populated upstream (relaxation
    notes from probe.py, future no_preference/failed-fetch notes from later
    phases) — no re-detection needed here, just display."""
    items = []
    for product in run.products:
        for spec_name, sv in product.specs.items():
            if sv.conflicting_values:
                other_values = ", ".join(sv.conflicting_values)
                items.append(
                    f"<li>Sources disagree on {_esc(spec_name)} for "
                    f"{_esc(product.name)}: {_esc(sv.value)} vs "
                    f"{_esc(other_values)}.</li>"
                )
            if not sv.corroborated_by:
                items.append(
                    f"<li>{_esc(spec_name)} for {_esc(product.name)}: "
                    "single-source, unverified.</li>"
                )
        items.append(
            f"<li>{_esc(product.name)} price observed "
            f"{product.price_observed_at.isoformat()} at "
            f"${product.price_usd:,.0f} — prices move.</li>"
        )
    for note in run.caveats:
        items.append(f"<li>{_esc(note)}</li>")

    if not items:
        body = "<p>No caveats recorded.</p>"
    else:
        body = f"<ul>{''.join(items)}</ul>"
    return f'<section id="section-caveats"><h2>Data reliability caveats</h2>{body}</section>'


def render_report_html(run: RunRecord) -> str:
    """Assembles the full self-contained HTML document: low-evidence banner
    -> scope-shift note -> verdict -> the five §5 sections, in order."""
    body = "".join(
        [
            render_low_evidence_banner(run),
            render_scope_shift_note(run),
            render_verdict_block(run),
            render_insufficient_evidence_resolution(run),
            render_comparison_table(run),
            render_recommendation_scale_section(run),
            render_written_recommendations(run),
            render_sources_section(run),
            render_caveats_section(run),
        ]
    )
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    title = f"Product Scout — {run.product_type}"
    html_doc = template.replace("__REPORT_TITLE__", _esc(title))
    html_doc = html_doc.replace("__GENERATED_AT__", _esc(run.created_at.isoformat()))
    html_doc = html_doc.replace("__REPORT_BODY__", body)
    return html_doc


def write_report(run: RunRecord, path: Path) -> Path:
    """Thin wrapper mirroring `RunStore.save`'s write-then-return-path
    shape. Not wired into `RunStore` or any CLI in this step — that's later
    build-order work once an orchestrator exists."""
    path.write_text(render_report_html(run), encoding="utf-8")
    return path
