"""Phase 6a — SCORING (spec docs/handoff.md §1/§5.1/§5.2/§6, build order
step 9).

One Opus pass scores every already-extracted `Product` holistically
(judgment — §6.3, never a formula), assigns each one's final `role` and
`strength_archetype`, and — for products the evidence can actually support
one on — re-judges the score at three discounted prices so a flip point can
be interpolated. `tools=[]` (§3's own `PHASES` sample): this phase reasons
over data the run record already holds; it does no further research.

This module owns four things, mirroring `phases/refine.py`'s own shape
(seam / pure runtime logic / SDK adapter):

1. `Scorer` — the seam through which the actual judgment call happens.
2. The §5.1/§5.2 constraint checks and the flip-point interpolation — pure
   functions, no I/O.
3. `_demote_for_row_cap` — §5.1's prior-gen demotion rule, added build
   order step 10 once `phases/prior_gen.py` existed to make it fireable.
4. `run_scoring` — the two-re-prompt-bound state machine built on top of
   the seam, with the row-cap demotion applied once scoring settles.

### What "§5.1 constraints enforced in code" means in THIS build step
### (step 9) and what step 10 added

§5.1 has two distinct kinds of content:

1. **The row-count formula** (floor 6 / soft 8 / hard cap 12, backfill,
   prior-gen demotion when admitting a row would exceed 12). Step 9 (before
   `phases/prior_gen.py` existed) deferred all of this — there was no
   prior-gen row that could ever trigger the demotion clause, and no larger
   candidate pool to backfill the floor from. Step 10 (this module's
   current state) closes ONE piece of it: `_demote_for_row_cap` enforces
   the literal hard-cap-plus-demotion sentence now that Phase 5 can
   actually produce a prior-generation row to admit. **The floor (6) /
   soft target (8) / backfill-from-a-larger-pool machinery remains
   deferred** — see `_demote_for_row_cap`'s own docstring for exactly why
   that part still needs orchestrator-level candidate-pool decisions this
   module has no visibility into.

2. **The bulleted "Constraints, enforced in code after Opus returns" list**
   — every row has a con (already schema-guaranteed by `Product.cons`'s
   `min_length=1`, invariant 6; nothing to re-check here), at least 3
   in-budget rows with distinct archetypes (when 3 qualifying products
   exist), at least 1 above-budget standout (when one exists), and
   prior-generation rows (handled separately by `_demote_for_row_cap` above
   — a row-count question, not a re-promptable judgment call, which is why
   it isn't part of `_find_issues`). **This is what `run_scoring`'s
   re-prompt loop enforces**, bounded at two re-prompts, exactly as build
   order step 9 asked for. §5.2's round-number clustering check ("more
   than half land on a .0/.5 boundary") rides the same bound, per
   CLAUDE.md's step-9 line grouping the two under one enforcement loop.

### Counterfactual scores are unconditional; using them is not

`Scored.score_at_minus_10pct/20pct/30pct` are plain, non-optional floats in
the locked schema (`models.py`) — there is no "N/A" representation for a
product the evidence can't support a flip point for. So `Scorer.propose_scores`
asks Opus for all three counterfactual re-judgments on *every* product,
unconditionally: asking "what would you score this at 10/20/30% less" is a
well-formed question regardless of evidence strength, even when the answer
ultimately goes unused. What varies is only whether Python *uses* those
three numbers to produce a `flip_point_amount` — gated by
`confidence.flip_point_eligible()`, `low_evidence_mode`, the product's
`PricingModel.model_type`, and (for `one_time_plus_subscription`) the
upfront-share gate — exactly mirroring the same four gates
`render/report.py`'s `_resolve_flip_point` re-applies at render time as
defense in depth. `_compute_flip_point` below is where eligible samples
become an interpolated `flip_point_amount`, or an ineligible/out-of-range
product gets `flip_point_amount=None` and a `flip_point_note` explaining
why — never a guess passed off as one.

### Why `rationale` here is short, and where the real write-up lives

`Scored.rationale` is written by THIS phase for every product, but is
deliberately a compact scoring justification, not §5.3's two-to-three
sentence prose. `render/report.py` renders `Scored.rationale` verbatim as
the "written recommendations" paragraph for the top 2–3 picks — the locked
schema has no separate prose field for that section (`Verdict.reasoning`
is verdict-level, not per-product). Phase 6b SYNTHESIS (`phases/synthesis.py`,
this same build step) is where the top 2–3 picks' `rationale` gets
*upgraded* into that fuller prose, once 6a's own re-prompt loop has already
settled — precisely the reason §1 gives for splitting phase 6 at all
("gives §5.1's constraint enforcement something to re-prompt against
without regenerating the write-up"). Every other product's `rationale`
stays exactly what this phase wrote.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from pydantic import BaseModel, Field

from product_scout import config
from product_scout.confidence import confidence_band, flip_point_eligible
from product_scout.models import (
    IntakeAnswers,
    Product,
    ProductRole,
    Scored,
    SurveyReport,
    TopicAnswer,
)
from product_scout.skills import assert_skill_loaded

# §5.1: "two re-prompts maximum, then accept the best result and log the
# unmet constraint." Total calls made is at most this + 1 (the first,
# unprompted attempt).
SCORING_REPROMPT_MAX: int = 2

# §5.1: "at least 3 in-budget rows with distinct archetypes."
MIN_DIVERSE_ARCHETYPE_ROWS: int = 3

# §5.2: sampled discount percentages, in the order Scored's fields expect.
_SAMPLE_PCTS: tuple[float, float, float, float] = (0.0, 10.0, 20.0, 30.0)

# §5.2, by PricingModel.model_type — verbatim reasons, never a flip point.
_NO_FLIP_MODEL_TYPE_NOTE: dict[str, str] = {
    "subscription_only": "No flip point — no meaningful one-time discount on a subscription.",
    "usage_based": "No flip point — no stable price to discount.",
    "financed_major_purchase": "No flip point — price is negotiated and variable.",
}

_NO_FLIP_THIN_EVIDENCE = "No flip point — evidence too thin to price the comparison."
_NO_FLIP_LOW_EVIDENCE_MODE = "No flip point — evidence too thin to price the comparison."
_NO_FLIP_UPFRONT_SHARE = (
    "No flip point — the upfront price is too small a share of first-year "
    "cost for a discount to change this."
)
_NO_FLIP_ALREADY_LEADS = "Already at or above the #1 pick's score without any discount."
# §5.2: "the bound reads 'would need more than a 30% cut to the upfront
# price' — not 'a 30% discount,' which would imply the TCO moved by 30%
# when it did not." Hybrid pricing gets its own wording; everything else
# uses the plain form.
_NO_FLIP_OUT_OF_RANGE = "would need more than a 30% discount to overtake the top pick"
_NO_FLIP_OUT_OF_RANGE_HYBRID = (
    "would need more than a 30% cut to the upfront price to overtake the top pick"
)
_NO_FLIP_NO_UPFRONT = "No flip point — no upfront price to discount."


class RawProductScore(BaseModel):
    """One product's judgment, as Opus returns it. Field names/types mirror
    `Product`/`Scored` exactly so `_apply_raw_scores`/`_build_scored` are
    plain lookups, not translation layers."""

    product_name: str
    score: float = Field(ge=0.0, le=10.0)
    rationale: str
    role: ProductRole
    strength_archetype: str
    score_at_minus_10pct: float = Field(ge=0.0, le=10.0)
    score_at_minus_20pct: float = Field(ge=0.0, le=10.0)
    score_at_minus_30pct: float = Field(ge=0.0, le=10.0)


class RawScoring(BaseModel):
    scores: list[RawProductScore]


@runtime_checkable
class Scorer(Protocol):
    """Seam for Phase 6a's actual judgment call. `feedback` is `None` on the
    first attempt and the §5.1/§5.2 issue text on a re-prompt — see
    `run_scoring`."""

    async def propose_scores(
        self,
        products: list[Product],
        survey: SurveyReport,
        intake: IntakeAnswers,
        topics: list[TopicAnswer],
        low_evidence_mode: bool,
        feedback: str | None,
    ) -> RawScoring: ...


class ScoringOutcome(BaseModel):
    """Phase 6a's fully-resolved result. `products` carries Opus's `role`/
    `strength_archetype` judgments merged in (via `model_copy`, everything
    else on each `Product` untouched); `scores` is the final, possibly
    re-prompted `Scored` list with flip points already resolved."""

    products: list[Product]
    scores: list[Scored]
    caveats: list[str] = []


# ---------------------------------------------------------------------------
# §5.1/§5.2 issue detection — pure functions, no I/O.
# ---------------------------------------------------------------------------


def _is_round_number(score: float) -> bool:
    """True when `score` sits on a `.0`/`.5` boundary. Compares against the
    nearest half-point rather than `score % 0.5` directly — float modulo
    on values like `7.5` is not reliably exactly `0.0`."""
    return abs(score - round(score * 2) / 2) < 1e-9


def _round_number_issue(raw_scores: list[RawProductScore]) -> str | None:
    """§5.2: 'Warn and re-prompt when more than half land on a .0/.5
    boundary — that is bucketing, not discriminating.'"""
    if not raw_scores:
        return None
    on_boundary = sum(1 for r in raw_scores if _is_round_number(r.score))
    if on_boundary > len(raw_scores) / 2:
        return (
            f"{on_boundary} of {len(raw_scores)} scores land on a .0/.5 "
            "boundary — that reads as bucketing into tiers rather than "
            "discriminating between products (§5.2). Reconsider what "
            "specifically separates products currently tied at the same "
            "round number, and use the decimal place to say so."
        )
    return None


