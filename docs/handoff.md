# Product Scout — Build Handoff Spec

**Version 7** — supersedes v1–v6 wholesale. Do not consult older versions.

**Changes from v6**, from the fifth round of external review: the coherence validator keys on `source_count` rather than `independent_review_count` (§4.0c) — the earlier form rejected the ordinary case of a manufacturer page and one review agreeing; flip-point eligibility becomes an explicit two-clause predicate rather than an emergent property of a `0.019` margin (§5.2); and `conflict_ratio` takes the *extracted* denominator so a thinly-researched product cannot cap its own penalty (§4.0a, §4.0b).

**Carried from v6:** both evidence ratios defined with fixed denominators (§4.0a, §4.0b); conflict coefficient `0.40` making the total-disagreement cap structural (§4.1); ledger access modes distinguishing fetched from search-seen URLs (§4.3); the hybrid upfront-share gate (§5.2); the cost-cap counter in orchestrator state (§13.1).

**Carried from v5**, from the first two rounds: multiplicative confidence with a smoothed review curve (§4.1); re-anchored bands that tile (§4.2); Python orchestration with no model-driven control flow (§3); flip points bounded to an interpolable range (§5.2); ledger-validated `source_url` (§4.3); source type and methodology orthogonal (§14); question stopping bounded by category structure (§9.7); a golden set gating skill edits (§17.1); tiered caveats (§5.5).

**For:** Claude Code
**Target:** A standalone Python application on the Claude Agent SDK that produces balanced, research-backed product recommendations for any product category.

---

## 0. Decisions already locked

| Decision | Choice |
|---|---|
| Packaging | Standalone **Claude Agent SDK application** |
| Language | **Python** (`claude-agent-sdk`) |
| Orchestration | **Python holds the loop.** One `query()` per phase, no model-driven dispatch (§3) |
| Interface | **Terminal CLI now, web later** — all interaction behind `QuestionPort` |
| Report output | **Self-contained local HTML** (auto-opened) **+ JSON run record** |
| Model routing | **Haiku** for gathering/extraction (phases 1, 3, 4, 5). **Opus** for judgment (phases 2, 6a, 6b) |
| Market-timing research | **Always run**, as its own pass |
| Currency / region | **User-configured**, not assumed (§10) |
| Output language | **English by default**, changed only on explicit request |
| Units | **Derived from location**, overridable. Stored native, converted at render only |
| Shortlist | **Cluster-based** (§5.1). Measured in **rows**: floor 6, soft target 8, hard cap 12 |
| Archetype cap | **6–8 total distinct, default 7**, pooled and ranked by relevance |
| Budget | **Single scalar ceiling + free-text note.** No tiered model |
| Run history | **Indexed** in `~/.product-scout/`, with per-phase checkpointing |
| Buy links | **None.** Price citations only, no affiliate tagging |
| Score vs confidence | Score is model judgment. Confidence is computed in Python (§4.1) |
| Flip points | Require **Moderate confidence (`≥0.65`)**; bounded to sampled range (§5.2) |
| Community sources | **Bounded-purpose admissible** in standard mode (§14) |
| Acquisition default | **New is expected.** Prior-gen new is normal. Secondhand is supplementary (§12) |
| Trusted sources | Schema seam only, unwired (§15) |

---

## 1. Phase sequence

```
  0  INTAKE      Python ── fixed questions, no model calls
  1  SURVEY      Haiku  ── coverage, differentiation, pricing complexity,
                           secondhand risk, region scope, clusters, dimensions
  2  REFINE      Opus   ── secondary questions grounded in the survey
  3  EXTRACTION  Haiku  ── deep pass; representatives chosen against requirements
  4  TIMING      Haiku  ── releases, price trends, tech transitions
  5  PRIOR-GEN   Haiku  ── prior generations of shortlisted products
  6a SCORING     Opus   ── scores, counterfactuals, flip points
  6b SYNTHESIS   Opus   ── verdict, write-up, against the scored set
  7  RENDER      Python ── HTML + JSON, no model calls
```

**Why REFINE precedes EXTRACTION.** Clustering happens before requirements are known. If a requirement surfaces after extraction, there is no way to tell whether *other, unextracted* members of a cluster would have satisfied it — only one representative per cluster was researched. Asking first fixes this at the source.

**Why Phase 6 is split.** A single call producing scores, three counterfactuals per product, verdict, and prose for up to 12 products is a lot to ask well. Splitting also gives §5.1's constraint enforcement something to re-prompt against without regenerating the write-up.

**The sequence is fixed and Python-enforced (§3).** No model decides what runs next.

---

## 2. Project layout

```
product-scout/
├── pyproject.toml
├── README.md
├── .env.example
├── src/product_scout/
│   ├── cli.py                     # research | rescore | history | config | eval
│   ├── orchestrator.py            # the control loop; §3
│   ├── config.py
│   ├── settings.py                # ~/.product-scout/config.toml
│   ├── models.py                  # §4
│   ├── confidence.py              # §4.1 — pure functions, heavily tested
│   ├── phases/
│   │   ├── intake.py  survey.py  refine.py  extraction.py
│   │   ├── timing.py  prior_gen.py  scoring.py  synthesis.py
│   ├── io/
│   │   ├── port.py                # QuestionPort ← swappability seam
│   │   ├── cli_port.py
│   │   └── web_port.py            # stub
│   ├── tools/
│   │   ├── ask_topic.py  ask_choice.py  record_product.py
│   │   └── server.py
│   ├── hooks/
│   │   ├── fetch_guard.py         # source allowlist + cost cap
│   │   └── ledger.py              # §4.3 provenance ledger
│   ├── render/
│   │   ├── report.py  scale.py  units.py  template.html
│   └── store/
│       ├── runs.py  index.py  checkpoint.py
├── eval/                          # §17.1 golden set
│   ├── cases/*.yaml
│   └── grade.py
└── .claude/skills/
    ├── intake-protocol/  question-design/  research-protocol/
    ├── market-timing/    recommendation-logic/  report-contract/
```

---

## 3. Orchestration and SDK wiring

**Python holds the control loop.** The orchestrator issues **one `query()` per phase**, each with its own model, its own prompt, and its own narrowly-scoped tool list. There is no top-level `model=`, no `agents={}` block, and `"Agent"` is never in `allowed_tools` — nothing dispatches subagents, because nothing needs to.

This is what makes the fixed-sequence invariant a structural guarantee rather than a prompt-enforced hope. A model that cannot dispatch cannot reorder.

```python
from claude_agent_sdk import query, ClaudeAgentOptions

PHASES = {
    "survey":     dict(model=HAIKU, tools=["WebSearch", "WebFetch"]),
    "refine":     dict(model=OPUS,  tools=["mcp__scout__ask_topic",
                                           "mcp__scout__ask_choice"]),
    "extraction": dict(model=HAIKU, tools=["WebFetch",
                                           "mcp__scout__record_product"]),
    "timing":     dict(model=HAIKU, tools=["WebSearch", "WebFetch"]),
    "prior_gen":  dict(model=HAIKU, tools=["WebSearch", "WebFetch"]),
    "scoring":    dict(model=OPUS,  tools=[]),
    "synthesis":  dict(model=OPUS,  tools=[]),
}

async def run_phase(name: str, prompt: str) -> str:
    spec = PHASES[name]
    options = ClaudeAgentOptions(
        model=spec["model"],
        allowed_tools=spec["tools"],
        permission_mode="default",       # no phase edits files; §3.2
        setting_sources=["project"],
        mcp_servers={"scout": scout_server},
        hooks=HOOKS,
    )
    result = None
    async for msg in query(prompt=prompt, options=options):
        if msg.type == "result":
            result = msg.result
    return result
```

### 3.1 No conversational carryover

Each phase is a fresh `query()`. **Nothing a later phase needs may live in conversational state — it must be in the run record.** This is the property that makes `rescore` work at all, and it is the one most likely to be violated by someone finding it convenient to thread context along. See invariant 10.

### 3.2 No phase writes files

All persistence — run records, index, config, rendered report, checkpoints — happens in Python, outside the model's tool surface. No phase is granted a file-writing tool, so `permission_mode="default"` with per-phase `allowed_tools` is sufficient. (An earlier draft set `acceptEdits`, granting a permission nothing in the design uses.)

### 3.3 Pin model IDs and assert skills loaded

Resolve `HAIKU`/`OPUS` to fully-qualified IDs in `config.py` and store them in the run record. Assert at startup that `.claude/skills/` actually loaded — running without `recommendation-logic` produces plausible-looking garbage rather than an error.

### 3.4 QuestionPort

Every user interaction routes through this. No phase may call terminal input directly.

```python
from typing import Protocol, Literal

GateAnswer = Literal["must_have", "must_avoid", "persuadable", "no_preference"]

class AxisSpec(BaseModel):
    kind: Literal["position", "importance"]      # from Dimension.axis_kind; §9.4
    low_label: str                               # position: the opposing pole
    high_label: str                              #   importance: high_label only
    why_this_matters: str                        # one sentence, grounded in clusters

class TopicPrompt(BaseModel):
    topic: str
    dimension_name: str | None                   # None = free-text-only; §9.7
    gate_question: str
    gate_description: str                        # what "must have"/"must avoid" mean here
    axis: AxisSpec | None                        # None when no coherent axis exists
    free_text_prompt: str                        # ALWAYS present; §9.3

class QuestionPort(Protocol):
    async def ask_choice(
        self, question: str, options: list[str], escape_hatch: str
    ) -> str: ...

    async def ask_topic(
        self, topic: TopicPrompt
    ) -> TopicAnswer: ...        # gate + optional axis + free text, composed

    async def offer_bailout(self) -> bool: ...   # "use your judgment for the rest"
```

`ask_topic` composes the gate, axis, and free-text fields into one logical question. **In the CLI this is a presentation improvement, not an interaction-count reduction** — a terminal still collects three inputs sequentially without a TUI form layer. What actually bounds CLI interaction volume is the §9.7 stopping condition and the bail-out. The composition exists so the web port can render one form; say so plainly rather than claiming a reduction the first release won't deliver.

---

## 4. Data model

### 4.0a The two evidence ratios — definitions

**`corroboration_ratio` and `conflict_ratio` are not competing shares of one quantity.** They are independent per-spec properties measured over the same fixed denominator, and either may be `1.0` while the other also is.

