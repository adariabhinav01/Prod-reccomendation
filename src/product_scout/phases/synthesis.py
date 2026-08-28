"""Phase 6b — SYNTHESIS (spec docs/handoff.md §1/§5.3/§6, build order
step 9).

One Opus pass computes `Verdict` independently of ranking (§6.1) and
upgrades the top 2–3 picks' `Scored.rationale` into the §5.3 prose
write-up. `tools=[]`, same as Phase 6a — this reasons over the already-
scored set Phase 6a (`phases/scoring.py`, this same build step) produced;
it does no further research and re-runs no scoring.

### Why this exists as a second phase rather than folding into 6a

§1 gives the reason directly: "Splitting also gives §5.1's constraint
enforcement something to re-prompt against without regenerating the
write-up." Phase 6a's own re-prompt loop (`run_scoring`) can iterate up to
three times against §5.1/§5.2 issues; none of those re-prompts should also
regenerate prose for products whose role/archetype may still change on the
next iteration. This phase only runs once 6a has already settled.

### Where the §5.3 write-up actually lives in the locked schema

`docs/handoff.md`'s §4.0c schema block (RunRecord and friends) has no
dedicated "written recommendations" field — the only per-product prose
slot is `Scored.rationale`, and `render/report.py`'s
`_render_written_recommendations` already renders it verbatim as that
section (see `phases/scoring.py`'s own module docstring for the parallel
note from the 6a side). So this phase's write-up job is literally: for the
top 2–3 `role=="recommendation"` picks by score, replace their
`Scored.rationale` with two-to-three sentences of real prose. Every other
product's `rationale` — written by Phase 6a as a compact scoring
justification — passes through untouched.

### `top pick` selection is a local, self-contained copy

`_select_top_picks` below mirrors `render/report.py`'s `_top_picks`
exactly (role=="recommendation", sorted by score descending, top 3) but is
not imported from there — phase modules in this codebase are each
self-contained (see `phases/survey.py`'s `_ask_yes_no`/`phases/scoring.py`'s
JSON-parsing helpers for the same precedent), and a phase importing from
`render` would additionally invert this codebase's dependency direction
(render depends on a finished `RunRecord`; phases produce the pieces of
one). Keeping it here also makes the invariant this module leans on
explicit and testable on its own: **picks are selected the same way
regardless of `Verdict.action`** — `_select_top_picks` takes no verdict
input at all, which is what makes invariant 7 ("top 2-3 picks are shown
even when the verdict is don't buy") actually hold rather than merely being
asserted in prose.

### `timing`/`low_evidence_mode` are required parameters with no default

Phase 4 TIMING (build order step 10) doesn't exist yet, so there is no real
producer for `TimingAssessment` in this build step. `run_synthesis` takes
one as a required parameter anyway, rather than defaulting it internally —
matching `phases/extraction.py`'s precedent for `ledger: FetchLedger`
("a required parameter with no default" until the real orchestrator wires
one in) — a caller before step 10 exists supplies a "no signal found"
placeholder explicitly, which keeps the fabrication visible at the call
site instead of buried in this module.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from pydantic import BaseModel

from product_scout import config
from product_scout.confidence import confidence_band
from product_scout.degraded_modes import is_commodity_category
from product_scout.models import (
    IntakeAnswers,
    Product,
    Scored,
    SurveyReport,
    TimingAssessment,
    Verdict,
    VerdictAction,
)
from product_scout.skills import assert_skill_loaded

# §5.3: "Two to three paragraphs on the top 2-3 picks."
TOP_PICK_LIMIT: int = 3


class RawSynthesis(BaseModel):
    verdict_action: VerdictAction
    verdict_reasoning: str
    verdict_timing_note: str | None
    # product_name -> two-to-three sentence prose, one entry per top pick
    # Opus was shown. A pick Opus omits keeps its Phase 6a rationale
    # untouched (see _apply_top_pick_rationales) rather than erroring —
    # an incomplete write-up is a real, loggable degradation, not a
    # reason to fail the whole phase.
    top_pick_rationales: dict[str, str] = {}


@runtime_checkable
class Synthesizer(Protocol):
    """Seam for Phase 6b's actual verdict + write-up call."""

    async def synthesize(
        self,
        products: list[Product],
        scores: list[Scored],
        survey: SurveyReport,
        intake: IntakeAnswers,
        timing: TimingAssessment,
        low_evidence_mode: bool,
        top_picks: list[Product],
    ) -> RawSynthesis: ...