def _archetype_diversity_issue(
    products_by_name: dict[str, Product], raw_scores: list[RawProductScore]
) -> str | None:
    """§5.1: 'At least 3 in-budget rows with distinct archetypes — when 3
    qualifying products exist.' Reads `role`/`in_budget` from each raw
    score's OWN proposed role (not the pre-scoring `Product.role`) — Opus's
    role judgment is exactly what this constraint is checking."""
    qualifying = [
        r
        for r in raw_scores
        if r.role == "recommendation" and products_by_name[r.product_name].in_budget
    ]
    if len(qualifying) < MIN_DIVERSE_ARCHETYPE_ROWS:
        return None  # constraint only binds when enough candidates exist
    distinct = {r.strength_archetype for r in qualifying}
    if len(distinct) < MIN_DIVERSE_ARCHETYPE_ROWS:
        names = ", ".join(sorted(r.product_name for r in qualifying))
        return (
            f"{len(qualifying)} in-budget products ({names}) only span "
            f"{len(distinct)} distinct archetype(s) — §5.1 needs at least "
            f"{MIN_DIVERSE_ARCHETYPE_ROWS} distinct archetypes among "
            "in-budget recommendations when that many qualifying products "
            "exist. Differentiate the narrative reason to prefer each one, "
            "or explain plainly if this is genuinely a low-differentiation "
            "commodity set (§8.4) rather than inventing a distinction."
        )
    return None