For each spec key in `SurveyReport.comparison_specs`, relative to a given product:

- **corroborated** — the product has that spec and its `corroborated_by` list is non-empty.
- **conflicted** — the product has that spec and its `conflicting_values` list is non-empty.

**The two ratios take different denominators, deliberately** — see §4.0b for why:

```
extracted           = comparison_specs ∩ keys(product.specs)

corroboration_ratio = |corroborated| / |comparison_specs|   # completeness matters
conflict_ratio      = |conflicted|   / |extracted|          # of what we HAVE, how much is disputed
                                                            # 0.0 when |extracted| == 0
```

They answer different questions. Corroboration asks *how much of the decision-relevant picture is confirmed*, so an unextracted spec counts against it. Conflict asks *how much of what we actually found is disputed*, so an unextracted spec is simply outside the question.

**A single spec can be both.** Worked example, from real hand-testing: desk weight capacity, three sources — A and B both report 176 lb, C reports 265 lb. That spec is corroborated (A and B agree) *and* conflicted (C dissents). This is the ordinary shape of a well-sourced product with one dissenting outlet, not a data error.

> **Do not add a validator requiring `corroboration_ratio + conflict_ratio ≤ 1.0`.** It would reject the case above. An external reviewer proposed exactly that, reasoning from two undefined floats sitting adjacent in the schema — which is the reading anyone will land on absent this section. That is why the definitions are written out here rather than left implicit.

### 4.0b The denominator is fixed per run

`comparison_specs` is decided once in SURVEY, is identical for every product in the run, must contain every `Dimension.name`, and should stay within the 5–10 range the comparison table can legibly show.

**Why this matters more than it looks.** If the denominator were `len(product.specs)` — whatever the extractor happened to pull — then extraction verbosity would move confidence more than the evidence does. One genuinely disputed spec, same product, same sources:

| Specs extracted | `conflict_ratio` | Confidence | Band |
|---|---|---|---|
| 2 | `0.50` | `0.690` | Moderate |
| 3 | `0.33` | `0.747` | Moderate |
| 5 | `0.20` | `0.793` | Moderate |
| 10 | `0.10` | `0.828` | **High** |
| 20 | `0.05` | `0.845` | **High** |

One real disagreement spanning two bands, decided by how thorough Haiku felt. The same dilution runs in reverse for corroboration: padding the spec list with uncontested easy values improves both ratios. That destroys cross-product comparability — the property the entire confidence system exists to provide — and makes `rescore` inherit whatever the extractor did months earlier.

**A fixed denominator also makes missing specs count correctly.** A product with 2 of 10 comparison specs extracted, both corroborated, now yields `corroboration_ratio = 0.2` rather than `1.0`. You know less about it, and the number finally says so. Under a free denominator, missing specs were invisible — they appeared in neither numerator nor denominator.

**Absence is not disagreement.** A spec the product doesn't have counts as neither corroborated nor conflicted; it simply fails to contribute to corroboration's numerator.

**Why conflict takes the narrower denominator.** Conflict can only be *detected* on specs that were actually extracted, so measuring it over `comparison_specs` caps it at the coverage ratio — and a thinly-extracted product then cannot accumulate a penalty however disputed it is. With `comparison_specs = 8` and a well-researched profile:

| Extracted | Disputed | Disputed as % of found | conflict over `comparison_specs` | conflict over `extracted` |
|---|---|---|---|---|
| 8 of 8 | 1 | 12% | `0.125` → `0.829` High | `0.125` → `0.829` High |
| 8 of 8 | 4 | 50% | `0.500` → `0.658` Moderate | `0.500` → `0.658` Moderate |
| **2 of 8** | **2** | **100%** | `0.250` → **`0.680` Moderate** | `1.000` → **`0.453` Low** |
| 4 of 8 | 4 | 100% | `0.500` → `0.604` Low | `1.000` → `0.453` Low |

Under the wide denominator, a product where *every spec found is disputed* outscores one where half are — because 25% coverage caps its conflict at `0.25`, a 10% penalty ceiling. The narrow denominator orders them correctly.

This is **not** a return to the free denominator of the extraction-verbosity defect above: `extracted` is bounded by `comparison_specs`, so padding the spec list cannot inflate it.

**Deliberately not done: a `spec_coverage` term in breadth.** It was proposed and rejected. Corroboration over `comparison_specs` already carries the incompleteness signal — a product with 2 of 8 specs, both corroborated, scores `corroboration_ratio = 0.25` and lands `0.10` below full coverage. Adding coverage to breadth as well would penalize the same fact twice, and would invalidate every row of the §4.1 assertion table for a defect the denominator change already fixes. Those rows all assume full coverage, where the two denominators coincide — which is why they are unchanged by this section.

Golden-set stable assertion: **all products in a run share one `comparison_specs` denominator** (§17.1).

### 4.0c Schemas

```python
class SourcedValue(BaseModel):
    value: str
    native_unit: str | None
    source_url: str                        # REQUIRED, and ledger-validated (§4.3)
    source_type: Literal["manufacturer", "testing_outlet", "aggregator",
                         "retailer", "community"]
    has_stated_methodology: bool           # orthogonal to type; §14
    corroborated_by: list[str] = []
    conflicting_values: list[str] = []
    observed_at: datetime
    from_trusted_source: bool = False      # §15 seam

class Location(BaseModel):
    country: str                           # ISO 3166-1 alpha-2
    currency: str                          # ISO 4217

class Availability(BaseModel):
    sold_in_region: bool
    regional_names: list[SourcedValue] = []
    ships_to_region: bool | None
    ships_from: str | None
    ships_from_signal: Literal["shipping_policy", "cctld", "currency",
                               "language", "fallback"] | None
    ships_from_confidence: float           # DERIVED from signal; §10.6
    import_caveats: list[str] = []
    shipping_estimate_native: float | None # only when CONFIRMED cross-border
    duty_estimate_native: float | None
    landed_price_native: float | None

class PricingModel(BaseModel):
    model_type: Literal[
        "one_time", "subscription_only", "one_time_plus_subscription",
        "freemium", "usage_based", "financed_major_purchase"]
    upfront_amount: float | None
    recurring_amount: float | None
    recurring_period: Literal["monthly", "annual"] | None
    recurring_required_for_core: bool = False
    total_cost_1yr: float | None           # None for usage_based & financed; §11.3
    price_currency: str
    price_tax_inclusive: bool | None       # None = undetermined; §10.3a
    price_source_url: str
    price_observed_at: datetime
    price_overridden: bool = False         # set by rescore; §16

class EvidenceProfile(BaseModel):
    """EVERY field here is DERIVED in Python. See §4.0d — no part of this
    model is ever emitted by an extraction or analysis model."""
    source_count: int = Field(ge=0)              # distinct source URLs across
                                                 # specs, ownership_notes, and
                                                 # review_sources. Not reviews.
    independent_review_count: int = Field(ge=0)  # sources of type testing_outlet
                                                 # or aggregator
    extracted_spec_count: int = Field(ge=0)      # |comparison_specs ∩ specs|
    has_tier1_specs: bool                        # any source_type == manufacturer
    has_methodology_backed_source: bool          # any has_stated_methodology
    corroboration_ratio: float = Field(ge=0.0, le=1.0)   # §4.0a
    conflict_ratio: float = Field(ge=0.0, le=1.0)        # §4.0a
    recency_factor: float = Field(ge=0.0, le=1.0)        # observed_at vs the
                                                 # category_kind window; §4.1
    confidence: float = Field(ge=0.0, le=1.0)    # compute_confidence(); §4.1
    confidence_note: str                         # rendered from the above

    @model_validator(mode="after")
    def _coherent(self):
        # A lone SOURCE cannot corroborate or conflict with anything.
        # Keyed on source_count, NOT independent_review_count: a manufacturer
        # spec page and one independent review reporting the same figure IS
        # corroboration, at independent_review_count == 1. Keying on reviews
        # would reject ordinary extractor output. (§14 sources specs from
        # manufacturer pages and prices from retailers; neither is a review.)
        if self.source_count <= 1 and (
            self.corroboration_ratio != 0.0 or self.conflict_ratio != 0.0
        ):
            raise ValueError(
                "corroboration_ratio and conflict_ratio must be 0.0 when "
                "source_count <= 1 — there is nothing to agree or disagree with"
            )
        if self.extracted_spec_count == 0 and self.conflict_ratio != 0.0:
            raise ValueError(
                "conflict_ratio must be 0.0 when no comparison specs were "
                "extracted — its denominator is empty"
            )
        return self
        # NOTE: there is deliberately NO rule constraining the SUM of the two
        # ratios. See §4.0a — a spec can be both corroborated and conflicted.

class Product(BaseModel):
    name: str
    brand: str
    generation: Literal["current", "prior"]
    role: Literal["recommendation", "baseline_current", "reference_above_budget",
                  "reference_unavailable", "reference_displaced"] = "recommendation"
    cluster_key: str                        # prior-gen INHERITS its sibling's key
    cluster_rationale: str
    strength_archetype: str
    pricing: PricingModel
    availability: Availability
    specs: dict[str, SourcedValue]
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)   # HARD CONSTRAINT
    ownership_notes: list[SourcedValue] = []
    review_sources: list[str] = Field(min_length=1)
    in_budget: bool
    evidence: EvidenceProfile

class Scored(BaseModel):
    product_name: str
    score: float = Field(ge=0.0, le=10.0)
    rationale: str
    score_at_minus_10pct: float
    score_at_minus_20pct: float
    score_at_minus_30pct: float
    flip_point_amount: float | None         # only within sampled range; §5.2
    flip_point_note: str | None             # states the bound when unbounded
    # NOTE: confidence is NOT stored here. It lives once, on
    # Product.evidence.confidence, and is joined at render. Two stored copies
    # of one computed number is how the scale ends up showing a different
    # value than the caveats cite.

class Verdict(BaseModel):
    action: Literal["BUY", "WAIT", "CONSIDER_CHEAPER_CATEGORY",
                    "KEEP_CURRENT", "INSUFFICIENT_EVIDENCE"]
    reasoning: str
    timing_note: str | None

class BroaderCategory(BaseModel):
    name: str
    rationale: str
    estimated_coverage: Literal["rich", "moderate", "sparse", "barren"]

class IntakeAnswers(BaseModel):
    owns_current_version: bool
    current_model: str | None               # when upgrading; §7.2
    budget_ceiling: float | None            # None = no limit
    budget_note: str = ""                   # verbatim; §7.1
    required_features: list[str] = []       # FILTERS, not preferences
    candidates_under_consideration: list[str] = []

class TimingAssessment(BaseModel):
    signal_found: bool
    successor_expected: str | None          # with its basis; §6.5
    price_trend: str | None
    technology_transition: str | None
    basis_notes: list[str] = []             # every claim carries its basis
    recommends_wait: bool

class Cluster(BaseModel):
    key: str
    label: str
    exemplar_products: list[str]            # UNSOURCED; §8.1a constraints apply
    price_range_native: tuple[float, float] # indicative only, pre-extraction
    approximate_member_count: int

class Dimension(BaseModel):
    """A spec axis that separates clusters. Drives §9.7's stopping condition."""
    name: str
    splits: dict[str, str]                  # cluster_key -> position on this dimension
    axis_kind: Literal["position", "importance"] | None   # decided in SURVEY; §9.4

    def separating_power(self, surviving: set[str]) -> int:
        """Count of distinct positions across surviving clusters."""
        return len({v for k, v in self.splits.items() if k in surviving})

class SurveyReport(BaseModel):
    category_kind: Literal["physical", "software_service", "hybrid"]
    coverage: Literal["rich", "moderate", "sparse", "barren"]
    differentiation: Literal["high", "moderate", "low"]
    estimated_product_count: int
    independent_review_sources_found: int
    has_methodology_backed_testing: bool
    clusters: list[Cluster]
    dimensions: list[Dimension]
    comparison_specs: list[str]             # §4.0b — the ratio denominator.
                                            # Must contain every Dimension.name.
                                            # Target 5–10 for table legibility.
    pricing_complexity: Literal["simple", "subscription_based",
                                "financed_major_purchase", "hybrid"]
    secondhand_risk_factors: list[SourcedValue] = []
    products_unavailable_in_region: int
    suggested_broader_categories: list[BroaderCategory] = []
    notes: str

class TopicAnswer(BaseModel):
    topic: str
    dimension_name: str | None              # which Dimension this addressed
    gate_answer: GateAnswer
    axis_kind: Literal["position", "importance"] | None
    axis_value: float | None
    axis_skipped: bool = False              # leans require explicit answers; §9.7
    free_text: str = ""
    became_filter: bool
    assumption_logged: str | None

class RunRecord(BaseModel):
    run_id: str
    created_at: datetime
    product_type: str
    original_product_type: str | None
    location: Location                      # mismatch invalidates rescore; §16
    units: Literal["imperial", "metric"]
    intake: IntakeAnswers
    survey: SurveyReport
    topics: list[TopicAnswer]
    low_evidence_mode: bool
    commodity_category: bool
    category_broadening_offered: bool
    truncated_at_phase: int | None          # cost-cap termination; §13
    products: list[Product]
    timing: TimingAssessment
    verdict: Verdict
    scores: list[Scored]
    caveats: list[Caveat]                   # tiered; §5.5
    model_ids: dict[str, str]
    skill_hashes: dict[str, str]            # SHA-256 per SKILL.md; §16
    trusted_sources: list[str] = []
    rescored_from: str | None = None        # original run_id, set by rescore; §16
```

