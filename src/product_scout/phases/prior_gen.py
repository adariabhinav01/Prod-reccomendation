"""Phase 5 — PRIOR-GEN (spec docs/handoff.md §1/§5.1/§12, build order
step 10).

For each shortlisted current-generation `Product`, one Haiku pass looks for
its immediate predecessor, still findable new, and reports whether the
price delta materially exceeds the capability delta (§12.3) — the highest-
value behavior in this design and, per §12.3's own text, "the one most
review sites structurally cannot provide." A promoted finding becomes a
genuine `Product` (`generation="prior"`, inheriting its sibling's
`cluster_key`) that enters the run's product pool as a first-class row,
exactly like anything EXTRACTION produced.

This module owns four things:

1. `PriorGenResearcher` — the seam through which the actual research call
   happens.
2. `RawPriorGenFinding`/`RawPriorGen` — what Haiku returns per seed
   product: a comparison verdict plus (only when promoted) enough raw
   product data to construct a `Product` from.
3. `_admit_finding` — §12.1's conditional-section rule and §12.6's
   omit/disclose split, applied per finding.
4. `run_prior_gen` — thin orchestration on top of the seam.

### §3's own `PHASES` sample grants NO `record_product` tool here

`"prior_gen": dict(model=HAIKU, tools=["WebSearch", "WebFetch"])` — unlike
`"extraction"`, which also gets `mcp__scout__record_product`. So this phase
cannot validate a candidate's sourcing live, field by field, the way
EXTRACTION's tool-call boundary does; it gets exactly one final JSON
message per run, the same shape SURVEY/REFINE/SCORING/SYNTHESIS already
use. `RawPriorGenFinding.product` is therefore a loose `dict[str, Any]`
(record_product-tool-shaped, not yet validated) rather than a nested
`Product` — the JSON schema shown to Haiku nests the exact same
`RECORD_PRODUCT_SCHEMA` extraction's tool uses (imported, not re-typed),
so the shape Haiku is asked to produce is identical either way.

### Validation reuses `build_product_from_args`, not a second implementation

`tools/record_product.py`'s `build_product_from_args` (added this same
build step — see that module's docstring) is the exact §4.3-ledger-plus-
Product-validator contract this phase needs, extracted specifically so
this module doesn't reimplement it. The one real difference from
EXTRACTION's live usage: **no retry.** EXTRACTION's tool rejects a bad
call and tells the model exactly which field to fix, then the model calls
again in the same conversation. This phase gets one final message with no
conversation left to continue — `_admit_finding` treats any rejection
(including a raw `KeyError`/`TypeError` from a malformed `product` dict,
which a live tool call would never let through because the SDK schema-
validates tool args before the handler runs) as "drop this candidate, log
one caveat," never as something to re-ask about.

### Three structural fields are Python's, never trusted from the model

Before calling `build_product_from_args`, this module overwrites whatever
the model put in `product["generation"]`, `["cluster_key"]`, and
`["role"]`:

- `generation` is forced to `"prior"` — this phase's entire job.
- `cluster_key` is forced to the SEED's `cluster_key` — §5.1: "prior-gen
  INHERITS its sibling's cluster_key (same cluster, different generation —
  they must not inflate `clusters_found`)." Trusting the model's own
  cluster_key would risk exactly the inflation that sentence warns against.
- `role` is forced to `"recommendation"` — §12.3: "it enters as a
  first-class row." The lowest-scoring-current-gen demotion §5.1 also
  describes (`role="reference_displaced"`) is a LATER, score-dependent
  decision — Phase 5 runs before Phase 6a SCORING even exists, so there
  are no scores yet to decide who gets demoted. `phases/scoring.py`'s
  `_demote_for_row_cap` (added this same build step) is where that half
  of the rule actually lives; see that module's docstring.

### Not in scope for this build step: §12.2's other two acquisition paths

§12.2 names three acquisition paths — prior-gen **new**, prior-gen
**used**, and current-gen **used** — and calls them out as genuinely
different recommendations ("the previous model is $80 cheaper new" vs.
"last year's is half price used"). This module implements only the first:
§12.3's prior-gen-NEW evaluation, which is the one path that produces a
schema-representable outcome (a full `Product`). The locked schema
(`models.py`) has no field for a used-price range at all — §12.5's
"typically $180–240 used in good condition" has nowhere to attach short of
inventing a new `Product`/`PricingModel` field, which is out of this
step's authority. `SurveyReport.secondhand_risk_factors` (already built,
Phase 1) and its own collapsed report section remain the only used/
secondhand-adjacent content this pipeline currently carries.

### `low_evidence_mode` (build order step 12)

Closed the gap this module's docstring used to flag here: neither the
prompt nor the signature mentioned `low_evidence_mode`, even though
`research-protocol`'s own skill content is explicitly conditional on "when
told a run is in low-evidence mode." `research`/`run_prior_gen`/
`_admit_finding` all take it now, threaded to `PRIOR_GEN_PROMPT_TEMPLATE`
and to `build_product_from_args`'s §14/§8.3 mode-gated checks — the same
treatment `phases/extraction.py` got in the same build step, closing both
gaps together rather than patching them one at a time, exactly as this
note used to say a future step should.

`research`/`run_prior_gen` also take `budget: RunBudget` (build order step
13, required, no default) — the run-scoped §13 cost-cap counter, threaded
into `SdkPriorGenResearcher.research()`'s `ClaudeAgentOptions(hooks=...)`
the same way `ledger` already is. See `hooks/budget.py`'s module docstring
for why this must be the SAME instance every research phase shares.

### §12.1's conditional-section rule and §12.6's omit/disclose split

§12.1: "It appears only when there is something actionable to say. No
'not applicable' line for a first-generation product. No mention of a
prior generation whose discount doesn't justify the capability gap."
§12.6 sharpens this into two different cases that must not be conflated:
"Omit absence-of-relevance; disclose absence-of-information. A nonexistent
prior generation is a fact about the world with no bearing — silence is
correct. A failed fetch or relaxed constraint is a limitation *in the
research* — always disclosed." `_admit_finding` implements exactly this
split: no predecessor, or a predecessor that isn't worth promoting, is
**absence-of-relevance** — silence, no caveat manufactured. A predecessor
reported as worth promoting that then fails to source or validate is
**absence-of-information** — a real research limitation, always disclosed.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from pydantic import BaseModel

from product_scout import config
from product_scout.hooks.budget import (
    RunBudget,
    cost_cap_post_tool_use_matchers,
    cost_cap_pre_tool_use_matchers,
)
from product_scout.hooks.ledger import FetchLedger, ledger_hook_matchers
from product_scout.hooks.source_guard import source_guard_hook_matchers
from product_scout.models import Location, Product, SurveyReport
from product_scout.skills import assert_skill_loaded
from product_scout.tools.record_product import RECORD_PRODUCT_SCHEMA, build_product_from_args

PRIOR_GEN_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "seed_product_name": {
            "type": "string",
            "description": "Must exactly match one of the current-generation product names given.",
        },
        "predecessor_found": {"type": "boolean"},
        "worth_promoting": {
            "type": "boolean",
            "description": (
                "True ONLY when the price delta materially exceeds the "
                "capability delta (§12.3). Ignored when predecessor_found "
                "is false — leave it false rather than reasoning about a "
                "predecessor that doesn't exist."
            ),
        },
        "price_delta_note": {
            "type": ["string", "null"],
            "description": "e.g. '$80 cheaper new, still in stock at two retailers.'",
        },
        "capability_delta_note": {
            "type": ["string", "null"],
            "description": "What's actually different/worse — concrete, not vague.",
        },
        "product": {
            "anyOf": [RECORD_PRODUCT_SCHEMA, {"type": "null"}],
            "description": (
                "Required (non-null), fully researched to the same standard "
                "as a full extraction, when predecessor_found and "
                "worth_promoting are both true. Null otherwise."
            ),
        },
    },
    "required": ["seed_product_name", "predecessor_found"],
}

PRIOR_GEN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"findings": {"type": "array", "items": PRIOR_GEN_FINDING_SCHEMA}},
    "required": ["findings"],
}


class RawPriorGenFinding(BaseModel):
    seed_product_name: str
    predecessor_found: bool
    worth_promoting: bool = False
    price_delta_note: str | None = None
    capability_delta_note: str | None = None
    # record_product-tool-shaped, NOT yet validated — see module docstring.
    product: dict[str, Any] | None = None


class RawPriorGen(BaseModel):
    findings: list[RawPriorGenFinding] = []


@runtime_checkable
class PriorGenResearcher(Protocol):
    """Seam for Phase 5's actual research call."""

    async def research(
        self,
        seeds: list[Product],
        ledger: FetchLedger,
        low_evidence_mode: bool,
        budget: RunBudget,
    ) -> RawPriorGen: ...