def _above_budget_standout_issue(
    products_by_name: dict[str, Product], raw_scores: list[RawProductScore]
) -> str | None:
    """§5.1: 'At least 1 above-budget standout — when one exists. A
    generous budget in a cheap category has none, and that is not a
    failure.'"""
    above_budget_candidates = [
        r for r in raw_scores if not products_by_name[r.product_name].in_budget
    ]
    if not above_budget_candidates:
        return None
    if any(r.role == "recommendation" for r in above_budget_candidates):
        return None
    names = ", ".join(sorted(r.product_name for r in above_budget_candidates))
    return (
        f"{len(above_budget_candidates)} above-budget candidate(s) exist "
        f"({names}) but none were kept as a role=recommendation standout "
        "(§5.1). If one of them is genuinely worth showing as a "
        "recommendation despite the budget, keep it that way rather than "
        "demoting every above-budget product to a reference role."
    )


def _find_issues(products: list[Product], raw: RawScoring) -> list[str]:
    products_by_name = {p.name: p for p in products}
    issues = [
        _archetype_diversity_issue(products_by_name, raw.scores),
        _above_budget_standout_issue(products_by_name, raw.scores),
        _round_number_issue(raw.scores),
    ]
    return [issue for issue in issues if issue is not None]


def _format_feedback(issues: list[str]) -> str:
    bullet_list = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous scoring pass didn't satisfy every constraint. "
        f"Please reconsider and call again:\n{bullet_list}"
    )


