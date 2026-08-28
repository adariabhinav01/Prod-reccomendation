"""The renderer (spec docs/handoff.md §5, §8.3, §8.5, build order step 6).
Python only — no model calls (§1: "7 RENDER Python — HTML + JSON, no model
calls"). Takes a fully-formed `RunRecord` (in this build step, a hand-written
fixture; later, whatever Phase 6b produced) and turns it into a self-
contained local HTML file plus the JSON record.

### What this module does NOT do

- **Row selection / the 6–12 cap / archetype-diversity constraints (§5.1)**
  are Phase 6a's job, code-enforced *before* `RunRecord.products` exists in
  the shape the renderer sees (build order step 9, "constraint
  enforcement"). This module renders whatever `run.products` contains, in
  whatever order it chooses for legibility — it never drops, backfills, or
  re-selects rows.
- **Caveat *generation* (§5.5)** happens throughout the pipeline as each
  phase runs — `run.caveats` arrives here already populated, already
  class-collapsed (`Caveat.instance_count`). This module's job is *tiering*
  (§5.5 rules 1–2: decision-affecting inline, provenance collapsed) and
  *placement* (by `Caveat.anchor`), not generation.
- **Flip-point *computation*** (the interpolation itself, §5.2) is Phase
  6a's job. `Scored.flip_point_amount`/`flip_point_note` arrive pre-
  computed; this module only decides whether to *show* what's there — see
  `_resolve_flip_point` below, which is where `flip_point_eligible()` and
  §5.2's other two suppression rules (low-evidence mode, the upfront-share
  gate) are actually applied. They're stated in the spec as independent,
  equally-absolute rules about the same rendered element, and this is the
  first point in the build where any of them can actually be exercised
  (Phase 6a doesn't exist yet) — so all three are enforced here, not just
  the one the build-order line names, as a defense-in-depth reading of
  "never" language that appears nowhere else in the pipeline yet.

### Flip points are prose, not an SVG range

§5.2: "Render as a range, not a point marker with a connector line." The
schema stores one interpolated float (`Scored.flip_point_amount`) — no
bracket, no pair of endpoints — so there's nothing to draw a literal
graphical range *from* without the renderer re-deriving Phase 6a's own
interpolation, which would cross back into judgment territory (invariant
4). Resolution: flip points render as hedged prose ("would need to drop to
roughly $X..."), attached to the row as text. This satisfies the literal
rule (no marker, no connector line exists to misread as false precision)
without fabricating data the schema doesn't carry.
"""

from __future__ import annotations

from html import escape as _esc
from pathlib import Path
from string import Template

from product_scout.confidence import confidence_band, flip_point_eligible
from product_scout.location import SHIPS_FROM_UNCERTAIN_THRESHOLD
from product_scout.models import Caveat, Product, RunRecord, Scored
from product_scout.render.scale import build_scale_rows, format_price, render_scale_svg
from product_scout.render.units import convert_display_value

_TEMPLATE_PATH = Path(__file__).parent / "template.html"

_VERDICT_LABELS: dict[str, str] = {
    "BUY": "Buy",
    "WAIT": "Wait",
    "CONSIDER_CHEAPER_CATEGORY": "Consider a cheaper category",
    "KEEP_CURRENT": "Keep your current one",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence",
}

_ROLE_LABELS: dict[str, str] = {
    "recommendation": "",
    "baseline_current": "your current product",
    "reference_above_budget": "reference — above budget",
    "reference_unavailable": "reference — not available to you",
    "reference_displaced": "reference — displaced by a higher-scoring pick",
}

_NO_FLIP_THIN_EVIDENCE = "No flip point — evidence too thin to price the comparison."
_NO_FLIP_UPFRONT_SHARE = (
    "No flip point — the upfront price is too small a share of first-year "
    "cost for a discount to change this."
)
_NO_FLIP_MODEL_TYPE: dict[str, str] = {
    "subscription_only": "No flip point — no meaningful one-time discount on a subscription.",
    "usage_based": "No flip point — no stable price to discount.",
    "financed_major_purchase": "No flip point — price is negotiated and variable.",
}


# -- small shared helpers -----------------------------------------------


def _top_picks(run: RunRecord, limit: int = 3) -> list[tuple[Product, Scored]]:
    """§6.1/invariant 7: top 2-3 recommendation-role picks, shown regardless
    of `Verdict.action` — this function never looks at the verdict."""
    products_by_name = {p.name: p for p in run.products}
    pairs = [
        (products_by_name[s.product_name], s)
        for s in run.scores
        if s.product_name in products_by_name
        and products_by_name[s.product_name].role == "recommendation"
    ]
    pairs.sort(key=lambda pair: pair[1].score, reverse=True)
    return pairs[:limit]