class PriorGenOutcome(BaseModel):
    products: list[Product] = []
    caveats: list[str] = []


def _admit_finding(
    finding: RawPriorGenFinding,
    seeds_by_name: dict[str, Product],
    survey: SurveyReport,
    ledger: FetchLedger,
    location: Location,
    low_evidence_mode: bool,
) -> tuple[Product | None, str | None]:
    """§12.1/§12.6, applied per finding. See module docstring's
    "conditional-section rule" section for the omit/disclose split this
    implements."""
    seed = seeds_by_name.get(finding.seed_product_name)
    if seed is None:
        # Haiku named a seed outside the given set. Nothing to disclose
        # about a phantom seed — not a research limitation, just noise.
        return None, None

    if not finding.predecessor_found or not finding.worth_promoting:
        # §12.1, quoted directly: "No mention of a prior generation whose
        # discount doesn't justify the capability gap." Absence of
        # relevance — silence, no caveat manufactured.
        return None, None

    if finding.product is None:
        return None, (
            f'A prior generation for "{seed.name}" was reported as worth '
            "promoting, but no product data was provided — dropped rather "
            "than guessed at."
        )

    args = dict(finding.product)
    args["generation"] = "prior"  # this phase's entire job; never trust the model's own
    args["cluster_key"] = seed.cluster_key  # §5.1: inherits its sibling's key, always
    args["role"] = "recommendation"  # §12.3: "it enters as a first-class row"

    try:
        product, error_text = build_product_from_args(
            args, survey, ledger, location, low_evidence_mode
        )
    except (KeyError, TypeError) as e:
        # No live tool boundary schema-validated this dict before it got
        # here (see module docstring) — a malformed `product` object is a
        # real possibility this phase alone has to guard against.
        return None, (
            f'A prior generation for "{seed.name}" was reported as worth '
            f"promoting, but its product data was malformed ({e!r}) — "
            "dropped rather than guessed at."
        )

    if product is None:
        return None, (
            f'A prior generation for "{seed.name}" could not be sourced to '
            f"standard: {error_text}"
        )
    return product, None