# ---------------------------------------------------------------------------
# Flip-point interpolation — pure arithmetic over Opus's judgment, never a
# new judgment itself (invariant 4).
# ---------------------------------------------------------------------------


def _compute_flip_point(
    product: Product, raw: RawProductScore, top_score: float, low_evidence_mode: bool
) -> tuple[float | None, str | None]:
    """§5.2. `top_score` is the #1 pick's score — flip points are computed
    per-product against it, never pairwise. A product already at or above
    `top_score` at full price (including the #1 pick itself) needs no
    discount to compete, so it gets no flip point at all — distinct from
    the "outside the sampled range" case, which means the opposite: no
    discount within the sampled span is *enough*.

    `low_evidence_mode` is checked FIRST and unconditionally: "Low-evidence
    mode suppresses flip points entirely, independent of the threshold"
    (§5.2) — a separate rule from `flip_point_eligible()`'s confidence/
    review-count predicate, not a consequence of it (the §8.3 clamp caps
    confidence at 0.75, which sits *above* the 0.65 flip floor, so
    eligibility alone would not suppress a well-corroborated low-evidence
    product without this being its own explicit check).
    """
    if low_evidence_mode:
        return None, _NO_FLIP_LOW_EVIDENCE_MODE
    if flip_point_eligible(product) is False:
        return None, _NO_FLIP_THIN_EVIDENCE

    pricing = product.pricing
    if pricing.model_type in _NO_FLIP_MODEL_TYPE_NOTE:
        return None, _NO_FLIP_MODEL_TYPE_NOTE[pricing.model_type]

    if pricing.model_type == "one_time_plus_subscription":
        upfront, tco = pricing.upfront_amount, pricing.total_cost_1yr
        if upfront is None or tco is None or (upfront / tco) < 0.25:
            return None, _NO_FLIP_UPFRONT_SHARE

    basis = pricing.upfront_amount
    if basis is None:
        return None, _NO_FLIP_NO_UPFRONT

    if raw.score >= top_score:
        return None, _NO_FLIP_ALREADY_LEADS

    samples = (
        (_SAMPLE_PCTS[0], raw.score),
        (_SAMPLE_PCTS[1], raw.score_at_minus_10pct),
        (_SAMPLE_PCTS[2], raw.score_at_minus_20pct),
        (_SAMPLE_PCTS[3], raw.score_at_minus_30pct),
    )
    for (lo_pct, lo_score), (hi_pct, hi_score) in zip(samples, samples[1:]):
        if lo_score < top_score <= hi_score:
            frac = 0.0 if hi_score == lo_score else (top_score - lo_score) / (hi_score - lo_score)
            crossover_pct = lo_pct + frac * (hi_pct - lo_pct)
            amount = basis * (1.0 - crossover_pct / 100.0)
            return round(amount, 2), None

    if pricing.model_type == "one_time_plus_subscription":
        return None, _NO_FLIP_OUT_OF_RANGE_HYBRID
    return None, _NO_FLIP_OUT_OF_RANGE


def _top_pick_score(products_by_name: dict[str, Product], raw_scores: list[RawProductScore]) -> float:
    """The #1 pick's score — the reference every flip point is computed
    against (§5.1: 'not pairwise'). Prefers role=='recommendation' scores;
    falls back to the plain maximum in the degenerate case where nothing
    was kept as a recommendation at all (nothing left to flip a point
    against, but `_compute_flip_point` still needs *some* reference)."""
    recommendation_scores = [
        r.score for r in raw_scores if products_by_name[r.product_name].role == "recommendation"
    ]
    pool = recommendation_scores or [r.score for r in raw_scores]
    return max(pool)