### 4.0d The entire `EvidenceProfile` is derived

**No field of `EvidenceProfile` is ever emitted by a model.** The whole object is constructed in Python from data the run record already holds — `product.specs`, `product.review_sources`, `product.ownership_notes`, and `SurveyReport.comparison_specs` / `category_kind`. The extraction model's job is to emit `SourcedValue`s with honest `source_type`, `has_stated_methodology`, `corroborated_by`, `conflicting_values`, and `observed_at`; everything else is counting.

This started as a narrower note about the two ratios (§4.0a) and generalizes, because the same argument covers every field:

- **Invariant 4 would otherwise be half-true.** A model that cannot set `confidence` but *can* set the quantitative inputs that move it most has not been kept out of the loop at all. This is the identical critique that produced the `ships_from_confidence` fix (§10.6): put the threshold against an auditable input, not a model's assertion.
- **`rescore` becomes genuinely reproducible.** Everything recomputes from stored `SourcedValue`s rather than being frozen model output from months ago. This is what makes §16's re-scoring meaningful rather than a replay of old judgments.
- **It moots asymmetries in the validator.** §4.0c guards `conflict_ratio` against an empty denominator, but nothing bounds `corroboration_ratio` by coverage, or `independent_review_count` against `source_count`. Derived, none of those can go wrong; asserted, all of them can, and each would need its own rule.

Practical consequence for build step 1: `confidence.py` exposes `build_evidence_profile(product, survey) -> EvidenceProfile` alongside `compute_confidence()`, and nothing else constructs one.

---

### 4.1 Confidence is computed, not judged

`score` is a **judgment** — does this fit this user? — assigned by Opus, never reduced to a formula (§5.2). `confidence` is a **measurement** — how much do we know? — computed in Python. The model never assigns it. Judgments resist formulas; measurements demand them.

```python
import math

def review_credit(n: int) -> float:
    """Smooth diminishing returns. No cliff, no ceiling."""
    return 1.0 - math.exp(-n / 2.5)

def compute_confidence(e: EvidenceProfile) -> float:
    # BREADTH — how much evidence exists. Nothing else compensates for absence.
    breadth = (0.20 * float(e.has_tier1_specs)
             + 0.25 * float(e.has_methodology_backed_source)
             + 0.55 * review_credit(e.independent_review_count))

    # QUALITY — how good it is. A multiplier, never an additive floor.
    quality = ((0.78 + 0.15 * e.corroboration_ratio + 0.07 * e.recency_factor)
               * (1.0 - 0.40 * e.conflict_ratio))

    return round(min(max(breadth * quality, 0.0), 1.0), 3)
```

**Four structural properties, each the fix for a real defect:**

**Quality is multiplicative, so absence never pays.** An earlier additive form awarded credit for `conflict_ratio == 0` and `recency_factor == 1.0` — both trivially true when there is only one source. A single-source product collected 0.18 for evidence it did not have. Absence of disagreement is not agreement.

**The conflict coefficient is `0.40`, which makes the cap structural.** At `conflict_ratio == 1.0`, quality is pinned to `0.60` and **confidence cannot exceed `0.600` for any breadth, corroboration, or recency** — below the `0.65` flip floor by construction rather than by margin. An earlier `0.22` looked adequate against a three-review profile but failed at scale: ten fully-conflicting reviews reached `0.656` and cleared the floor, because breadth kept climbing while the penalty stayed fixed. A guarantee that depends on where breadth happens to land is not a guarantee.

**The coherence validator prevents a source corroborating itself.** Forcing both ratios to `0.0` at `source_count ≤ 1` removes a class of impossible profiles. Note it is keyed on *sources*, not reviews (see the schema comment), and that it does **not** by itself guarantee §5.2's two-review property — that is now an explicit predicate, for reasons given there. And see §4.0a on why there is no sum constraint.

**Review credit is a smooth curve, not `min(n, 4)`.** At 0.55 weight, a hard cap made the fourth review worth `+0.133` and the fifth through twentieth worth nothing — piling well-covered products at identical breadth exactly where the table is most crowded. The curve yields `0.33 / 0.55 / 0.70 / 0.80 / 0.87 / 0.91` for 1–6 reviews and approaches 1.0 asymptotically.

Assertions for `confidence.py`, fully parameterized so they transcribe directly into tests:

```python
# (label, t1, meth, reviews, corr, conflict, recency, expected, band)
CONFIDENCE_CASES = [
    ("saturated evidence",              1, 1,  6, 1.0, 0.0, 1.0, 0.950, "high"),
    ("well covered",                    1, 1,  4, 1.0, 0.0, 1.0, 0.889, "high"),
    ("typical rich category",           1, 1,  3, 0.8, 0.1, 1.0, 0.777, "moderate"),
    ("solid mainstream",                1, 1,  2, 0.7, 0.0, 1.0, 0.719, "moderate"),
    ("two reviews, half corroborated",  1, 1,  2, 0.5, 0.0, 1.0, 0.696, "moderate"),
    ("no methodology source",           1, 0,  4, 0.8, 0.0, 1.0, 0.620, "low"),
    ("heavy conflict, corroborated",    1, 1,  3, 0.5, 0.5, 1.0, 0.617, "low"),
    ("total conflict, 20 reviews",      1, 1, 20, 1.0, 1.0, 1.0, 0.600, "low"),
    ("one review + corroborating mfr",  1, 1,  1, 1.0, 0.0, 1.0, 0.631, "low"),
    ("single source (corr forced 0)",   1, 1,  1, 0.0, 0.0, 1.0, 0.537, "low"),
    ("total conflict, 3 reviews",       1, 1,  3, 1.0, 1.0, 1.0, 0.501, "low"),
    ("low-evidence: mfr + 2 community", 1, 0,  2, 0.0, 0.2, 0.8, 0.387, "low"),
    ("one community source",            0, 0,  1, 0.0, 0.0, 1.0, 0.154, "very_low"),
    ("no evidence at all",              0, 0,  0, 0.0, 0.0, 0.0, 0.000, "very_low"),
]
```

Three rows carry weight beyond their values. **`total conflict, 20 reviews` is the ceiling case** — it demonstrates the structural cap rather than a point value, so it survives curve tuning. **`no evidence at all` confirms breadth-zero yields exactly `0.000`**, the headline property of the multiplicative form. And **`one review + corroborating mfr` at `0.631` sits only `0.019` under the flip floor** — which is precisely why §5.2's two-review requirement is an explicit predicate rather than an artifact of where the curve lands.

All rows assume full spec coverage, where §4.0a's two denominators coincide.

### 4.1a Property tests

These assert the *shape* of the function rather than point values, so they survive the weight tuning §19 already schedules instead of being invalidated by it. All four belong in §17.1's **stable** half.