async def run_prior_gen(
    seeds: list[Product],
    survey: SurveyReport,
    ledger: FetchLedger,
    location: Location,
    low_evidence_mode: bool,
    budget: RunBudget,
    researcher: PriorGenResearcher,
) -> PriorGenOutcome:
    """Run Phase 5 end to end: research every current-gen seed in one
    Haiku call, then admit or silently drop each finding per §12.1/§12.6.

    Filters `seeds` to `generation == "current"` defensively — researching
    a prior generation's OWN predecessor isn't this phase's job, and
    nothing upstream currently guarantees the list it's handed is already
    that narrow.

    Short-circuits without calling the researcher when there are no
    current-gen seeds — mirrors `run_extraction`/`run_scoring`'s "nothing
    to reason about, don't spend a call" precedent.

    `location` (build order step 11) is threaded straight through to
    `_admit_finding`/`build_product_from_args` — the same §10.3
    confirmed-cross-border gate EXTRACTION applies. `low_evidence_mode`
    (build order step 12) is threaded the same way, to both the prompt and
    §14/§8.3's mode-gated `record_product` checks — the exact gap this
    module's own docstring flagged as "an inherited gap, not one introduced
    here" is what this closes.
    """
    current_gen_seeds = [p for p in seeds if p.generation == "current"]
    if not current_gen_seeds:
        return PriorGenOutcome(products=[], caveats=[])

    raw = await researcher.research(current_gen_seeds, ledger, low_evidence_mode, budget)
    seeds_by_name = {p.name: p for p in current_gen_seeds}

    products: list[Product] = []
    caveats: list[str] = []
    for finding in raw.findings:
        product, caveat = _admit_finding(
            finding, seeds_by_name, survey, ledger, location, low_evidence_mode
        )
        if product is not None:
            products.append(product)
        if caveat is not None:
            caveats.append(caveat)

    return PriorGenOutcome(products=products, caveats=caveats)