def _apply_raw_scores(products: list[Product], raw: RawScoring) -> list[Product]:
    """Merge Opus's `role`/`strength_archetype` judgments into fresh
    `Product` copies. A product Opus didn't return a score for keeps its
    pre-scoring `role`/`strength_archetype` untouched — `run_scoring`
    itself decides whether that omission is otherwise a problem."""
    raw_by_name = {r.product_name: r for r in raw.scores}
    updated: list[Product] = []
    for product in products:
        r = raw_by_name.get(product.name)
        if r is None:
            updated.append(product)
            continue
        updated.append(
            product.model_copy(update={"role": r.role, "strength_archetype": r.strength_archetype})
        )
    return updated


def _build_scored(
    products: list[Product], raw: RawScoring, low_evidence_mode: bool
) -> list[Scored]:
    products_by_name = {p.name: p for p in products}
    # Use Opus's OWN role judgments (from `raw`), not `products_by_name`'s
    # (already merged, but reading straight off `raw` keeps this function
    # independent of `_apply_raw_scores` having run first).
    top_score = _top_pick_score(products_by_name, raw.scores)

    scores: list[Scored] = []
    for r in raw.scores:
        product = products_by_name.get(r.product_name)
        if product is None:
            continue  # Opus named a product outside the given set; ignore
        amount, note = _compute_flip_point(product, r, top_score, low_evidence_mode)
        scores.append(
            Scored(
                product_name=r.product_name,
                score=r.score,
                rationale=r.rationale,
                score_at_minus_10pct=r.score_at_minus_10pct,
                score_at_minus_20pct=r.score_at_minus_20pct,
                score_at_minus_30pct=r.score_at_minus_30pct,
                flip_point_amount=amount,
                flip_point_note=note,
            )
        )
    return scores


# §5.1: "hard stop at 12 rows." Only the hard cap + demotion half of §5.1's
# row-width rule is enforced here — see `_demote_for_row_cap`'s docstring
# for exactly what remains deferred and why.
ROW_CAP: int = 12


def _demote_for_row_cap(
    products: list[Product], scores: list[Scored]
) -> tuple[list[Product], list[str]]:
    """§5.1: 'Prior-generation rows... count toward row width. If admitting
    one would exceed 12 rows, the lowest-scoring current-gen row is
    demoted to role="reference_displaced", not discarded — the extraction
    is already paid for, and a reference row is more informative than a
    caveat explaining an absence.'

    Only the hard-cap/demotion half of §5.1's row-width rule is
    implemented here. The floor (6) / soft target (8) / backfill machinery
    is still explicitly deferred (see this module's own docstring from
    build order step 9) — it requires selecting *which* candidates get
    extracted from a larger pool than what's already in `products`, a
    decision this phase has no visibility into. This function only ever
    REMOVES rows from an already-too-wide set; it never adds any.

    Never demotes a `generation == "prior"` row itself — it is the newly
    admitted row the cap exists to make room for, per §12.3's promotion
    having already happened before scoring runs (`phases/prior_gen.py`).
    Never touches a row that isn't currently `role == "recommendation"` —
    "Reference rows... count toward none of the above" (§5.1), so there is
    nothing to demote there; demoting an already-reference row would be
    inventing width it never occupied. Stops (rather than looping forever)
    once no current-gen recommendation row remains to demote, leaving any
    residual overflow rather than touching a prior-gen row — an explicit,
    logged trade rather than a silent one.
    """
    scores_by_name = {s.product_name: s.score for s in scores}
    updated = list(products)
    caveats: list[str] = []

    def recommendation_count() -> int:
        return sum(1 for p in updated if p.role == "recommendation")

    while recommendation_count() > ROW_CAP:
        current_gen_candidates = [
            p for p in updated if p.role == "recommendation" and p.generation == "current"
        ]
        if not current_gen_candidates:
            break  # nothing left to demote without touching a prior-gen row
        lowest = min(current_gen_candidates, key=lambda p: scores_by_name.get(p.name, 0.0))
        updated = [
            p.model_copy(update={"role": "reference_displaced"}) if p.name == lowest.name else p
            for p in updated
        ]
        caveats.append(
            f'"{lowest.name}" demoted to a reference row (role=reference_displaced) — '
            "admitting a prior-generation row would otherwise have pushed the "
            "comparison table past its 12-row cap (§5.1). Not discarded: its "
            "research is still shown as a reference."
        )
    return updated, caveats