class SynthesisOutcome(BaseModel):
    verdict: Verdict
    scores: list[Scored]  # top-pick rationale upgraded; everything else unchanged
    caveats: list[str] = []


_INSUFFICIENT_EVIDENCE_NO_PRODUCTS = (
    "No products were extracted for this run, so there is nothing to base "
    "a call on — this is the same INSUFFICIENT_EVIDENCE §8.5 describes for "
    "a run that can't support a confident recommendation, applied to the "
    "degenerate case of an empty shortlist."
)


def _select_top_picks(products: list[Product], scores: list[Scored]) -> list[Product]:
    """§6.1/invariant 7's selection, computed independently of
    `Verdict.action` — see module docstring. Mirrors
    `render/report.py`'s `_top_picks` exactly."""
    products_by_name = {p.name: p for p in products}
    pairs = [
        (products_by_name[s.product_name], s)
        for s in scores
        if s.product_name in products_by_name
        and products_by_name[s.product_name].role == "recommendation"
    ]
    pairs.sort(key=lambda pair: pair[1].score, reverse=True)
    return [product for product, _ in pairs[:TOP_PICK_LIMIT]]


def _apply_top_pick_rationales(
    scores: list[Scored], rationales: dict[str, str]
) -> list[Scored]:
    """Merge Opus's prose into fresh `Scored` copies, keyed by
    `product_name`. A name Opus returned that wasn't actually a top pick
    (or doesn't match any known product) is silently ignored — this
    function only ever upgrades entries already present in `scores`, never
    invents new ones."""
    if not rationales:
        return scores
    updated: list[Scored] = []
    for scored in scores:
        prose = rationales.get(scored.product_name)
        if prose:
            updated.append(scored.model_copy(update={"rationale": prose}))
        else:
            updated.append(scored)
    return updated


async def run_synthesis(
    products: list[Product],
    scores: list[Scored],
    survey: SurveyReport,
    intake: IntakeAnswers,
    timing: TimingAssessment,
    low_evidence_mode: bool,
    synthesizer: Synthesizer,
) -> SynthesisOutcome:
    """Run Phase 6b end to end: pick the top picks (pure Python, ranking-
    independent of the verdict about to be computed), call Opus once for
    the verdict + their write-up, merge the prose back into `scores`.

    Short-circuits to a code-computed `INSUFFICIENT_EVIDENCE` verdict
    without calling the synthesizer when `products` is empty — mirrors
    `run_extraction`/`run_scoring`'s "nothing to reason about, don't spend
    a call" precedent. This is a structural guard on a degenerate input,
    not Python overriding a judgment call §6 reserves for Opus — there is
    literally no product for a model to reason about.
    """
    if not products:
        return SynthesisOutcome(
            verdict=Verdict(
                action="INSUFFICIENT_EVIDENCE",
                reasoning=_INSUFFICIENT_EVIDENCE_NO_PRODUCTS,
                timing_note=None,
            ),
            scores=[],
            caveats=[],
        )

    top_picks = _select_top_picks(products, scores)
    raw = await synthesizer.synthesize(
        products, scores, survey, intake, timing, low_evidence_mode, top_picks
    )
    verdict = Verdict(
        action=raw.verdict_action,
        reasoning=raw.verdict_reasoning,
        timing_note=raw.verdict_timing_note,
    )
    updated_scores = _apply_top_pick_rationales(scores, raw.top_pick_rationales)

    caveats: list[str] = []
    missing = [p.name for p in top_picks if p.name not in raw.top_pick_rationales]
    if missing:
        caveats.append(
            "Write-up prose missing for top pick(s) "
            f"{', '.join(missing)}; the Phase 6a scoring rationale is shown "
            "for them instead."
        )

    return SynthesisOutcome(verdict=verdict, scores=updated_scores, caveats=caveats)


# ---------------------------------------------------------------------------
# SdkSynthesizer — real Synthesizer. Not unit tested here (no
# ANTHROPIC_API_KEY in this suite, consistent with every other real SDK
# adapter in this codebase).
# ---------------------------------------------------------------------------

_UNPARSED = object()


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_synthesis_json(text: str) -> dict | None:
    """Same whole-message-then-outermost-braces fallback as every other
    real adapter in this codebase (`survey.py`/`refine.py`/`scoring.py`)."""
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