```python
FLIP_FLOOR = 0.65

def test_single_review_cannot_price_a_comparison():
    """§5.2: a flip point always rests on >= 2 independent reviews.

    Tests the PREDICATE, not the arithmetic. The best single-review profile
    computes to 0.631 — only 0.019 under the floor — so asserting on
    confidence alone would silently break under §19.2's curve tuning.
    """
    for corr in (0.0, 1.0):          # 1.0 is reachable: mfr page + one review
        p = product(t1=True, meth=True, reviews=1, corr=corr,
                    conflict=0.0, rec=1.0, sources=2)
        assert not flip_point_eligible(p)

def test_total_conflict_cannot_price_a_comparison():
    """§4.1: total disagreement is a structural cap, not a margin."""
    for reviews in range(2, 21):
        for corr in (0.0, 0.5, 1.0):
            assert compute_confidence(profile(t1=True, meth=True, reviews=reviews,
                                              corr=corr, conflict=1.0,
                                              rec=1.0)) < FLIP_FLOOR

def test_absence_never_pays():
    """§4.1: zero breadth is zero confidence, whatever quality says."""
    for rec in (0.0, 0.5, 1.0):
        assert compute_confidence(profile(t1=False, meth=False, reviews=0,
                                          corr=0.0, conflict=0.0, rec=rec)) == 0.0

def test_bands_tile():
    """§4.2: every representable confidence maps to exactly one band."""
    for i in range(1001):
        assert confidence_band(round(i / 1000, 3)) in {
            "high", "moderate", "low", "very_low"}
```

`test_total_conflict_cannot_price_a_comparison` sweeps `corr` as well as review count — the earlier single-value version passed against a coefficient that was still broken at high breadth.

**`recency_factor`** is the fraction of sources inside the category's freshness window, selected by `category_kind`:

| `category_kind` | Window | Rationale |
|---|---|---|
| `physical` | 24 months | An 18-month-old review still describes the same paddle. |
| `software_service` | 9 months | An 18-month-old SaaS review may describe a different product. |
| `hybrid` | 9 months | Shorter window. A fitness ring's hardware ages slowly; its app doesn't. |

`category_kind` is classified once in SURVEY and is **orthogonal to `pricing_complexity`** — a fitness ring is `hybrid` with `one_time_plus_subscription` pricing; a mattress is `physical` with `simple` pricing. Never derive either from the other.

### 4.2 Display bands

Half-open on the upper edge, so the domain tiles with no gaps. Re-anchored against the §4.1 distribution — the previous boundaries were inherited from a formula that no longer exists.

| Label | Range |
|---|---|
| High | `[0.80, 1.00]` |
| Moderate | `[0.65, 0.80)` |
| Low | `[0.35, 0.65)` |
| Very low | `[0.00, 0.35)` |

```python
def confidence_band(c: float) -> Literal["high", "moderate", "low", "very_low"]:
    if c >= 0.80: return "high"
    if c >= 0.65: return "moderate"
    if c >= 0.35: return "low"
    return "very_low"
```

**Test:** every value in `[round(i/1000, 3) for i in range(1001)]` maps to exactly one band.

**What Moderate means, concretely — at `conflict_ratio == 0`.** Reaching `0.65` requires either two independent reviews plus *both* structural sources (Tier 1 specs and a methodology-backed source), or four to five reviews compensating for a missing structural source. That is the floor for pricing a comparison — see §5.2.

**With conflict present, the requirement rises steeply**, which is the point of the `0.40` coefficient. A well-researched product (Tier 1 + methodology + 4 reviews, `corr 0.8`, fresh) holds above the floor up to roughly `conflict_ratio = 0.6`; beyond that no amount of breadth recovers it, and at `conflict_ratio = 1.0` nothing reaches `0.60`. Do not quote the zero-conflict thresholds above as general requirements.

### 4.3 `source_url` is ledger-validated

A required `source_url` prevents omission, not invention — a model can emit a plausible URL it never fetched. The §13 `PostToolUse` hook already records every URL actually fetched; nothing previously validated against it.

**Every `source_url` is checked against the fetch ledger. A value citing a URL the ledger has never seen is rejected and the field dropped.**

Three implementation requirements, all load-bearing:

**Normalized comparison.** Match on scheme + host + path with query string discarded, and record both pre- and post-redirect URLs. Exact string comparison would drop good data to redirects, tracking parameters, and trailing slashes — silent false rejection is the worst possible failure shape for a hallucination guard, because it looks like clean extraction.

**The ledger is run-scoped, not phase-scoped.** Extraction legitimately cites pages SURVEY fetched. Phase-scoping would manufacture exactly the false rejections this is meant to prevent.

**Ledger entries carry an access mode**, because SURVEY produces `SourcedValue`s (notably `secondhand_risk_factors`) largely from search rather than fetching, and a naive fetch-only ledger would reject legitimate SURVEY output at build step 8:

| Mode | Written by | Admissible for |
|---|---|---|
| `fetched` | `PostToolUse` on `WebFetch` | Anything, including `Product.specs` |
| `seen_not_fetched` | `PostToolUse` on `WebSearch` | Judgment-bearing values only |

**Spec values require a `fetched` entry. A search snippet is not a page.** Judgment-bearing SURVEY values may cite `seen_not_fetched`. This distinction is what invariant 3 actually asserts.

---

## 5. Output contract

### 5.1 Comparison table

**Width is measured in rows, not clusters.** Prior-generation products inherit their sibling's `cluster_key` and backfill adds same-cluster rows, so the two stopped being interchangeable.

```
rows = max(6, clusters_found)          when clusters_found ≤ 8
                                        (floor conditional on ≥6 real products)
otherwise:
  start at 8 rows (highest-relevance clusters)
  admit rows 9–12 ONLY where the marginal cluster contributes a
    distinguishing dimension not already represented
  hard stop at 12 rows
```

**The cap is a legibility constraint.** Extraction scales linearly and flip points are computed per-product against the #1 pick, not pairwise — so cost is not what binds here. Legibility is, and it happens to bind tighter than cost would. Do not raise the cap by arguing tokens are cheap; that is not the constraint being managed.

**Backfill floor.** When fewer than 6 clusters exist but ≥6 real, distinct, purchasable products do, backfill to 6 with further representatives from existing clusters, favouring genuine intra-cluster differences (brand, price, warranty). **Never invent archetype labels to hit the target.** The report says so: *"low spec differentiation — these six are genuinely comparable, differing mainly on brand and price."*

**Low-evidence mode overrides the floor.** The floor is a legibility target, not something worth padding thin evidence to reach.

**Prior-generation rows.** They inherit `cluster_key` (same cluster, different generation — they must not inflate `clusters_found`) and count toward row width. If admitting one would exceed 12 rows, the lowest-scoring current-gen row is **demoted to `role="reference_displaced"`, not discarded** — the extraction is already paid for, and a reference row is more informative than a caveat explaining an absence.

**Two grouping concepts, different jobs.** `cluster_key` governs *what counts as a distinct option*. `strength_archetype` governs *diversity of narrative reasons to prefer one*.

**Archetypes.** Base vocabulary — `value`, `performance`, `aesthetic`, `durability`, `ergonomics`, `features`, `support` — available to any category, never mandatory. Opus may add category-specific ones (accessibility, longevity, privacy, integration, learning-curve) with the same sourced justification a pro or con requires. **Cap on total distinct archetypes: 6–8, default 7.** Candidates are pooled — base and new together — and ranked by decision-relevance. Base archetypes are **not** privileged.

**Constraints, enforced in code after Opus returns:**

- **Every row has at least one con.** An empty `cons` list is a validation failure.
- **At least 3 in-budget rows with distinct archetypes** — *when 3 qualifying products exist.*
- **At least 1 above-budget standout** — *when one exists.* A generous budget in a cheap category has none, and that is not a failure.
- **Prior-generation rows** where §12.3 found a better value proposition.

**Enforcement is bounded: two re-prompts maximum**, then accept the best result and log the unmet constraint. Failing loudly forever is worse than shipping with a disclosed gap — §5.5 exists for disclosed gaps.

**Reference rows** (`role != "recommendation"`) count toward none of the above.

### 5.2 The recommendation scale

Inline SVG, 0–10 horizontal. Each product plots with name, price, and score.

| Band | Meaning |
|---|---|
| 9.0–10.0 | Best fit. Buy this. No material reservation. |
| 7.5–8.9 | Strong. Recommend with one named tradeoff. |
| 6.0–7.4 | Solid, defensible. Real compromises. |
| 4.5–5.9 | Situational. Only if one preference dominates. |
| 3.0–4.4 | Weak. Better options at similar price. |
| 0.0–2.9 | Do not recommend. |

- Scores are **holistic judgment**, never a weighted formula. (Contrast §4.1.)
- Near-ties are permitted and expected. When products fall within 0.2, Opus states the equivalence and why. **There is no re-prompt forcing separation** — mandating a spread manufactures precision, which is the same error §6.3 forbids elsewhere. Render near-ties as a visually grouped cluster.
- **Scores must use the decimal place meaningfully.** Warn and re-prompt when more than half land on a `.0`/`.5` boundary — that is bucketing, not discriminating. This targets lazy rounding, a different problem from clustering.

**Confidence renders as a band:**

```python
band_half_width = max(0.15, 1.5 * (1.0 - confidence))   # score units
```

The `1.5` coefficient is a **rendering calibration** against the §4.1 distribution. The `0.15` floor exists because a zero-width band at `confidence == 1.0` renders as nothing and reads as a bug.

The band is a **legibility device, not a statistic.** Never present it as a confidence interval, never attach a probability. Legend: *"wider band = thinner evidence."*

**Flip points.**

Score is not a function of price, so there is no mechanical crossover — a flip point is an interpolation between sampled re-judgments. Treat it accordingly.

- **Sample at −10%, −20%, and −30%** (`score_at_minus_*` on `Scored`).
- **Report a flip point only when the crossover falls within the sampled span.** Outside it, report the bound instead of a number: *"would need more than a 30% discount to overtake."* Never extrapolate.
- **Render as a range, not a point marker with a connector line.** A point marker implies a precision the underlying judgment does not have.
- **Eligibility is an explicit predicate**, not an emergent property of the arithmetic:

```python
def flip_point_eligible(p: Product) -> bool:
    return (p.evidence.confidence >= 0.65
            and p.evidence.independent_review_count >= 2)
```

Below either condition, `flip_point_amount = None` and the report says why: *"No flip point — evidence too thin to price the comparison."*

**Both clauses are load-bearing.** The `0.65` floor is the Moderate band boundary. The two-review clause is stated separately because the best single-review profile — a manufacturer page and one independent review agreeing — computes to `0.631`, only `0.019` under the floor. Leaving the property to emerge from that margin would mean §19.2's open question about the curve scale could erase a documented guarantee silently: at scale `2.2` the same profile reaches `0.651` and clears. As a predicate the guarantee is immune to weight tuning entirely.