async def run_scoring(
    products: list[Product],
    survey: SurveyReport,
    intake: IntakeAnswers,
    topics: list[TopicAnswer],
    low_evidence_mode: bool,
    scorer: Scorer,
) -> ScoringOutcome:
    """Run Phase 6a end to end: score (one Opus call), check the §5.1/§5.2
    issues, re-prompt up to `SCORING_REPROMPT_MAX` times, accept whatever
    the last attempt produced, then apply §5.1's row-cap demotion
    (`_demote_for_row_cap`) — in that order, so demotion always sees the
    FINAL scores rather than an attempt a re-prompt later replaced.

    `low_evidence_mode` is threaded straight through to `_build_scored` —
    §5.2's flip-point suppression is unconditional and Python-owned, never
    something a re-prompt could talk Opus out of.

    Short-circuits to an empty result without calling the scorer when
    `products` is empty — mirrors `run_extraction`'s "an empty shortlist
    never spends a model call" precedent.
    """
    if not products:
        return ScoringOutcome(products=[], scores=[], caveats=[])

    feedback: str | None = None
    raw = RawScoring(scores=[])
    issues: list[str] = []
    for attempt in range(SCORING_REPROMPT_MAX + 1):
        raw = await scorer.propose_scores(products, survey, intake, topics, low_evidence_mode, feedback)
        issues = _find_issues(products, raw)
        if not issues or attempt == SCORING_REPROMPT_MAX:
            break
        feedback = _format_feedback(issues)

    caveats = [
        f"Unmet scoring constraint after {SCORING_REPROMPT_MAX} re-prompt(s): {issue}"
        for issue in issues
    ]

    updated_products = _apply_raw_scores(products, raw)
    scores = _build_scored(updated_products, raw, low_evidence_mode)
    updated_products, demotion_caveats = _demote_for_row_cap(updated_products, scores)
    caveats.extend(demotion_caveats)
    return ScoringOutcome(products=updated_products, scores=scores, caveats=caveats)


# ---------------------------------------------------------------------------
# SdkScorer — real Scorer. Not unit tested here (no ANTHROPIC_API_KEY in this
# suite, consistent with SdkSurveyor/SdkExtractor/SdkRefiner).
# ---------------------------------------------------------------------------

_UNPARSED = object()


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_scoring_json(text: str) -> dict | None:
    """Mirrors `survey.py`'s `_parse_survey_json`/`refine.py`'s
    `_parse_topic_list_json`: whole-message JSON first, then the outermost
    bracketed span, kept as a local self-contained copy per this codebase's
    existing per-phase-module convention."""
    text = text.strip()
    if not text:
        return None

    parsed = _try_json_loads(text)
    if parsed is _UNPARSED:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        parsed = _try_json_loads(text[start : end + 1])

    if parsed is _UNPARSED or not isinstance(parsed, dict):
        return None
    return parsed


