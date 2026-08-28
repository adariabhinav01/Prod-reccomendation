"""Confidence: computed, not judged (spec docs/handoff.md §4.1, v7).

`score` is a judgment, assigned by Opus, never reduced to a formula (§5.2).
`confidence` is a measurement — how much do we actually know? — computed
here, in Python. The model never assigns it. Judgments resist formulas;
measurements demand them.

Per §4.0d, `build_evidence_profile()` is the ONLY place an `EvidenceProfile`
is constructed — nothing else in this codebase should build one directly.
Everything else in this module (`compute_confidence`, `confidence_band`,
`flip_point_eligible`) is a pure function over already-derived data, which is
exactly why all of it is fully testable before a single model call exists
(build order step 1).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

from product_scout.models import EvidenceProfile, Product, SurveyReport, normalize_url

# §5.2 — the Moderate band's lower bound. Both flip_point_eligible clauses
# are load-bearing; see the function docstring.
FLIP_FLOOR: float = 0.65

# §8.3 — "Confidence clamped to 0.75... applied after compute_confidence(),
# both values recorded." Deliberately above FLIP_FLOOR (0.65): the clamp
# caps *display* confidence, not flip-point eligibility — a low-evidence
# product is separately, unconditionally denied a flip point regardless of
# where its confidence lands (§5.2, enforced in `phases/scoring.py`'s
# `_compute_flip_point`, not here). Conflating the two would make this one
# number do a job §5.2 explicitly assigns elsewhere.
LOW_EVIDENCE_CONFIDENCE_CLAMP: float = 0.75

# §4.1 — recency window in days, keyed by SurveyReport.category_kind.
# "An 18-month-old review still describes the same paddle" (physical) vs.
# "may describe a different product" (software_service / hybrid).
_RECENCY_WINDOW_DAYS: dict[str, int] = {
    "physical": 24 * 30,
    "software_service": 9 * 30,
    "hybrid": 9 * 30,
}


def review_credit(n: int) -> float:
    """Smooth diminishing returns. No cliff, no ceiling.

    Yields 0.33 / 0.55 / 0.70 / 0.80 / 0.87 / 0.91 for n = 1..6, approaching
    1.0 asymptotically. Replaces v3's `min(n, 4) / 4.0` hard cap, which made
    the 4th review worth +0.133 and the 5th through 20th worth nothing —
    piling well-covered products at identical breadth exactly where the
    table is most crowded.
    """
    return 1.0 - math.exp(-n / 2.5)


def compute_confidence(e: EvidenceProfile) -> float:
    """§4.1's exact mechanism. Only the weights are tunable later (§19.1);
    the multiplicative shape itself is load-bearing — see §4.1a's property
    tests, which assert the shape rather than point values.
    """
    # BREADTH — how much evidence exists. Nothing else compensates for absence.
    breadth = (
        0.20 * float(e.has_tier1_specs)
        + 0.25 * float(e.has_methodology_backed_source)
        + 0.55 * review_credit(e.independent_review_count)
    )

    # QUALITY — how good it is. A multiplier, never an additive floor: a
    # single-source product must not collect credit for conflict_ratio == 0
    # and recency_factor == 1.0, both trivially true when there's nothing to
    # disagree with or nothing to go stale.
    quality = (0.78 + 0.15 * e.corroboration_ratio + 0.07 * e.recency_factor) * (
        1.0 - 0.40 * e.conflict_ratio
    )

    return round(min(max(breadth * quality, 0.0), 1.0), 3)


def confidence_band(c: float) -> Literal["high", "moderate", "low", "very_low"]:
    """§4.2 — half-open on the upper edge, so the domain tiles with no gaps."""
    if c >= 0.80:
        return "high"
    if c >= 0.65:
        return "moderate"
    if c >= 0.35:
        return "low"
    return "very_low"


def flip_point_eligible(product: Product) -> bool:
    """§5.2's explicit two-clause predicate — NOT an emergent property of
    the confidence arithmetic.

    Both clauses are load-bearing. The `FLIP_FLOOR` clause is the Moderate
    band boundary. The two-review clause is stated separately because the
    best single-review profile (a manufacturer page and one independent
    review agreeing) computes to 0.631 — only 0.019 under the floor. Leaving
    this to emerge from that margin would mean §19.2's open question about
    the review-credit scale factor could erase the guarantee silently (at
    scale 2.2 the same profile reaches 0.651 and clears). As a predicate,
    the guarantee is immune to weight tuning entirely.

    Low-evidence mode suppresses flip points independent of this predicate
    (§5.2) — that rule is applied by the caller (Phase 6a), not here.
    """
    return (
        product.evidence.confidence >= FLIP_FLOOR
        and product.evidence.independent_review_count >= 2
    )


def build_evidence_profile(
    product: Product,
    survey: SurveyReport,
    *,
    low_evidence_mode: bool = False,
    now: datetime | None = None,
) -> EvidenceProfile:
    """§4.0d — the ONLY constructor for `EvidenceProfile`. Every field is
    derived from data the run record already holds: `product.specs`,
    `product.ownership_notes`, `product.review_sources`, and
    `survey.comparison_specs` / `survey.category_kind`.

    `low_evidence_mode` applies §8.3's clamp — `min(confidence,
    LOW_EVIDENCE_CONFIDENCE_CLAMP)` — AFTER `compute_confidence()`, never
    folded into the formula itself (that would make the clamp look like
    part of the measurement rather than a separate, disclosed ceiling on
    it). Defaults `False` rather than being required-with-no-default like
    `phases/tools/record_product.py`'s `location`/`low_evidence_mode`
    parameters: this is a widely-reusable pure function (mirrors `now`'s own
    optional, sensible default), not a phase-boundary seam where an
    unstated default would silently hide which mode a call ran in.

    **The clamp also fires per-product on a `moderate`-coverage run, even
    when `low_evidence_mode` (the RUN-level flag) is `False`.** §8.1's own
    table gives `moderate` a nuance the run-level flag can't carry: "Proceed;
    low-evidence for under-covered products only." `RunRecord.low_evidence_mode`
    is a single bool (`phases/survey.py`'s `run_survey` sets it `False` for
    every `moderate` result, unconditionally) — there is no schema field for
    "this one product is under-covered." §8.3 itself names exactly this case
    as the clamp's real justification: "It exists for the moderate coverage
    path, where a well-covered product sits inside a partially-low-evidence
    run." So this function checks `survey.coverage` directly, per product,
    independent of the flag: `coverage == "moderate"` and this PRODUCT's own
    `independent_review_count < 4` triggers the same clamp.

    `4` is `§8.1`'s own upper bound for "moderate" ("2-4 independent
    sources") — a CATEGORY-level aggregate, not a per-product figure, so
    there is no spec-given per-product number to use verbatim; anchoring to
    that row's own ceiling is the least arbitrary reading available
    (flagged here, not resolved silently). **`< 2` was considered and
    rejected as dead code**: `review_credit(n)` caps breadth at `0.6313`
    for `n == 1` regardless of every other factor maxing out, structurally
    below `LOW_EVIDENCE_CONFIDENCE_CLAMP` — a clamp gated on `< 2` could
    never actually fire, which would make this whole fix cosmetic. At
    `n == 2`, the ceiling is `0.7529` (barely clears); at `n == 3`,
    `0.8343` — `< 4` is the smallest threshold under which the clamp can
    meaningfully bind.

    Implementation notes on two genuinely underspecified corners of §4.0c
    (flagged here rather than resolved silently, per house style):

    **`independent_review_count`** is documented as "sources of type
    testing_outlet or aggregator" (§4.0c), but `Product.review_sources` is
    `list[str]` — plain URLs with no `source_type` tag. The reading used
    here: `review_sources` is, by construction, the list of testing_outlet/
    aggregator-type sources (community reviews live in `ownership_notes`
    instead, per §14's bounded-purpose rule; manufacturer/retailer sources
    live in `specs`/`pricing`, not `review_sources`). So
    `independent_review_count` is simply the distinct-URL count of
    `review_sources`. This is enforced by the extraction phase's contract
    (build order step 8), not by this function.

    **`recency_factor`**'s source set is specs + ownership_notes (the only
    places `observed_at` lives — `review_sources` is untimestamped). `now`
    is an injectable parameter, not `datetime.now()` called internally, so
    this stays a pure function for testing and so `rescore` recomputing it
    against the current date is an explicit, visible choice rather than
    hidden nondeterminism.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    comparison_specs = survey.comparison_specs
    extracted_keys = [k for k in comparison_specs if k in product.specs]
    extracted_spec_count = len(extracted_keys)

    corroborated_keys = [
        k
        for k in comparison_specs
        if k in product.specs and product.specs[k].corroborated_by
    ]
    conflicted_keys = [k for k in extracted_keys if product.specs[k].conflicting_values]

    corroboration_ratio = (
        len(corroborated_keys) / len(comparison_specs) if comparison_specs else 0.0
    )
    conflict_ratio = (
        len(conflicted_keys) / extracted_spec_count if extracted_spec_count else 0.0
    )

    timestamped_sources = list(product.specs.values()) + list(product.ownership_notes)

    source_urls = {normalize_url(sv.source_url) for sv in timestamped_sources}
    source_urls |= {normalize_url(u) for u in product.review_sources}
    source_count = len(source_urls)

    independent_review_count = len({normalize_url(u) for u in product.review_sources})

    has_tier1_specs = any(sv.source_type == "manufacturer" for sv in product.specs.values())
    has_methodology_backed_source = any(
        sv.has_stated_methodology for sv in timestamped_sources
    )

    window_days = _RECENCY_WINDOW_DAYS[survey.category_kind]
    if timestamped_sources:
        fresh = sum(
            1
            for sv in timestamped_sources
            if (now - sv.observed_at).days <= window_days
        )
        recency_factor = fresh / len(timestamped_sources)
    else:
        recency_factor = 0.0

    profile = EvidenceProfile(
        source_count=source_count,
        independent_review_count=independent_review_count,
        extracted_spec_count=extracted_spec_count,
        has_tier1_specs=has_tier1_specs,
        has_methodology_backed_source=has_methodology_backed_source,
        corroboration_ratio=corroboration_ratio,
        conflict_ratio=conflict_ratio,
        recency_factor=recency_factor,
        confidence=0.0,  # provisional; computed below and reassigned
        confidence_note="",  # provisional; filled in below
    )
    computed_confidence = compute_confidence(profile)
    # §8.3: clamp applied AFTER compute_confidence(), both values recorded
    # (in the note — EvidenceProfile.confidence is one field, per §4.0c's
    # locked schema, so there's no second numeric slot to hold the
    # unclamped figure). Rarely binds in practice — a typical low-evidence
    # profile computes well under 0.75.
    under_covered_in_moderate_run = survey.coverage == "moderate" and independent_review_count < 4
    if (low_evidence_mode or under_covered_in_moderate_run) and computed_confidence > LOW_EVIDENCE_CONFIDENCE_CLAMP:
        confidence = LOW_EVIDENCE_CONFIDENCE_CLAMP
        reason = (
            "low-evidence mode"
            if low_evidence_mode
            else "under-covered product in a moderate-coverage run (§8.1)"
        )
        clamp_note = f" (computed {computed_confidence:.3f}, clamped for {reason})"
    else:
        confidence = computed_confidence
        clamp_note = ""
    band = confidence_band(confidence)  # banded on the FINAL (possibly
    # clamped) value — the note must never claim a band the displayed
    # number doesn't actually sit in.
    profile.confidence = confidence
    profile.confidence_note = (
        f"{independent_review_count} independent review(s), "
        f"{'Tier 1 specs' if has_tier1_specs else 'no Tier 1 specs'}, "
        f"{'methodology-backed' if has_methodology_backed_source else 'no methodology-backed source'}, "
        f"{corroboration_ratio:.0%} corroborated, {conflict_ratio:.0%} conflicted "
        f"→ {band} confidence.{clamp_note}"
    )
    return profile