**Low-evidence mode suppresses flip points entirely**, independent of the threshold. Pricing a comparison from community sources and unverified manufacturer claims is exactly the stacked-speculation problem this section guards against. (The §8.3 confidence clamp does *not* achieve this — it clamps at `0.75`, above the floor — so this is stated as its own rule.)

**By pricing model:**

| `model_type` | Flip point |
|---|---|
| `one_time` | Against price. Normal. |
| `one_time_plus_subscription` | Against `total_cost_1yr`, discount applied to the upfront component only — **subject to the upfront-share gate below.** |
| `freemium` | As its paid tier if that is what is recommended; otherwise none. |
| `subscription_only` | **None.** No meaningful one-time discount. |
| `usage_based` | **None.** No stable price to discount. |
| `financed_major_purchase` | **None.** Price is negotiated and variable. |

Never compute a flip point against a *subscription discount* — those are rare and short-lived.

**Hybrid bounds are stated in the unit actually discounted.** Sampling is at −10/−20/−30% of the **upfront component**, so the bound reads *"would need more than a 30% cut to the upfront price"* — not "a 30% discount," which would imply the TCO moved by 30% when it did not. For a $70 device with a required $6/month subscription (TCO $142), those cuts move TCO by only 4.9 / 9.9 / 14.8%.

Sampling in TCO terms instead is not an option: it is sometimes unsatisfiable. A $20 device with a $10/month subscription has a TCO of $140, and a −30% TCO move would require a negative upfront price.

**Upfront-share gate: suppress the flip point entirely when `upfront_amount` is `None`, `total_cost_1yr` is `None`, or `upfront_amount / total_cost_1yr < 0.25`.** (The schema permits `None` on both; a missing upfront amount means there is no component to discount, so suppression is the correct reading rather than an error.) Below that, the lever is too short for any reachable discount to change the comparison — at a 14% upfront share, even giving the device away moves TCO by 14%. Without the gate, every subscription-dominant hybrid renders the same "more than 30%" bound on every row: technically true, practically noise. Emit one honest statement instead, in the same register low-evidence mode uses: *"No flip point — the upfront price is too small a share of first-year cost for a discount to change this."*

### 5.3 Written recommendations

Two to three paragraphs on the top 2–3 picks. Prose, not bullets — this is where tradeoffs get argued rather than tabulated. When a top pick sits in the `low` or `very_low` band, say so in the prose; do not leave it to the scale's visual encoding.

Where the user's budget note (§7.1) carries conditional nuance, it surfaces here: *"the $195 option is only marginally better than the $140 one — at that gap I'd take the cheaper."*

### 5.4 Sources

Every URL used, grouped by product, with `source_type` and `has_stated_methodology`. Community sources labeled as sentiment, not measurement.

### 5.5 Caveats — tiered

Auto-generated from the data model; requires no model call.

The accepted review set adds many caveat generators — skipped-axis assumptions, constraint relaxations, unmet constraints, prior-gen demotions, undetermined tax conventions, uncertain shipping origin (on most rows, per §10.6), indicative prices, truncation, skill-hash mismatches. Ungoverned, a 12-row table produces 40+ lines. **A caveats section nobody reads is worth the same as no caveats.**

```python
class Caveat(BaseModel):
    tier: Literal["decision_affecting", "provenance"]
    text: str
    anchor: str | None      # product name or table cell it attaches to
    instance_count: int = 1 # for class-collapsed caveats
```

Three rules:

1. **Decision-affecting caveats render inline**, adjacent to what they affect — an undetermined tax convention next to that product's price, a demoted row next to the table.
2. **Provenance and assumption caveats collapse** into the §5.6 disclosure section — skipped-axis defaults, uncertain shipping origin, skill-hash mismatches.
3. **One line per class, not per instance**, where instances share a cause: *"shipping origin inferred rather than confirmed for 9 of 12 products"* beats nine identical lines. This is what actually contains growth.

Always disclosed regardless of tier: source conflicts, single-source values, failed fetches, price timestamps, every constraint relaxation, every logged assumption.

In hand-testing this research process, spec conflicts appeared in **both** categories tried — a desk reported at both 176 lb and 265 lb capacity, and a paddle line whose pricing varied nearly 2.5× across sources depending on which SKU the reviewer had. Silently picking one number is the failure this prevents.

### 5.6 Progressive disclosure

Core comparison, scale, written recommendations, and verdict are **always visible**. Availability detail, secondhand risk factors, provenance caveats, and per-spec source lists are **collapsed by default**. Secondhand information must never dominate the page (§12).

---

## 6. Recommendation logic

Read by Opus in phases 6a and 6b.

### 6.1 Verdict is independent of ranking

Compute `Verdict.action` separately, then **always show the top 2–3 picks anyway**, even when the verdict is "don't buy." An engine that returns "buy nothing" and nothing else is useless.

### 6.2 The five triggers

**`CONSIDER_CHEAPER_CATEGORY`** — an adjacent cheaper category satisfies every stated requirement. Check early; it can reframe the run.

**`KEEP_CURRENT`** — upgrading, and the delta doesn't justify full replacement cost. You pay the entire price of the new thing, not the marginal improvement.

> **Required caveat:** this must not become reflexive anti-upgrade nagging. If the user is jumping several tiers, or has said they want the top end and accepted the cost, that is a legitimate purchase — flag the value math once, then respect the stated preference. Models over-apply frugality advice; test this explicitly.

**`WAIT`** — an imminent successor, category-wide price movement, or a landing technology transition.

**`INSUFFICIENT_EVIDENCE`** — even low-evidence mode can't support a call (§8.5).

**`BUY`** — none of the above.

### 6.3 Confidence must not be laundered into score

A thin-evidence product is **not** a mediocre product. Never resolve uncertainty by lowering a score. A product with one glowing review and no corroboration can legitimately score 8.0 at `very_low` confidence — that combination is informative, and collapsing it to 6.0 destroys the information.

### 6.4 How answers feed scoring

| Input | Effect |
|---|---|
| Gate `must_have` / `must_avoid` | **Hard filter.** Excluded, not down-scored. |
| Gate `persuadable` / `no_preference` | No filtering. `no_preference` down-weights the dimension. |
| **Position axis** | Shifts scoring **toward a pole**. Never excludes the opposite pole. |
| **Importance axis** | Scales the **weight of that dimension**. |
| Free text | Interpreted directly. May encode conditionals and tiebreak rules. |

**Axis answers are soft weights, never filters.** A 0.7-toward-control answer makes control-leaning picks score better; it does not remove power-leaning products from the table. This is why axes exist rather than forced choices — a person at 70/30 should still see the strong option on the other side, argued.

### 6.5 Timing claims are labeled speculation

Every timing claim carries its basis — *"historically refreshed each September," "manufacturer announced a Q4 launch."* Never bare fact. If nothing credible is found, say "no timing signal found" rather than manufacturing one.

Software timing signals differ and the skill covers both: announced price increases, acquisitions (often a leading indicator of degradation or shutdown), sunset notices — not just release cadence.

---

## 7. Phase 0 — intake

### 7.1 The questions

1. **Do you already own a version of this product?** → gates `KEEP_CURRENT`; if upgrading, follow up for the specific model.
2. **Budget** — a single numeric ceiling plus a free-text note. Tiered intent ("under $220 for something good, under $150 for adequate") puts the **highest figure in the ceiling** and the full statement in the note. Opus surfaces the nuance in §5.3. There is no tiered budget model — that tier logic is a judgment about whether a price gap is worth it, and judgments don't belong in schema.
3. **Required features** — **filters, not preferences.**
4. **Products already under consideration** — named candidates enter the shortlist with identical treatment including honest cons. They do **not** count as discovered cluster representatives.
5. **Location**, only if not already in settings (§10.1).

### 7.2 The upgrade baseline row

The user's current product is researched, enters with `role="baseline_current"`, and is **scored on the same 0–10 scale**. This makes `KEEP_CURRENT` visceral: *"your current paddle scores 7.8; the best upgrade in your budget scores 8.1"* beats a paragraph about marginal improvement. It occupies no recommendation slot.

---

## 8. Category assessment and degraded modes

### 8.1 Phase 1 SURVEY

One Haiku pass produces the whole `SurveyReport`. Region scoping happens **here, before clustering** — no point clustering a catalog half of which isn't purchasable.

| Coverage | Signal | Action |
|---|---|---|
| `rich` | ≥8 products, ≥5 independent sources, methodology-backed testing | Proceed |
| `moderate` | 4–8 products, 2–4 independent sources | Proceed; low-evidence for under-covered products only |
| `sparse` | <4 products, or ≤1 independent source | **Halt and ask** (§8.2) |
| `barren` | Effectively no independent coverage | **Halt and ask** (§8.2) |

### 8.1a `Cluster` and `Dimension` are the system's only unsourced values

`Cluster.exemplar_products` and `price_range_native` come from search snippets before extraction — no `source_url`, no tier, no `observed_at`. They then appear in §9.6 question copy, in front of the user, at the exact moment trust is being established. If SURVEY invents a product name, the user is asked about something that doesn't exist.

Four constraints, all enforced:

1. **Exemplar names must appear verbatim in a fetched page or a search-returned result.** Never model-generated.
2. **The unsourced *value* fields never enter the report** — `Cluster.exemplar_products` and `Cluster.price_range_native`. Structural fields may: `Dimension.name` legitimately appears in §9.6's logged assumption copy, which renders as a caveat.
3. **Question copy marks prices indicative** — "around $200," never "$199."
4. **These types deliberately do not use `SourcedValue`**, and this section is why. Do not later "improve" them into report-eligible data.

### 8.2 The broadening interrupt

On `sparse` or `barren`, halt **before spending anything on extraction** and interrupt once, presenting all `suggested_broader_categories` together:

```
Coverage check: "vintage film scanners" has thin independent review coverage.
  Found: 3 products, 1 independent review source, no methodology-backed testing.

  → Research "film scanners" instead                (moderate coverage)
  → Research "flatbed scanners with film adapters"  (rich coverage)
  → Keep my original scope, proceed anyway          (low-evidence mode)
  → Stop here
```

**One interrupt maximum, enforced by a latch.** Set `category_broadening_offered = True` on first fire. If the user broadens and the new category *also* probes sparse, do **not** interrupt again — proceed in low-evidence mode and note it. Re-probing is cheap; re-asking is not, because a user who declined once has answered.