def _resolve_flip_point(product: Product, scored: Scored, run: RunRecord) -> str:
    """§5.2's full suppression stack — see module docstring. Order matters
    only for which message the reader sees; the outcome (suppressed or not)
    is the same regardless of check order since these are independent
    gates, not a priority chain."""
    if run.low_evidence_mode:
        return _NO_FLIP_THIN_EVIDENCE
    if not flip_point_eligible(product):
        return _NO_FLIP_THIN_EVIDENCE

    pricing = product.pricing
    if pricing.model_type in _NO_FLIP_MODEL_TYPE:
        return _NO_FLIP_MODEL_TYPE[pricing.model_type]

    if pricing.model_type == "one_time_plus_subscription":
        upfront = pricing.upfront_amount
        tco = pricing.total_cost_1yr
        if upfront is None or tco is None or (upfront / tco) < 0.25:
            return _NO_FLIP_UPFRONT_SHARE

    if scored.flip_point_amount is not None:
        currency = pricing.price_currency
        share_note = (
            " (upfront price)" if pricing.model_type == "one_time_plus_subscription" else ""
        )
        return (
            f"Flip point: would need to drop to roughly "
            f"{scored.flip_point_amount:,.0f} {currency}{share_note} to "
            "overtake the top pick."
        )
    if scored.flip_point_note:
        return scored.flip_point_note
    return "No flip point reported."


def _split_caveats(caveats: list[Caveat]) -> tuple[list[Caveat], list[Caveat]]:
    """§5.5 rule 1/2. `run.caveats` is already class-collapsed
    (`Caveat.instance_count`) by whoever generated it — this only sorts by
    tier, it doesn't re-collapse."""
    decision = [c for c in caveats if c.tier == "decision_affecting"]
    provenance = [c for c in caveats if c.tier == "provenance"]
    return decision, provenance


def _caveats_by_anchor(
    caveats: list[Caveat], product_names: set[str]
) -> tuple[dict[str, list[Caveat]], list[Caveat]]:
    by_product: dict[str, list[Caveat]] = {}
    general: list[Caveat] = []
    for c in caveats:
        if c.anchor and c.anchor in product_names:
            by_product.setdefault(c.anchor, []).append(c)
        else:
            general.append(c)
    return by_product, general


def _caveat_line(c: Caveat) -> str:
    suffix = f" ({c.instance_count} instances)" if c.instance_count > 1 else ""
    return f"<li>{_esc(c.text)}{suffix}</li>"


# -- section renderers -----------------------------------------------------


def _render_banner(run: RunRecord) -> str:
    """§8.3: 'The report leads with the limitation — a banner above the
    table, not a footnote.' Renders only when `low_evidence_mode` is true —
    invariant 10's first checkable guard."""
    if not run.low_evidence_mode:
        return ""
    return (
        '<div class="banner low-evidence-banner">'
        "<strong>Low-evidence mode.</strong> This category didn't clear the "
        "standard evidence bar, so this run relaxed source and confidence "
        "requirements — every relaxation is logged in the notes section "
        "below. Confidence is capped, and flip points are suppressed "
        "entirely."
        "</div>"
    )


def _render_verdict_section(run: RunRecord) -> str:
    """Always visible (§5.6) regardless of `action` (invariant 7). When the
    verdict is `INSUFFICIENT_EVIDENCE`, §8.5 asks for a section naming what
    would resolve the question — there's no dedicated schema field for
    that, so it's rendered from `Verdict.reasoning`, which is where Opus's
    prose (including the resolution path) is expected to live."""
    label = _VERDICT_LABELS.get(run.verdict.action, run.verdict.action)
    css_class = "insufficient-evidence" if run.verdict.action == "INSUFFICIENT_EVIDENCE" else ""
    heading = (
        "What would resolve this" if run.verdict.action == "INSUFFICIENT_EVIDENCE" else "Verdict"
    )
    parts = [
        f'<section class="verdict {css_class}">',
        f"<h2>{heading}: {_esc(label)}</h2>",
        f"<p>{_esc(run.verdict.reasoning)}</p>",
    ]
    if run.verdict.timing_note:
        parts.append(f"<p><em>{_esc(run.verdict.timing_note)}</em></p>")
    parts.append("</section>")
    return "".join(parts)


def _render_written_recommendations(top_picks: list[tuple[Product, Scored]]) -> str:
    """§5.3: two to three paragraphs on the top picks, prose. `Scored.
    rationale` is that prose — one paragraph per pick, attributed."""
    if not top_picks:
        return ""
    paragraphs = []
    for product, scored in top_picks:
        band = confidence_band(product.evidence.confidence)
        band_note = (
            f' <span class="confidence-flag">(confidence: {band.replace("_", " ")})</span>'
            if band in ("low", "very_low")
            else ""
        )
        paragraphs.append(
            f"<p><strong>{_esc(product.name)}</strong> — {_esc(scored.rationale)}"
            f"{band_note}</p>"
        )
    return (
        '<section class="written-recommendations">'
        "<h2>Written recommendations</h2>"
        f"{''.join(paragraphs)}"
        "</section>"
    )