def _product_summary(product: Product) -> dict:
    """What Opus needs to score a product — deliberately not the full
    `Product` dump (specs carry `SourcedValue` provenance noise irrelevant
    to judgment; §4.0d's derived `EvidenceProfile` is summarized as a band,
    never handed over raw, since a model reasoning about its own confidence
    band is harmless but reasoning about raw ratios risks re-deriving a
    number invariant 4 reserves for Python)."""
    return {
        "name": product.name,
        "brand": product.brand,
        "role": product.role,
        "cluster_key": product.cluster_key,
        "strength_archetype": product.strength_archetype,
        "in_budget": product.in_budget,
        "pricing": {
            "model_type": product.pricing.model_type,
            "upfront_amount": product.pricing.upfront_amount,
            "recurring_amount": product.pricing.recurring_amount,
            "recurring_period": product.pricing.recurring_period,
            "total_cost_1yr": product.pricing.total_cost_1yr,
            "currency": product.pricing.price_currency,
        },
        "specs": {k: v.value for k, v in product.specs.items()},
        "pros": product.pros,
        "cons": product.cons,
        "confidence_band": confidence_band(product.evidence.confidence),
    }


SCORING_PROMPT_TEMPLATE = """You are scout-scorer, judging already-researched \
products (category kind: {category_kind}) for a buyer. Score holistically \
— never a weighted formula — following your recommendation-logic skill \
exactly.

BUDGET: ceiling {budget_ceiling}, note: {budget_note}
LOW-EVIDENCE MODE: {low_evidence_mode}

BUYER'S ANSWERS (gates already filtered products; axes are soft weights, \
never filters):
{topics_json}

PRODUCTS:
{products_json}

For EACH product above, return: product_name, score (0-10, one decimal that \
actually discriminates), rationale (a compact justification, not the final \
write-up), role (one of recommendation / baseline_current / \
reference_above_budget / reference_unavailable / reference_displaced), \
strength_archetype (the narrative reason to prefer it), and your holistic \
re-judgment of score at 10%, 20%, and 30% lower price \
(score_at_minus_10pct/20pct/30pct) — answer these for every product \
regardless of how well-evidenced it is; whether they end up used is not \
your concern.

{feedback_block}
When you are done, your FINAL message must be, and contain nothing except, \
a single JSON object matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


class SdkScorer:
    """Real `Scorer` — calls Opus directly via the SDK, `tools=[]` (§3's own
    `PHASES` sample: judgment over already-gathered data, no fresh
    research). Not unit tested (see module docstring)."""

    def __init__(self, model: str = config.MODEL_OPUS) -> None:
        self._model = model

    async def propose_scores(
        self,
        products: list[Product],
        survey: SurveyReport,
        intake: IntakeAnswers,
        topics: list[TopicAnswer],
        low_evidence_mode: bool,
        feedback: str | None,
    ) -> RawScoring:
        # §3: fail loudly before spending anything if the recommendation
        # logic skill isn't there to be loaded.
        assert_skill_loaded(config.RECOMMENDATION_LOGIC_SKILL)

        prompt = SCORING_PROMPT_TEMPLATE.format(
            category_kind=survey.category_kind,
            budget_ceiling=intake.budget_ceiling if intake.budget_ceiling is not None else "none stated",
            budget_note=intake.budget_note or "no note given",
            low_evidence_mode=low_evidence_mode,
            topics_json=json.dumps(
                [
                    {
                        "topic": t.topic,
                        "gate_answer": t.gate_answer,
                        "axis_kind": t.axis_kind,
                        "axis_value": t.axis_value,
                        "free_text": t.free_text,
                    }
                    for t in topics
                ]
            ),
            products_json=json.dumps([_product_summary(p) for p in products]),
            feedback_block=(feedback + "\n\n") if feedback else "",
            schema=json.dumps(RawScoring.model_json_schema()),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=[],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            skills=[config.RECOMMENDATION_LOGIC_SKILL],
        )

        final_text = ""
        async for message in query(prompt=prompt, options=options):
            if not isinstance(message, AssistantMessage):
                continue
            content = message.content
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, TextBlock):
                    final_text = block.text  # keep overwriting; last wins

        parsed = _parse_scoring_json(final_text)
        if parsed is None:
            raise RuntimeError(
                "scout-scorer's final message did not contain a parseable "
                f"JSON object: {final_text!r}"
            )
        return RawScoring(**parsed)