# ---------------------------------------------------------------------------
# SdkPriorGenResearcher — real PriorGenResearcher. Not unit tested here (no
# ANTHROPIC_API_KEY in this suite, consistent with every other real SDK
# adapter in this codebase).
# ---------------------------------------------------------------------------

_UNPARSED = object()


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_prior_gen_json(text: str) -> dict | None:
    """Same whole-message-then-outermost-braces fallback every other real
    adapter in this codebase uses."""
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


def _seed_summary(product: Product) -> dict:
    """What Haiku needs to go find a predecessor — name/brand/cluster
    context and current specs to diff against, deliberately not the full
    `Product` dump (provenance noise irrelevant to finding a different
    product entirely)."""
    return {
        "name": product.name,
        "brand": product.brand,
        "cluster_key": product.cluster_key,
        "specs": {k: v.value for k, v in product.specs.items()},
        "price": product.pricing.upfront_amount,
        "currency": product.pricing.price_currency,
    }


PRIOR_GEN_PROMPT_TEMPLATE = """You are scout-prior-gen. For EACH \
current-generation product below, research whether a direct predecessor \
(the immediately prior generation) exists and is still findable new \
(clearance, old stock — normal, not secondhand).

CURRENT-GENERATION PRODUCTS:
{seed_json}

For each one, report seed_product_name (copied verbatim), \
predecessor_found, and — only when one exists — worth_promoting: true \
ONLY when the price delta materially exceeds the capability delta. Do not \
manufacture a marginal case just to report something; a real "no" is a \
complete answer.

When worth_promoting is true, research and report `product` with the same \
rigor as a full extraction — manufacturer page plus independent reviews. \
Every spec and the price must carry a real, citable http(s) source_url you \
actually fetched with WebFetch in this conversation; a URL only seen in a \
search result is not admissible for a spec or price, only for \
ownership_notes or review_sources. Omit a spec entirely rather than \
inventing a source for it.

Budget yourself to roughly {max_searches} searches across all products \
combined.

LOW-EVIDENCE MODE: {low_evidence_mode}. When true, follow your research \
protocol skill's low-evidence-mode section exactly — community sources and \
unverified manufacturer performance claims become admissible (labeled as \
such), and every relaxation you make must be something the report can name \
explicitly. When false, a community source is never admissible as a \
spec's source_type — record_product rejects it.

A fetch or search failure is routine, not exceptional — report what you \
couldn't reach and move on; don't retry the same query and don't route \
around a failure through another method.

When you are done, your FINAL message must be, and contain nothing except, \
a single JSON object matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


class SdkPriorGenResearcher:
    """Real `PriorGenResearcher` — calls Haiku directly via the SDK, no
    dispatched subagent (§3), no `record_product` tool (see module
    docstring). Not unit tested (see module docstring)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def research(
        self,
        seeds: list[Product],
        ledger: FetchLedger,
        low_evidence_mode: bool,
        budget: RunBudget,
    ) -> RawPriorGen:
        # §3: fail loudly before spending anything if the research
        # protocol skill isn't there to be loaded.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)

        prompt = PRIOR_GEN_PROMPT_TEMPLATE.format(
            seed_json=json.dumps([_seed_summary(p) for p in seeds]),
            max_searches=config.MAX_PRIOR_GEN_SEARCHES,
            low_evidence_mode=low_evidence_mode,
            schema=json.dumps(PRIOR_GEN_RESPONSE_SCHEMA),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=["WebSearch", "WebFetch"],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            hooks={
                "PreToolUse": source_guard_hook_matchers(low_evidence_mode)
                + cost_cap_pre_tool_use_matchers(budget),
                "PostToolUse": ledger_hook_matchers(ledger)  # §4.3
                + cost_cap_post_tool_use_matchers(budget),
            },
            skills=[config.RESEARCH_PROTOCOL_SKILL],
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

        parsed = _parse_prior_gen_json(final_text)
        if parsed is None:
            raise RuntimeError(
                "scout-prior-gen's final message did not contain a "
                f"parseable JSON object: {final_text!r}"
            )
        return RawPriorGen(**parsed)