def _render_scale_section(run: RunRecord) -> str:
    rows = build_scale_rows(run)
    return (
        '<section class="scale">'
        "<h2>Recommendation scale</h2>"
        f"{render_scale_svg(rows)}"
        "</section>"
    )


def _render_spec_cell(product: Product, key: str, run: RunRecord) -> str:
    sourced = product.specs.get(key)
    if sourced is None:
        return '<td class="spec-missing">—</td>'
    display = convert_display_value(sourced.value, sourced.native_unit, run.units)
    flags = []
    if sourced.corroborated_by:
        flags.append('<span class="flag corroborated" title="Corroborated by another source">✓</span>')
    if sourced.conflicting_values:
        flags.append('<span class="flag conflict" title="Sources disagree on this value">⚠</span>')
    flags_html = f" {''.join(flags)}" if flags else ""
    return f"<td>{_esc(display)}{flags_html}</td>"


def _render_comparison_table(
    run: RunRecord, caveats_by_product: dict[str, list[Caveat]]
) -> str:
    """§5.1's core comparison table — always visible. Row selection/
    ordering here is purely for legibility (recommendation rows by score
    descending, reference rows after); it enforces none of §5.1's
    constraints, which are Phase 6a's job (see module docstring)."""
    specs_keys = run.survey.comparison_specs
    scores_by_name = {s.product_name: s for s in run.scores}

    def sort_key(p: Product) -> tuple[int, float]:
        is_reference = 0 if p.role == "recommendation" else 1
        score = scores_by_name[p.name].score if p.name in scores_by_name else -1.0
        return (is_reference, -score)

    products = sorted(run.products, key=sort_key)

    header_cells = "".join(f"<th>{_esc(k)}</th>" for k in specs_keys)
    header = (
        "<tr><th>Product</th><th>Price</th><th>Score</th><th>Archetype</th>"
        f"{header_cells}<th>Pros</th><th>Cons</th><th>Flip point</th></tr>"
    )

    body_rows = []
    for product in products:
        scored = scores_by_name.get(product.name)
        score_display = f"{scored.score:.1f}" if scored else "—"
        flip_display = _resolve_flip_point(product, scored, run) if scored else ""
        role_note = _ROLE_LABELS.get(product.role, product.role)
        role_html = f'<div class="role-note">{_esc(role_note)}</div>' if role_note else ""
        row_class = "reference-row" if product.role != "recommendation" else ""

        spec_cells = "".join(_render_spec_cell(product, key, run) for key in specs_keys)
        pros = "".join(f"<li>{_esc(p)}</li>" for p in product.pros)
        cons = "".join(f"<li>{_esc(c)}</li>" for c in product.cons)

        inline_caveats = caveats_by_product.get(product.name, [])
        caveat_html = (
            f'<ul class="inline-caveats">{"".join(_caveat_line(c) for c in inline_caveats)}</ul>'
            if inline_caveats
            else ""
        )

        body_rows.append(
            f'<tr class="{row_class}">'
            f"<td>{_esc(product.name)}<br><small>{_esc(product.brand)}</small>{role_html}"
            f"{caveat_html}</td>"
            f"<td>{_esc(format_price(product.pricing))}</td>"
            f"<td>{score_display}</td>"
            f"<td>{_esc(product.strength_archetype)}</td>"
            f"{spec_cells}"
            f"<td><ul>{pros}</ul></td>"
            f"<td><ul>{cons}</ul></td>"
            f"<td class=\"flip-point\">{_esc(flip_display)}</td>"
            "</tr>"
        )

    return (
        '<section class="comparison">'
        "<h2>Comparison</h2>"
        f'<table><thead>{header}</thead><tbody>{"".join(body_rows)}</tbody></table>'
        "</section>"
    )