### 8.3 Low-evidence mode

- **Community sources fully admissible** (beyond §14's bounded purposes), always labeled, never as sole basis for a spec value. Manufacturer performance claims admissible, marked `unverified manufacturer claim`.
- **Table constraints scale down**, every relaxation logged.
- **Confidence clamped to `0.75`**, applied after `compute_confidence()`, both values recorded. Note this clamp **rarely binds** — a typical low-evidence profile computes to `0.427`. It exists for the `moderate` coverage path, where a well-covered product sits inside a partially-low-evidence run.
- **Flip points suppressed entirely** (§5.2) — a separate rule, not a consequence of the clamp.
- **The report leads with the limitation** — a banner above the table, not a footnote.

### 8.4 Commodity categories

When `differentiation == "low"` against a large catalog, the archetype-diversity requirement relaxes rather than manufacturing distinctions. Report says so: *"these cluster into effectively two real options, not six."*

### 8.5 `INSUFFICIENT_EVIDENCE`

When even low-evidence mode can't support a call, say so. An agent that manufactures confident recommendations from three forum posts is worse than one that says "there isn't enough here, and here's what's missing."

The verdict still ships the full report — products found, specs with provenance, timing, top picks with low confidence stated — plus a section naming what would resolve the question: a specific community, a retailer with a return window, a spec the user could measure.

**This must not become a shrug.** Same diligence as any run; it changes the *claim strength*, not the *effort*.

---

## 9. The question system

Read by Opus in Phase 2.

### 9.1 Two-stage construction

```
STAGE 1 — GATE  (whenever the topic could be a hard constraint for anyone)
  must_have / must_avoid / persuadable / no_preference
    → must_have | must_avoid  →  becomes a FILTER, topic ends
    → persuadable | no_preference  →  proceed to stage 2

STAGE 2 — PREFERENCE
  [optional axis: position OR importance]  +  [free text: ALWAYS present]
  Both independently skippable.
```

**Gate escape hatch defaults to soft.** Unsure whether something is a requirement → record `persuadable`. An under-classified requirement surfaces as a con (recoverable); an over-classified preference silently eliminates a good option.

### 9.2 Two axis types

| | Position axis | Importance axis |
|---|---|---|
| Example | power ←→ control | how much noise matters |
| Both ends legitimate? | Yes | No — low end means "don't care" |
| Middle means | genuinely balanced | moderate weight |
| Downstream | shift toward a pole | scale that dimension's weight |

Same widget; **different types in schema, different consumption in §6.4.**

### 9.3 Free text is always present

Not an optional elaboration field — a permanent half of the preference stage. Conditional and lexicographic preferences (*"performance first, aesthetics as tiebreaker within 10%"*) cannot be held by a scalar. When a topic has no meaningful axis, the axis is omitted and text carries the answer.

**Why keep the axis then?** Because it is far lower-effort. Someone will tap "7" who would bounce off an open prompt.

### 9.4 Axis kind is decided in SURVEY and recorded

`Dimension.axis_kind` is set during SURVEY, not inferred at question time. Type cannot be derived from a topic's name: **weight** is an importance axis for laptops (lighter is monotonically better) and a position axis for pickleball paddles (a real sweet spot — too light gives up stability, too heavy costs hand speed). Same word, opposite structure.

Recording it as a field rather than an instruction makes it testable by the golden set.

### 9.5 Construction checks

**Dead-question filter.** If no reasonable person would give a low answer — *"how much do you value reliability?"* — the question carries zero information. **Reformulate as a circumstance question:** *"how much do you care about future-proofing"* → *"how long do you expect to keep this?"* One good circumstance question can retire several preference questions.

**Magnitude × direction decomposition.** Where "how much" and "which way" vary independently, one question can't hold both. **Aesthetics** needs an importance axis *and* a direction question. Brand trust is the same shape.

**Compound-tradeoff check.** Verify one coherent underlying quantity exists. *"Portability vs. capacity"* fails — a MacBook with more RAM and storage is far more portable than a gaming laptop with less. Decompose, or drop to free text.

**Hidden-threshold check.** Skill level is continuous except where tournament-legality kicks in at a discrete threshold. Split the cliff into its own gate.

**Floor-then-indifference → multiple choice.** Warranty length looks smooth but is a threshold. Use `ask_choice` with **concrete option text naming the actual threshold** — *"1 year is fine, longer doesn't matter to me"* / *"I want 3+ years"* — never an abstract gesture like "a reasonable minimum," which tells the user nothing.

**Scope.** Not limited to product specs. Any input that would change the recommendation qualifies, financing structure included (§11.4).

### 9.6 Skips and defaults

| Skipped | Default | Logged |
|---|---|---|
| Gate | `persuadable` | Yes |
| Position axis | `0.5` — genuinely balanced | Yes |
| Importance axis | **`0.2` — low weight** | Yes |
| Free text | empty | No |

**The axis defaults are deliberately asymmetric.** A skipped *position* axis genuinely means "balanced between two legitimate poles." A skipped *importance* axis means the user declined to say the dimension matters — `0.5` would invent an opinion from silence. The failure modes differ too: over-weighting an unstated preference distorts the ranking invisibly; under-weighting one surfaces as a con the user can react to.

Logged copy for the importance case: *"You didn't say how much X matters, so I weighted it lightly. If X is actually important to you, Y and Z move up."*

The `ask_choice` two-attempt loop is retained for categorical questions: attempt 1 offers *"Not sure — explain what this changes"*; if taken, Opus explains **grounded in the actual candidates** —

> Bad: *"Dual motors are faster and quieter than single motors."*
> Good: *"Among your candidates this is roughly the $250 thermoformed cluster vs the $200 single-motor one — and it matters more than usual for you because a monitor arm loads one side of the desk, which is where single motors wobble."*

— then re-asks with the hatch swapped to *"No preference — pick a sensible default."* The hatch is **swapped, not counted**, so the bound lives in the UI rather than a counter the model could argue around.

Note the example uses **cluster-level language with indicative prices**, per §8.1a. Named products with exact prices do not exist at Phase 2.

### 9.7 Stopping condition

**The interview is bounded by category structure, not by user response pattern.**

An earlier draft stopped when two consecutive answers "neither eliminated a cluster nor inverted a lean." That misreads `no_preference` as silence. It is a substantive answer — *this dimension doesn't matter to me* — which legitimately down-weights the dimension. A user answering "don't care" about noise and aesthetics would have been cut off before the durability question they cared about.

```
Ask about dimensions in descending order of separating_power(surviving_clusters).

STOP when either:
  (a) PRIMARY — no remaining Dimension has ≥2 distinct values across
      surviving clusters. Nothing left to ask that would distinguish anything.
  (b) BACKSTOP — the last two answers neither eliminated a cluster,
      inverted a lean, nor down-weighted a separating dimension.

FLOOR: min(2, len(separating_dimensions)) topics before either may fire.
CAP:   8 topics.
```

**(a) is computable**, which is the point — `Dimension.separating_power()` counts distinct positions across surviving clusters in Python. It also gives the golden set something stable to assert against.

**(b) is a backstop and will rarely fire.** If you always ask the highest-separating dimension, every answer down-weights something that was separating clusters. Keep it for the pathological case; do not assert it fires.

**Only topics mapped to a `Dimension` can shrink `surviving_clusters`.** `TopicAnswer.dimension_name` is nullable: a free-text-only topic is informational and can neither eliminate a cluster nor advance condition (a). That distinction is the difference between (a) terminating the interview and (a) stalling into the 8-topic cap, so gate questions should be attached to a `Dimension` wherever one exists.

**A "lean" means a prior axis answer at `≤0.35` or `≥0.65` that was explicitly given.** `axis_skipped == True` never establishes a lean — a logged default of `0.2` on a skipped importance axis is a non-answer, and reading it as progress would repeat the exact confusion (a) was written to fix.

**The floor is `min(2, ...)`** so a category with one real dimension doesn't get a manufactured second question.

**The 8-topic cap is a runaway backstop.** With (a) in place it should rarely bind.

### 9.7a The bail-out

`QuestionPort.offer_bailout()` — *"skip the rest and use your best judgment"* — is presented alongside every topic **after the floor has been met**, and never before it, since bailing out of an interview that has asked nothing produces a recommendation with no user input at all.

When taken, REFINE stops immediately. Every remaining topic is recorded as a `TopicAnswer` with `gate_answer = "no_preference"`, skipped axis, and an `assumption_logged` line, so §5.5's caveat machinery discloses exactly what was assumed rather than silently proceeding. One class-collapsed caveat covers the set: *"You asked me to use my judgment for the rest; I assumed no preference on X, Y, and Z."*

This is what actually bounds interaction volume in the CLI. §3.4 is explicit that `ask_topic`'s composition is a presentation improvement there rather than a count reduction — the bail-out and the §9.7 stopping condition are the two mechanisms that keep a terminal run short.

### 9.8 Requirements discovered mid-run

Phase 0 only catches requirements the user knows to name. REFINE may surface **newly discovered candidate requirements** from the dimension landscape — still categorical, just later.

**When a new requirement filters candidates: filter first, re-cluster only on collapse.** Only re-run clustering if the surviving set drops below the backfill floor. If a requirement eliminates *everything*, route through §8.2's halt-and-ask.

---

## 10. Location

### 10.1 Settings

```toml
[location]
country = "US"
currency = "USD"

[display]
output_language = "en"   # English default; changed only on explicit request
units = "imperial"       # derived from country on first run, overridable

[sources]
trusted = []             # §15 seam — parsed, unused
```

Asked once in Phase 0 if missing, written back, reused silently. `scout config set location.country DE` changes the default; `--location DE` overrides one run.

### 10.2 Currency and shipping origin are independent

`price_currency` is what the price is **denominated in**. `ships_from` is where it is **fulfilled from**. A product priced in USD but shipped from within the EU costs an EU buyer nothing in customs despite needing conversion for display.

**Landed-cost research triggers only on a confirmed `ships_from` mismatch — never on currency alone.**

### 10.3 Landed cost — computed only when needed

Products sold in-region get no shipping research; the listed price is the real price. Only confirmed cross-border products get an estimate, **labeled as an estimate**.

~0% added tokens in the common case, 8–12% ceiling in a worst-case all-cross-border run. **Undetermined origin produces no estimate** — "only when needed" means only when *confirmed*.

### 10.3a Tax convention

A US listing is quoted pre-tax; an EU listing is VAT-inclusive. Comparing them without recording which convention applies produces an error larger than the §10.4 threshold that decides whether location affects the recommendation at all — the defect would sit inside the feature it breaks.

`price_tax_inclusive` records the convention where determinable: an explicit "incl. VAT" / "excl. tax" statement, or a jurisdiction whose retail display convention is unambiguous.

**`None` suppresses the §10.4 comparison entirely.** No native-vs-landed percentage is computed and the >15% trigger is not evaluated. Emit instead: *"prices for this product are quoted under an undetermined tax convention; cross-border cost comparison suppressed."* A stated inability to compare beats a confident 18% computed across mismatched conventions.

### 10.4 When location affects the recommendation

Every product gets a mundane availability note regardless.

Location becomes a **first-class input to scoring, flip points, and table placement** only when:

- the product isn't sold in-region, **or**
- landed price exceeds native price by **>15%**, **or**
- landed price pushes it outside the stated budget.

The second and third require `price_tax_inclusive is not None` for **both** prices compared. Where either is undetermined, neither fires and §10.3a's caveat is emitted instead.

Below the threshold: one caveat line. **Unavailable or prohibitively expensive products become reference rows** (§5.1) — marked, excluded from targets, but visible.

### 10.5 Units

Store **native units as published**, convert at render only. The no-source-no-field invariant argues for storing what was actually printed: the source said "8 oz," not "227 g."

### 10.6 Storefront-origin inference

The question is **"is this the region-appropriate storefront for me,"** not literal warehouse geography. A native German storefront implies EU fulfillment, which is what affects shipping cost.

Signals in order of robustness:

1. **Explicit shipping-policy statement.** Authored once, not per-visit personalized.
2. **Country-code TLD** — with a **known-exclusion list** for domain-hacked TLDs carrying no geographic signal: `.io`, `.co`, `.ai`, `.tv`, `.me`, `.fm`, `.ly`, `.gg`, `.sh`.
3. **Displayed currency.**
4. **Page language** — least trustworthy (below). Attempt dialect resolution: Mexican vs. European Spanish via peso/euro and *computadora*/*ordenador*; Quebec French via `.ca`, CAD, *courriel*.
5. **Multiple large markets, nothing disambiguating** → **largest market** (French → France).
6. **English, generic TLD, nothing else** → **US-facing storefront**.

Step 6 is a claim about **the site**, not the user. Compare the inferred region against `location.country` afterward.

**`ships_from_confidence` is derived in Python from `ships_from_signal`**, never model-assigned — the same principle as §4.1. Nothing distinguishes a model's 0.93 from its 0.96, so the threshold is placed against an auditable discrete input.

| `ships_from_signal` | Confidence |
|---|---|
| `shipping_policy` | `0.98` |
| `cctld` | `0.90` |
| `currency` | `0.70` |
| `language` | `0.40` |
| `fallback` | `0.25` |

**Below `0.95`, present the inference and mark it uncertain.** Only an explicit policy statement clears the threshold — the honest outcome given the ranking above. Because this flag attaches to most rows, it is a class-collapsed caveat per §5.5 rule 3.

**Why page language ranks last.** If fetch requests are geo-detected or auto-translated by target sites, observed language reflects the fetching infrastructure's apparent location rather than the site's default market — affecting exactly the mid-to-large retailers most worth reading. TLD and shipping policy are immune; currency mostly is. **This is a build-time verification task (§17.2), not something to architect around on assumption.**

---

## 11. Pricing

### 11.1 Complexity is opt-in per category

For `pricing_complexity: simple` — the large majority — **nothing beyond the plain upfront price ever surfaces.** No subscription fields, no financing language, no TCO line.

### 11.2 Hybrid and recurring models

`PricingModel` covers all six types. `recurring_required_for_core` distinguishes a subscription you must have from one you may want.

### 11.3 Total cost of ownership

`total_cost_1yr` makes hybrid pricing comparable — a $70 device with a required $6/month subscription against a $300 one-time product. Without it, sticker price makes the subscription product look artificially cheap.

**TCO drives scoring and flip points at reduced weight.** Its reliability depends on an assumed ownership duration the system is guessing at.

- A TCO advantage must not override a large fixed-price difference.
- The one-year assumption is stated wherever TCO appears.
- Underweight rather than over-index. A fixed price is a fact; a TCO is a projection.

**TCO is `None` for `usage_based` and `financed_major_purchase`** — usage-based cost depends on consumption the system cannot know, and a financed purchase's price is negotiated. The comparability claim above **does not extend to these two types**, and the report must not imply it does: where a table mixes them with priced products, the TCO column is omitted rather than left blank with an implied zero.

**Computed within a single tax convention.** Where two products carry different `price_tax_inclusive` values, the TCO comparison is suppressed and disclosed rather than normalized — the system does not know the user's applicable rate.

Guidance for the skill: subscriptions suit products that improve continuously (patched live, no version to re-buy); one-time purchases suit feature-stable products. That shapes whether a recurring cost is good value, independent of the arithmetic.

### 11.4 Financing

Available **only** when `pricing_complexity == "financed_major_purchase"`.

**Never gate output on an answer.** If unanswered, **show both**: *"approx. $X paid in full, approx. $Y/month financed over a typical term."* No exhaustive financing engine, no credit-score questions.

---

## 12. Prior generation and secondhand

**New is the expected purchase. Prior-generation *new* is normal expected behavior. Secondhand is supplementary and must never dominate the report.**

### 12.1 The section is conditional

It appears **only when there is something actionable to say.** No "not applicable" line for a first-generation product. No mention of a prior generation whose discount doesn't justify the capability gap.

### 12.2 Three acquisition paths

Prior-gen **new** (clearance, old stock — normal) · prior-gen **used** (supplementary) · current-gen **used** (supplementary). *"The previous model is $80 cheaper new"* and *"last year's is half price used"* are different recommendations.

### 12.3 Prior-generation evaluation

For each shortlisted current-gen product, Phase 5 finds its predecessor: what is the price delta, what is the capability delta? When price delta materially exceeds capability delta, it enters as a first-class row (§5.1's demotion rule governs width).

This is among the highest-value behaviors in the app and the one most review sites structurally cannot provide, being monetized on current-model affiliate links. Expect it to fight the grain of the source material.

### 12.4 `secondhand_risk_factors` — facts, not advice

| Cluster | Examples | Why |
|---|---|---|
| Safety-critical, invisible history | helmets, child car seats, climbing protection | impact damage doesn't show; failure is catastrophic |
| Data / security exposure | storage media, routers, security keys | data remanence, firmware compromise |
| Invisibly degrading consumables | batteries, mattresses, tires | condition unverifiable at purchase |
| High counterfeit / fraud rate | GPUs, memory cards, blacklist-risk phones | market-level risk |

**These attach disclosures; they do not suppress.** Suppression is a pre-decision, and a discount the user might reasonably accept shouldn't vanish because the system decided for them.

**State facts, never verdicts.** Category-level risk is knowable. Whether buying used is right for this person is not — they may have a known seller, a certified refurbisher, or the ability to verify condition. Stated plainly, the facts carry their own weight.

### 12.5 Used pricing

A researched **rough range, labeled an estimate**: *"typically $180–240 used in good condition."*

### 12.6 Reporting principle

**Omit absence-of-relevance; disclose absence-of-information.** A nonexistent prior generation is a fact about the world with no bearing — silence is correct. A failed fetch or relaxed constraint is a limitation *in the research* — always disclosed.

---

## 13. Hooks

Three earn their place; the rest belongs in Python.

**`PreToolUse` on `WebFetch` — source guard.** Check the domain before spending a fetch. **Must loosen in low-evidence mode**, or it blocks the community sources that are the only evidence available.

**`PostToolUse` on `WebFetch` — provenance ledger.** Append every fetched URL (pre- and post-redirect), status, and timestamp. Feeds §4.3's validation and makes §5.4/§5.5 free. Run-scoped.

**`PreToolUse` global — cost cap.** Hard-stop at N fetches / M searches.

> **The counter must live in orchestrator state, not in the hook closure.** §3 issues a separate `query()` per phase with `hooks=HOOKS`; a closure-held counter resets on every one of them, making the effective cap 7N rather than N. The hook reads a counter the orchestrator owns. This fails in the direction of spending money rather than erroring, so it will not announce itself.

### 13.1 Cost-cap termination

When the cap trips, the orchestrator **stops issuing new research calls, completes what it can from what it has, and ships a partial run**: table from completed products, a banner stating research was truncated and where, every unresearched cluster named, and `RunRecord.truncated_at_phase` set.

This is **termination, not a phase skipping another phase** — Python terminating on a resource limit, which is Python's prerogative. See invariant 2's wording.

`rescore` warns on a non-null `truncated_at_phase`: a partial product set analyzed without that signal would produce a confident answer over incomplete research.

---

## 14. Skills and source classification

| Skill | Read by | Contents |
|---|---|---|
| `intake-protocol` | Phase 0 | §7 |
| `question-design` | Phase 2 | §9 — construction, axis types, checks, stopping condition |
| `research-protocol` | Haiku phases | Source classification, `category_kind`, clustering, dimensions, conflicts |
| `market-timing` | Phase 4 | §6.5 |
| `recommendation-logic` | Phases 6a/6b | §6 |
| `report-contract` | Phases 6b/7 | §5 |

### Source type and methodology are orthogonal

A single tier integer cannot carry both. A YouTube reviewer publishing instrumented measurements is `community` by type and methodology-backed by rigor; Amazon is simultaneously a `retailer` (price) and an `aggregator` (reviews).

```python
source_type: Literal["manufacturer", "testing_outlet", "aggregator",
                     "retailer", "community"]
has_stated_methodology: bool
```

`EvidenceProfile.has_methodology_backed_source` is **derived** from the per-source flags, not independently asserted.

**Requirements are written against methodology, not type** — which is what they were always about:

- Specs from `manufacturer` sources where obtainable.
- Judgment from ≥2 independent sources.
- Price from `retailer` with a timestamp. Never specs.
- **Reject any source without `has_stated_methodology` as the *sole* basis for a performance claim.**

### Community sources — bounded purpose in standard mode

**Admissible for:** reliability, longevity, ownership experience over time, and **cons discovery**. Stored in `Product.ownership_notes`.

**Never admissible for:** spec values, or as sole basis for a performance claim.

**Always labeled** as sentiment, never measurement.

This matters for the every-product-needs-a-con invariant: formal reviews are systematically soft on cons and forums are not. *"Fails at six months," "support ghosts you"* reaches a forum long before a methodology-backed review, and often never reaches one.

Community sources carry proportionally more weight for **software and services**, where day-to-day usability lives in discussion, and less for physical goods, where measurement dominates.

**Low-evidence mode:** all sources admissible for all purposes, every claim labeled, `high` confidence unreachable, every relaxation logged.

---

## 15. Trusted sources — schema seam, unwired

Reserve now; retrofitting persisted records is expensive.

- `SourcedValue.from_trusted_source` — always `False` in v5
- `RunRecord.trusted_sources` — always `[]`
- `[sources] trusted` — parsed, unused

**Three constraints for when it is built:**

**A tiebreak nudge, never exclusive authority.** Break ties between comparable claims; never suppress a contrary source, never become the sole basis for a claim.

**Divergence is the most valuable output.** When a trusted source disagrees with consensus, surface it: *"your trusted source rates this highly; three other testers found X."* Silently letting it win produces a report that reflects the user's existing beliefs back at them with a research process laundering it into apparent objectivity. That is the central risk.

**Influence is always disclosed.**

---

## 16. `rescore`, `history`, `config`, `eval`

```
scout research [--location XX] [--resume <run_id>]
scout rescore <run_id> --set "Product Name=199"
scout history [--category <type>]
scout config set location.country DE
scout eval [--stable-only]
```

**`rescore`** loads the record, overrides named prices, re-runs **phases 6a/6b only**, re-renders. No searching, no fetching. Preserves the original, writes a linked new run — the new record's `rescored_from` field carries the original `run_id`.

**Override semantics:** `in_budget`, `total_cost_1yr`, and `landed_price_native` all **recompute**. `price_observed_at` is set to the override time and `price_overridden = True`, so provenance stays honest — the price is user-asserted, not sourced, and the report says so.

**Three refusal/warning conditions:**

- **Location mismatch → refuse.** Region scoping happens in SURVEY and shapes which clusters exist, so a location change cannot be handled by re-running analysis. Direct the user to a fresh run rather than silently answering for the wrong region.
- **`truncated_at_phase` non-null → warn.** The product set is incomplete.
- **Skill-hash mismatch → warn**, naming which skills changed. Not a refusal — re-scoring against improved logic is often the point — but never silent.

**Storage:**

```
~/.product-scout/
├── config.toml
├── index.json                 # derived; rebuildable by scanning runs/
└── runs/<run_id>/
    ├── record.json
    ├── report.html
    └── phases/<n>.json        # checkpoints; §16.1
```

`index.json` is a derived artifact so a corrupted index is never data loss.

### 16.1 Checkpointing and resume

Each phase writes `phases/<n>.json` on completion. `scout research --resume <run_id>` picks up from the last completed phase. Phase 3 can be dozens of fetches; losing it to a transient failure is unacceptable.

### 16.2 Progress output

The long research phases emit per-phase progress. A CLI that asks the user several questions and then goes silent for minutes is the worst available experience, and this is cheap to prevent.

---

## 17. Build order

Steps 1–6 are testable with few or no model calls. Do them first.

1. **Data model** + run store + index + checkpointing. **`confidence.py` with the §4.1 assertion table, the §4.1a property tests, and the coherence validator.** Confidence is deterministic and fully testable before any model call exists — and the §4.0a/§4.0b ratio definitions must be implemented as written, since an undefined denominator was the most consequential defect found in review.
2. **`QuestionPort` + CLI port** — `ask_topic`, `ask_choice`, `offer_bailout`, the two-attempt hatch swap, the §9.6 skip defaults. Stub `web_port.py`.
3. **Settings** + `scout config`.
4. **Phase 0 intake** (§7), including the upgrade baseline path.
5. **Phase 1 SURVEY** (§8.1) + `Cluster`/`Dimension` construction + §8.1a constraints + broadening interrupt + latch.
6. **Renderer** against a hand-written `RunRecord` fixture — HTML, SVG scale with confidence bands and the `0.15` floor, flip-point suppression, caveat tiering, progressive disclosure, unit conversion.
7. **Phase 2 REFINE** (§9), including the §9.7 stopping condition against `Dimension.separating_power()`.
8. **Phase 3 EXTRACTION** on Haiku + **§4.3 ledger validation**. Test that a spec citing an unfetched URL is rejected, and that a redirect does *not* cause false rejection.
9. **Phases 6a/6b** on Opus, with §5.1 constraints enforced in code (two re-prompts max) and the round-number check.
10. **Phases 4–5** — timing and prior-generation, including the demotion rule.
11. **Location** (§10) end to end, including the inference ladder.
12. **Low-evidence** and commodity modes.
13. **Hooks**, cost caps, and §13.1 truncation.
14. **`rescore` / `history`**, including all three refusal/warning conditions.
15. **Golden set** (§17.1). From here on, it gates every skill edit.

### 17.1 The golden set

Everything this app produces is judgment; nothing else in this plan measures whether a recommendation is good. Skills are plain text with no type checking behind them and are the most likely thing to silently degrade.

`eval/cases/` holds **five categories** spanning the modes that matter: one rich, one sparse (low-evidence), one commodity, one cross-border-heavy, one software/subscription.

**Grading splits in two**, because a live-web system's expectations decay:

| Decaying — informational, refresh quarterly | Stable — **gates a skill edit** |
|---|---|
| Must-appear product names | Constraint satisfaction (§5.1) |
| Expected confidence ordering | Every product has ≥1 con |
| Price-sensitive assertions | Source discipline + ledger validity (§4.3) |
| | Verdict *shape* given coverage class |
| | Band tiling and `confidence_band` totality |
| | `axis_kind` recorded for every `Dimension` |
| | The four §4.1a property tests |
| | **All products in a run share one ratio denominator** (§4.0b) |

Only the stable half blocks. Products get discontinued and prices move; a suite that fails for reasons that aren't regressions trains people to ignore it, which is worse than no suite.

**The stable half replays frozen fixtures; it does not hit the network.** Each case stores a fetch ledger plus page snapshots captured when the case was authored, and `scout eval --stable-only` replays them offline. Running stable assertions against the live web would be slow and flaky for reasons unrelated to regressions — precisely the failure the decaying/stable split exists to prevent, reintroduced through the back door. Only the decaying half runs live, and only manually.

**§19.1's weight tuning is meaningless without this and is gated behind it.**

### 17.2 Build-time verification

- **Geo-redirect check (§10.6).** Fetch several known geo-redirecting retailers and observe what comes back. Do not assume.
- **Low-evidence mode** tested against a genuinely obscure category, not a simulated one.
- **`QuestionPort` discipline.** Grep for direct terminal input outside `io/`.
- **`ships_from_confidence` purity.** Grep prompts for the field name; unit-test that derivation is a pure function of `ships_from_signal`.
- **`KEEP_CURRENT` false-positive test.** Confirm the app does not talk a user out of a legitimate multi-tier upgrade.

---

## 18. Known risks

- **Fetch failures are routine.** In hand-testing, two intended sources were blocked by robots.txt in a single run. Report what couldn't be reached; don't retry around it; don't route through alternative fetch methods.
- **Review sites are structurally biased toward current models.** §12.3 is the counterweight.
- **Prices are the most volatile and most decision-relevant field.** Always timestamp.
- **Model-assigned scores drift between versions.** Pin model IDs; hash skills.
- **`KEEP_CURRENT` over-fires if under-specified.** Test the justified-upgrade case.
- **Low-evidence mode is where the app is most likely to become dishonest**, because every incentive points toward a confident-looking report.
- **Opus clusters scores on round numbers** unless checked (§5.2).
- **The confidence band reads as statistical** if the legend is careless. Never call it a confidence interval.
- **Axis answers can silently become filters** if §6.4 is implemented loosely — reintroducing the exact failure axes exist to prevent.
- **Secondhand content can overrun the report.** §12's framing and §5.6's collapsing are both required.
- **Caveats can overrun the report.** §5.5's tiering and class-collapsing are both required; neither alone suffices.
- **`Cluster`/`Dimension` values are unsourced** and appear in question copy. §8.1a's four constraints are the only thing preventing a hallucinated product name from reaching the user at the moment trust is being established.
- **The evidence ratios are only comparable because their denominator is fixed** (§4.0b). If anyone re-derives them over `len(product.specs)`, extraction verbosity silently becomes the dominant input to confidence and cross-product comparison quietly stops meaning anything. The golden-set shared-denominator assertion is what catches this.

---

## 19. Open questions

None blocking. Revisit after real runs. **All tuning items are gated behind §17.1** — the golden set is the only thing that can say whether a change helped, and the pressure to tune will be strongest right after the first live run, when there is a vivid result and no rubric to judge it against.

### 19.1 Confidence weights

Tune against ~10 runs across varied categories, particularly whether the review curve's `0.55` share over-weights categories where three reviews all trace to one press release. **Gated behind §17.1** — tuning without a grading rubric fits noise.

### 19.2 The review curve's `2.5` scale factor

It sets how fast the fourth and fifth reviews stop mattering. **The §4.1a property tests must stay green through any change** — an earlier draft staked a documented guarantee on a `0.019` margin that a modest move here would have erased silently. That is also why §5.2's two-review requirement is a predicate rather than an arithmetic consequence.

### 19.3 Whether the conflict coefficient should rise from `0.40` to `0.45`

Deferred deliberately. `0.40` is the first value clearing the flip floor with headroom, and raising it further only makes sense once the golden set can say whether disputed products are being scored fairly. Normalizing the input (§4.0b) came first for the same reason: a larger coefficient on a noisy measurement amplifies extractor variance rather than reducing it.

### 19.4 Remaining calibration questions

- Whether **soft-8 / hard-12 rows** holds up, now that it is honestly a legibility cap.
- Whether the **archetype cap default of 7** is right.
- Whether the **8-topic cap ever fires** with §9.7(a) in place. If it does, the `Dimension` model is what to fix, not the cap.

### 19.5 Free-trial "try both" mode

For software, freemium tiers and trials make a genuinely different recommendation shape available — *"try these two for a week"* rather than picking one. Deferred, not adopted.