def _score_summary(product: Product, scored: Scored | None) -> dict:
    """§5.3: 'When a top pick sits in the low or very_low band, say so in
    the prose.' Opus can't honor that instruction without seeing the band —
    `confidence_band` is included here for exactly that reason, read-only
    (invariant 4: this hands over an already-computed summary, never a raw
    ratio the model could use to back into setting confidence itself)."""
    return {
        "name": product.name,
        "role": product.role,
        "in_budget": product.in_budget,
        "score": scored.score if scored else None,
        "rationale": scored.rationale if scored else None,
        "confidence_band": confidence_band(product.evidence.confidence),
        "pros": product.pros,
        "cons": product.cons,
        "price": product.pricing.upfront_amount,
        "currency": product.pricing.price_currency,
    }


SYNTHESIS_PROMPT_TEMPLATE = """You are scout-synthesizer, for a buyer \
shopping (category kind: {category_kind}). Phase 6a has already scored \
every product below. Follow your recommendation-logic skill exactly.

BUDGET: ceiling {budget_ceiling}, note: {budget_note}
LOW-EVIDENCE MODE: {low_evidence_mode}
COMMODITY CATEGORY: {commodity_category} ({differentiation} differentiation, \
~{estimated_product_count} products found). When true, Phase 6a was not \
re-prompted for archetype diversity here — if the write-up is about a \
product from an undifferentiated set, say so plainly (§8.4: "these cluster \
into effectively two real options, not six") rather than writing as if a \
sharp distinction exists.

TIMING SIGNAL:
{timing_json}

ALL SCORED PRODUCTS (for verdict context — e.g. comparing a baseline_current \
row against the best upgrade for KEEP_CURRENT):
{all_scores_json}

TOP PICKS (write two-to-three sentences of real prose for EACH of these, \
keyed by exact product name — this is the only prose a reader sees per \
product, so argue the tradeoff, don't just restate the score; if a pick \
sits at low or very-low confidence, say so in the prose itself):
{top_picks_json}

Decide verdict_action (BUY / WAIT / CONSIDER_CHEAPER_CATEGORY / \
KEEP_CURRENT / INSUFFICIENT_EVIDENCE), independently of which product \
scored highest — the top picks above will be shown to the buyer regardless \
of your verdict. Every timing-based claim must carry its basis; if no \
credible timing signal exists, say so rather than manufacturing one.

When you are done, your FINAL message must be, and contain nothing except, \
a single JSON object matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


class SdkSynthesizer:
    """Real `Synthesizer` — calls Opus directly via the SDK, `tools=[]`
    (same reasoning as `SdkScorer`). Not unit tested (see module
    docstring)."""

    def __init__(self, model: str = config.MODEL_OPUS) -> None:
        self._model = model

    async def synthesize(
        self,
        products: list[Product],
        scores: list[Scored],
        survey: SurveyReport,
        intake: IntakeAnswers,
        timing: TimingAssessment,
        low_evidence_mode: bool,
        top_picks: list[Product],
    ) -> RawSynthesis:
        # §3: fail loudly before spending anything if the recommendation
        # logic skill isn't there to be loaded.
        assert_skill_loaded(config.RECOMMENDATION_LOGIC_SKILL)

        scores_by_name = {s.product_name: s for s in scores}
        prompt = SYNTHESIS_PROMPT_TEMPLATE.format(
            category_kind=survey.category_kind,
            budget_ceiling=(
                intake.budget_ceiling if intake.budget_ceiling is not None else "none stated"
            ),
            budget_note=intake.budget_note or "no note given",
            low_evidence_mode=low_evidence_mode,
            commodity_category=is_commodity_category(survey),
            differentiation=survey.differentiation,
            estimated_product_count=survey.estimated_product_count,
            timing_json=json.dumps(timing.model_dump(mode="json")),
            all_scores_json=json.dumps(
                [_score_summary(p, scores_by_name.get(p.name)) for p in products]
            ),
            top_picks_json=json.dumps(
                [_score_summary(p, scores_by_name.get(p.name)) for p in top_picks]
            ),
            schema=json.dumps(RawSynthesis.model_json_schema()),
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

        parsed = _parse_synthesis_json(final_text)
        if parsed is None:
            raise RuntimeError(
                "scout-synthesizer's final message did not contain a "
                f"parseable JSON object: {final_text!r}"
            )
        return RawSynthesis(**parsed)