def _render_availability_section(run: RunRecord) -> str:
    items = []
    for product in run.products:
        a = product.availability
        bits = [f"Sold in region: {'yes' if a.sold_in_region else 'no'}"]
        if a.ships_to_region is not None:
            bits.append(f"ships to region: {'yes' if a.ships_to_region else 'no'}")
        if a.ships_from:
            # §10.6: "Below 0.95, present the inference and mark it
            # uncertain. Only an explicit policy statement clears the
            # threshold" — said in words here, not just left to the raw
            # number, since a reader shouldn't have to know the threshold
            # to notice it wasn't cleared.
            certainty = (
                "confirmed"
                if a.ships_from_confidence >= SHIPS_FROM_UNCERTAIN_THRESHOLD
                else "uncertain"
            )
            bits.append(
                f"ships from: {_esc(a.ships_from)} ({certainty} — signal: "
                f"{a.ships_from_signal}, confidence {a.ships_from_confidence:.2f})"
            )
        if a.shipping_estimate_native is not None:
            bits.append(
                f"estimated shipping: {a.shipping_estimate_native:,.2f} "
                f"{product.pricing.price_currency} (estimate)"
            )
        if a.duty_estimate_native is not None:
            bits.append(
                f"estimated duty: {a.duty_estimate_native:,.2f} "
                f"{product.pricing.price_currency} (estimate)"
            )
        if a.landed_price_native is not None:
            bits.append(f"landed price: {a.landed_price_native:,.2f} {product.pricing.price_currency} (estimate)")
        for note in a.import_caveats:
            bits.append(_esc(note))
        items.append(f"<li><strong>{_esc(product.name)}</strong> — {'; '.join(bits)}</li>")
    body = f"<ul>{''.join(items)}</ul>" if items else "<p>No availability detail.</p>"
    return _details("Availability detail", body)


def _render_secondhand_section(run: RunRecord) -> str:
    """§12: 'Secondhand information must never dominate the page' — always
    collapsed, small."""
    factors = run.survey.secondhand_risk_factors
    if not factors:
        return ""
    items = "".join(
        f'<li>{_esc(sv.value)} — <a href="{_esc(sv.source_url)}">{_esc(sv.source_type)}</a></li>'
        for sv in factors
    )
    return _details("Secondhand / prior-generation risk factors", f"<ul>{items}</ul>")


def _render_sources_section(run: RunRecord) -> str:
    """§5.4, collapsed per §5.6 ('per-spec source lists')."""
    blocks = []
    for product in run.products:
        entries = []
        for key, sourced in product.specs.items():
            note = " (sentiment, not measurement)" if sourced.source_type == "community" else ""
            entries.append(
                f'<li>{_esc(key)}: <a href="{_esc(sourced.source_url)}">'
                f"{_esc(sourced.source_type)}</a>"
                f"{' — methodology stated' if sourced.has_stated_methodology else ''}{note}</li>"
            )
        for url in product.review_sources:
            entries.append(f'<li>review: <a href="{_esc(url)}">{_esc(url)}</a></li>')
        if entries:
            blocks.append(f"<h3>{_esc(product.name)}</h3><ul>{''.join(entries)}</ul>")
    body = "".join(blocks) if blocks else "<p>No sources recorded.</p>"
    return _details("Sources", body)


def _render_provenance_section(provenance_caveats: list[Caveat]) -> str:
    """§5.5 rule 2: provenance/assumption caveats collapse here."""
    if not provenance_caveats:
        return ""
    items = "".join(_caveat_line(c) for c in provenance_caveats)
    return _details("Provenance notes", f"<ul>{items}</ul>")


def _details(summary: str, body: str) -> str:
    return f"<details><summary>{_esc(summary)}</summary>{body}</details>"


# -- top-level entrypoints --------------------------------------------------


def render_html(run: RunRecord) -> str:
    """The whole self-contained report (§0). No model calls (§1) — pure
    function of an already-complete `RunRecord`."""
    decision_caveats, provenance_caveats = _split_caveats(run.caveats)
    product_names = {p.name for p in run.products}
    by_product, general_caveats = _caveats_by_anchor(decision_caveats, product_names)
    top_picks = _top_picks(run)

    general_caveats_html = (
        f'<section class="general-caveats"><h2>Notes</h2><ul>'
        f'{"".join(_caveat_line(c) for c in general_caveats)}</ul></section>'
        if general_caveats
        else ""
    )

    body = "".join(
        [
            f"<h1>{_esc(run.product_type)}</h1>",
            _render_banner(run),
            _render_verdict_section(run),
            _render_written_recommendations(top_picks),
            _render_scale_section(run),
            _render_comparison_table(run, by_product),
            general_caveats_html,
            _render_availability_section(run),
            _render_secondhand_section(run),
            _render_sources_section(run),
            _render_provenance_section(provenance_caveats),
        ]
    )

    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(page_title=_esc(f"Product Scout — {run.product_type}"), body=body)


def render_json(run: RunRecord) -> str:
    return run.model_dump_json(indent=2)


def write_html_report(run: RunRecord, run_dir: Path) -> Path:
    """Writes `report.html` into `run_dir` (the layout `store/runs.py`
    already documents) and returns its path. Does not write `record.json` —
    that's `RunStore.save()`'s job; keeping this function pure I/O over
    already-rendered HTML avoids a second, redundant JSON-writing path."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.html"
    path.write_text(render_html(run), encoding="utf-8")
    return path
