# Product Scout — Build Handoff Spec

**Version 3** — supersedes v1 and v2. Changes in v3: **confidence is now a computed float, not an enum** (§4.1), flip points suppressed below 0.70 confidence (§5.2), and the sparse-category broadening interrupt fully specified (§8.1). All previously-open questions are resolved; §16 now covers only genuinely deferred items.

Carried from v2: sparse-category handling (§8), evidence confidence as a first-class dimension (§5.2, §6), the revised "not sure" loop (§9), trusted-sources schema seam (§12).

**For:** Claude Code
**Target:** A standalone Python application on the Claude Agent SDK that produces balanced, research-backed product recommendations for any product category.

---

## 0. Decisions already locked

Do not re-litigate these; they were decided with the requester.

| Decision | Choice |
|---|---|
| Packaging | Standalone **Claude Agent SDK application** (not a Claude Code plugin) |
| Language | **Python** (`claude-agent-sdk`) |
| Interface | **Terminal CLI now, web later** — question-asking and rendering must sit behind swappable interfaces |
| Report output | **Self-contained local HTML file** (auto-opened) **+ a JSON run record**, enabling a cheap `rescore` command |
| Market-timing research | **Always run** as a dedicated pass, every run |
| Model routing | **Haiku 4.5** for search, fetch, extraction, aggregation. **Opus 5** for analysis, question generation, scoring, recommendations |
| Currency / region | **USD, US market.** Single config value, not yet parameterized |
| Shortlist width | **8 candidates discovered → 6–8 in the final table** (never fewer than 6 when 6 exist; see §8 for sparse categories) |
| Run history | **Indexed history** across categories in `~/.product-scout/` |
| Buy links | **None.** No retailer links, no affiliate tagging. Price citations only |
| Sparse categories | **Probe first, warn, ask before proceeding** (§8) |
| Evidence strength | **Separate dimension from score**, rendered on the scale (§5.2) |
| Trusted sources | **Schema seam built now, behavior unwired** (§12) |
| Score & confidence types | **Both `float`.** Confidence is `0.0–1.0`, deterministically computed (§4.1). Score is `0.0–10.0`, one decimal, model-assigned |
| Flip point suppression | **Suppressed below `0.70` confidence** (§5.2) |
| Category broadening | **One interrupt maximum**, all alternatives presented at once (§8.1) |

---

## 1. What this app does

A single interactive session, in eight phases:

```
  Phase 0  INTAKE        deterministic Python, no model
  Phase 1  PROBE         Haiku ── cheap coverage check; may warn + halt (§8)
  Phase 2  DISCOVERY     Haiku ── search + shortlist candidates
  Phase 3  EXTRACTION    Haiku ── fetch pages, extract specs into validated schema
  Phase 4  TIMING        Haiku ── releases, price trends, tech transitions
  Phase 5  PRIOR-GEN     Haiku ── prior-generation models of each shortlisted product
  Phase 6  REFINE        Opus  ── secondary questions grounded in research, w/ "not sure" loop
  Phase 7  ANALYSIS      Opus  ── verdict, scoring, confidence, flip points, write-up
  Phase 8  RENDER        deterministic Python, no model
```

Phases 1–5 are the expensive token work and all run on Haiku. Phases 6–7 are the judgment work and run on Opus. This split is the single biggest cost lever in the design — do not let extraction drift onto Opus.

---

## 2. Project layout

```
product-scout/
├── pyproject.toml
├── README.md
├── .env.example                        # ANTHROPIC_API_KEY
├── src/product_scout/
│   ├── cli.py                          # `scout research`, `scout rescore`, `scout history`
│   ├── orchestrator.py                 # phase state machine
│   ├── config.py                       # model IDs, caps, source tiers, thresholds
│   ├── models.py                       # pydantic schemas (§4)
│   ├── phases/
│   │   ├── intake.py
│   │   ├── probe.py                    # NEW in v2 — coverage check
│   │   ├── discovery.py
│   │   ├── extraction.py
│   │   ├── timing.py
│   │   ├── prior_gen.py
│   │   ├── refine.py
│   │   └── analyze.py
│   ├── io/
│   │   ├── port.py                     # QuestionPort protocol ← the swappability seam
│   │   ├── cli_port.py                 # build this now
│   │   └── web_port.py                 # stub, build later
│   ├── tools/
│   │   ├── ask_user.py
│   │   ├── record_product.py
│   │   └── server.py                   # create_sdk_mcp_server wiring
│   ├── hooks/
│   │   └── fetch_guard.py              # source allowlist + provenance ledger + cost cap
│   ├── render/
│   │   ├── report.py
│   │   ├── scale.py                    # inline SVG scale, w/ confidence encoding
│   │   └── template.html
│   └── store/
│       ├── runs.py                     # run records: load/save/rescore
│       └── index.py                    # NEW in v2 — cross-category history index
└── .claude/
    └── skills/                         # §11
        ├── intake-protocol/SKILL.md
        ├── research-protocol/SKILL.md
        ├── market-timing/SKILL.md
        ├── recommendation-logic/SKILL.md
        └── report-contract/SKILL.md
```

---

## 3. SDK wiring

Verified against current Agent SDK docs. Field names below are exact.

```python
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition

options = ClaudeAgentOptions(
    model="opus",                                  # main loop = judgment
    agents={
        "scout-prober": AgentDefinition(
            description="Cheaply assesses how much review coverage a product category has.",
            prompt=PROBE_PROMPT,
            tools=["WebSearch"],
            model="haiku",
        ),
        "scout-researcher": AgentDefinition(
            description="Searches the web and shortlists candidate products in a category.",
            prompt=DISCOVERY_PROMPT,
            tools=["WebSearch", "WebFetch"],
            model="haiku",
        ),
        "scout-extractor": AgentDefinition(
            description="Fetches product and review pages and extracts specs into the required schema.",
            prompt=EXTRACTION_PROMPT,
            tools=["WebFetch", "mcp__scout__record_product"],
            model="haiku",
        ),
        "scout-timing": AgentDefinition(
            description="Assesses market timing: upcoming releases, price movement, tech transitions.",
            prompt=TIMING_PROMPT,
            tools=["WebSearch", "WebFetch"],
            model="haiku",
        ),
    },
    mcp_servers={"scout": scout_server},
    allowed_tools=[
        "WebSearch", "WebFetch", "Agent",
        "mcp__scout__ask_user",
        "mcp__scout__record_product",
    ],
    permission_mode="acceptEdits",
    setting_sources=["project"],                   # ← required to load .claude/skills/
    hooks={...},                                   # §10
)
```

**Pin the model aliases.** `"haiku"` / `"opus"` resolve to whatever is current. Put fully-qualified model IDs in `config.py` and resolve aliases there, so a model upgrade doesn't silently change scores between a run and a later `rescore`.

**`setting_sources` is a known gotcha.** Whether filesystem `.claude/` config loads by default has changed across SDK versions. Set it explicitly and add a startup assertion that skills actually loaded — fail loudly rather than silently running without the recommendation logic.

### The `ask_user` custom tool

The seam that makes "CLI now, web later" work. The tool never touches the terminal; it delegates to an injected port.

```python
# io/port.py
from typing import Protocol, Literal

class QuestionPort(Protocol):
    async def ask(
        self,
        question: str,
        options: list[str],
        escape_hatch: Literal["not_sure", "no_preference", "none"] = "not_sure",
    ) -> str: ...
```

```python
# tools/ask_user.py
from claude_agent_sdk import tool

def make_ask_user(port: QuestionPort):
    @tool(
        "ask_user",
        "Ask the user a single multiple-choice question and return their answer.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"},
                            "minItems": 2, "maxItems": 4},
                "why_this_matters": {"type": "string",
                    "description": "One sentence on what this answer changes."},
            },
            "required": ["question", "options", "why_this_matters"],
        },
    )
    async def ask_user(args):
        answer = await port.ask(args["question"], args["options"])
        return {"content": [{"type": "text", "text": answer}],
                "structuredContent": {"answer": answer}}
    return ask_user
```

Building `web_port.py` later means implementing `QuestionPort` against HTTP and swapping the injection. No phase code changes.

---

## 4. Data model

Everything downstream depends on these. Use pydantic; validate hard, fail loudly.

```python
class SourcedValue(BaseModel):
    value: str
    source_url: str                        # REQUIRED — no source, no field
    source_tier: int                       # 1–5, see §11
    corroborated_by: list[str] = []
    conflicting_values: list[str] = []
    from_trusted_source: bool = False       # §12 seam — unwired in v1

class Product(BaseModel):
    name: str
    brand: str
    generation: Literal["current", "prior"]
    price_usd: float
    price_source_url: str
    price_observed_at: datetime
    specs: dict[str, SourcedValue]
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)  # ← HARD CONSTRAINT, §5.1
    strength_archetype: Literal[
        "value", "performance", "aesthetic",
        "durability", "ergonomics", "features", "support",
    ]
    in_budget: bool
    review_sources: list[str] = Field(min_length=1)
    evidence: EvidenceProfile              # NEW in v2

class EvidenceProfile(BaseModel):
    """How much we actually know about this product."""
    independent_review_count: int          # distinct Tier 2/3 sources
    has_tier1_specs: bool
    has_methodology_backed_source: bool    # ≥1 source disclosing test methodology
    corroboration_ratio: float             # 0.0–1.0: specs with ≥1 corroborating source
    conflict_ratio: float                  # 0.0–1.0: specs with conflicting values
    confidence: float = Field(ge=0.0, le=1.0)   # COMPUTED, not model-assigned (§4.1)
    confidence_note: str                   # one line: what drove this level

class Scored(BaseModel):
    product_name: str
    score: float = Field(ge=0.0, le=10.0)  # one decimal, model-assigned judgment
    confidence: float = Field(ge=0.0, le=1.0)   # copied from EvidenceProfile
    rationale: str
    flip_point_usd: float | None           # None when confidence < 0.70 (§5.2)
    flip_point_note: str | None
    score_at_minus_10pct: float
    score_at_minus_20pct: float

class Verdict(BaseModel):
    action: Literal["BUY", "WAIT", "CONSIDER_CHEAPER_CATEGORY",
                    "KEEP_CURRENT", "INSUFFICIENT_EVIDENCE"]   # last is new in v2
    reasoning: str
    timing_note: str | None

class BroaderCategory(BaseModel):          # NEW in v3
    name: str                              # e.g. "film scanners"
    rationale: str                         # why this would have better coverage
    estimated_coverage: Literal["rich", "moderate", "sparse", "barren"]

class CoverageReport(BaseModel):           # output of Phase 1
    estimated_product_count: int
    independent_review_sources_found: int
    has_methodology_backed_testing: bool
    coverage: Literal["rich", "moderate", "sparse", "barren"]
    suggested_broader_categories: list[BroaderCategory] = []   # NEW in v3
    notes: str

class RunRecord(BaseModel):                # ← persisted as JSON
    run_id: str
    created_at: datetime
    product_type: str
    intake: dict
    refine: dict
    coverage: CoverageReport
    low_evidence_mode: bool
    original_product_type: str | None      # set if the user broadened the category
    category_broadening_offered: bool      # NEW in v3 — latch, see §8.1
    products: list[Product]
    timing: TimingAssessment
    verdict: Verdict
    scores: list[Scored]
    caveats: list[str]
    model_ids: dict[str, str]              # for reproducible rescore
    trusted_sources: list[str] = []        # §12 seam — always empty in v1
```

**The `source_url` requirement on every `SourcedValue` is the primary hallucination guard.** An extractor that cannot cite a URL for a spec must omit the field, not invent it. Enforce in validation, not just in the prompt.

### 4.1 Confidence is computed, not judged

**Score and confidence are both floats, but they are produced by completely different mechanisms, and conflating them is the main way this part of the design goes wrong.**

`score` is a **judgment** — does this product fit this user? — assigned by Opus. §5.2 explicitly forbids reducing it to a weighted formula, because a formula produces false precision and argues worse than a reasoned score.

`confidence` is a **measurement** — how much do we actually know? — computed deterministically in Python from `EvidenceProfile` counts. A formula is exactly right here, because counting evidence is a counting problem. The model never assigns it.

This is not an inconsistency. Judgments resist formulas; measurements demand them.

```python
def compute_confidence(e: EvidenceProfile) -> float:
    raw = (
        0.15 * float(e.has_tier1_specs)
      + 0.20 * float(e.has_methodology_backed_source)
      + 0.40 * min(e.independent_review_count, 4) / 4.0
      + 0.15 * e.corroboration_ratio
      + 0.10 * (1.0 - e.conflict_ratio)
    )
    return round(min(max(raw, 0.0), 1.0), 3)
```

Sanity checks the implementer should assert:

| Situation | Confidence |
|---|---|
| Tier 1 specs, methodology-backed testing, ≥4 independent reviews, fully corroborated, no conflicts | `1.000` |
| Tier 1 specs, methodology source, 2 reviews, half corroborated, no conflicts | `0.725` |
| One review, no Tier 1 specs, no methodology, no corroboration, no conflicts | `0.200` |

Two properties this buys you: `rescore` months later produces identical confidence values from the same stored evidence, and the model cannot inflate its own certainty — a real failure mode when a model is asked to self-report reliability.

**Display bands** are derived for readability; the stored value is always the float.

| Label | Range |
|---|---|
| High | `0.85 – 1.00` |
| Moderate | `0.65 – 0.84` |
| Low | `0.40 – 0.64` |
| Very low | `0.00 – 0.39` |

---

## 5. Output contract

Five sections, in this order.

### 5.1 Comparison table

**6–8 rows.** Discovery finds 8 candidates; keep all 8 in the table when all 8 are genuinely distinct, narrow to 6 when some are near-duplicates. Never drop below 6 when 6 viable products exist. (Sparse categories relax this — see §8.)

Hard constraints, enforced in code after Opus returns, with a re-prompt on failure:

- **Every row must have at least one con.** The app is not a sales tool. An empty `cons` list is a validation failure, not a perfect product.
- **At least 3 in-budget options with *distinct* `strength_archetype` values.** This is what stops the table from being three near-identical mid-tier picks. "Aesthetic" is a legitimate archetype — some people are buying a thing they look at every day.
- **At least 1 standout above budget**, clearly marked, included even when the budget is hard. The user should see what they're not buying.
- **Include prior-generation models** where Phase 5 found a better value proposition than the current generation.

All three constraints **scale down to what exists** rather than failing. In a category with four total products, require 2 distinct archetypes, not 3. Log the relaxation into `caveats` so the report says why the table is thin.

### 5.2 The recommendation scale

A 0–10 horizontal scale as inline SVG. Each product plots as a datapoint labeled with **name, price, and score**.

Scoring anchors — give Opus these verbatim; don't let it invent band meanings:

| Band | Meaning |
|---|---|
| 9.0–10.0 | Best fit. Buy this. No material reservation given the stated constraints. |
| 7.5–8.9 | Strong. Recommend with one named tradeoff. |
| 6.0–7.4 | Solid and defensible. Real compromises. |
| 4.5–5.9 | Situational. Only right if one specific preference dominates. |
| 3.0–4.4 | Weak. Better options exist at similar price. |
| 0.0–2.9 | Do not recommend. |

Rules on top:

- Scores are a **holistic judgment**, not a weighted formula. Do not build a numeric rubric — it produces false precision and argues worse than a reasoned score. (Contrast with confidence, which *is* computed — see §4.1.)
- **No two products may score within 0.2** unless Opus explicitly states they're equivalent and says why. Clustered scores defeat the purpose of the scale.
- **Scores must use the decimal place meaningfully.** Models gravitate to round numbers; a table where every score ends in `.0` or `.5` means Opus is bucketing rather than discriminating. Add a validation warning when more than half the scores land on a `.0`/`.5` boundary, and re-prompt.

**Confidence is a separate visual dimension.** A product scoring 8.5 on one review is not the same claim as 8.5 backed by six independent testers, and collapsing those into one number destroys the distinction. Render confidence as a horizontal band around each datapoint, with the half-width a continuous function of the confidence float:

```python
band_half_width = round(2.0 * (1.0 - confidence), 2)   # in score units
```

| Confidence | Band half-width |
|---|---|
| `1.00` | ±0.00 |
| `0.85` | ±0.30 |
| `0.65` | ±0.70 |
| `0.40` | ±1.20 |
| `0.00` | ±2.00 |

The band is a **legibility device, not a statistic** — never present it as a confidence interval or attach a probability to it. Label the legend plainly: "wider band = thinner evidence."

**Flip points are the feature that makes the scale worth building.** For every product below the #1 pick, Opus computes `flip_point_usd`: the price at which it would overtake the top pick. This is the direct answer to "I found 20% off — does that change anything?" Render as a ghosted marker with a connector to the current price.

**Suppress flip points below `0.70` confidence.** Set `flip_point_usd = None` and render nothing. A price at which a barely-evidenced product would overtake a well-tested one is not a useful number — it's two stacked speculations presented with the precision of a single fact. Where suppressed, show the reason inline rather than leaving a silent gap: *"No flip point — evidence too thin to price the comparison."*

Note the interaction with §8.2: in low-evidence mode confidence is capped at `0.75`, so most products fall below the `0.70` threshold and flip points largely disappear. That is the intended behavior, not a bug to work around — the scale in a data-poor category should look visibly less precise than one in a well-covered category.

### 5.3 Written recommendations

Two to three paragraphs on the top 2–3 picks, with reasoning. Prose, not bullets. This is where tradeoffs get argued rather than tabulated. When a top pick has `low` or `very_low` confidence, that must be stated in the prose, not left to the scale's visual encoding.

### 5.4 Sources

Every URL used, grouped by product, with assigned tier (§11).

### 5.5 Data reliability caveats

Auto-generated from the data model — this section should require no model call:

- Any `SourcedValue` with non-empty `conflicting_values` → "Sources disagree on X: A reports N, B reports M."
- Any spec with `corroborated_by == []` → "Single-source, unverified."
- Any page that failed to fetch (robots.txt, timeout) → named explicitly, so the user knows what wasn't consulted.
- Price observation timestamps, with a note that prices move.
- Every constraint relaxation from §5.1 and §8.
- Every `no_preference` assumption from §9.

Not optional, not decoration. In hand-testing this research process, source-level spec conflicts appeared in **both** categories tested — a desk whose weight capacity was reported as both 176 lb and 265 lb, and a paddle line whose pricing varied by nearly 2.5× across sources depending on which SKU the reviewer actually had. Silently picking one number is the failure mode this section exists to prevent.

---

## 6. Recommendation logic

Implemented as the `recommendation-logic` skill (§11), read by Opus in Phase 7.

### 6.1 Verdict is separate from ranking

Compute `Verdict.action` **independently** of the ranking. Then — critically — **always still show the top 2–3 picks**, even when the verdict is "don't buy." A recommendation engine that returns "buy nothing" and nothing else is useless; the picks are what make the advice actionable if the user disagrees with the verdict.

### 6.2 The five verdict triggers

**`CONSIDER_CHEAPER_CATEGORY`** — an adjacent, cheaper category satisfies every stated requirement. If nothing in the intake requires what an electric razor uniquely provides, say so and price out safety razors alongside. Check this *before* deep research; it can reframe the whole run.

**`KEEP_CURRENT`** — the user is upgrading and the delta doesn't justify full replacement cost. The framing that matters: you pay the *entire* price of the new thing, not the marginal improvement over the old one. A 4070 → 4080 jump is a modest step for a full GPU purchase.

  *Required caveat:* this must not become reflexive anti-upgrade nagging. If the user is jumping several tiers, or has said they want the top end and accepted the cost, that's a legitimate purchase — flag the value math once, then respect the stated preference. Encode this explicitly; models over-apply frugality advice.

**`WAIT`** — market timing says hold. Three sub-cases from Phase 4:
  - A successor is imminent (new phone weeks away → current model drops).
  - Category-wide pricing is moving (RAM/GPU/panel cycles).
  - A technology transition is landing (DDR4→DDR5, a new wireless standard) that would strand a purchase made today.

**`INSUFFICIENT_EVIDENCE`** *(new in v2)* — the category is so thinly covered that no responsible recommendation is possible (§8). Still show the top picks, still show everything found, but lead with the honest statement that the evidence doesn't support a confident call, and name what would resolve it — a specific community, a hands-on trial, a retailer with a return window.

**`BUY`** — none of the above applies.

### 6.3 Prior-generation evaluation

For every shortlisted current-gen product, Phase 5 finds its immediate predecessor and answers: what's the price delta, and what's the feature delta? When the price delta materially exceeds the feature delta, the prior generation enters the table as a first-class row, not a footnote. This is one of the highest-value behaviors in the app and the one most review sites structurally can't provide, because they're monetized on current-model affiliate links.

### 6.4 Confidence must not be laundered into score

A thin-evidence product is **not** the same as a mediocre product, and Opus must not resolve uncertainty by lowering the score. "We don't know" and "probably not great" are different claims and get encoded in different fields. A product with one glowing review and no corroboration can legitimately score 8.0 with `very_low` confidence — that combination is informative, and collapsing it into a 6.0 destroys the information.

### 6.5 Timing claims must be labeled as speculation

Phase 4 output is inherently predictive. Every timing claim carries its basis — "historically refreshed each September," "manufacturer has announced a Q4 launch," "analysts expect prices to fall" — and never appears as bare fact. If Phase 4 finds nothing credible, it says "no timing signal found" rather than manufacturing one.

---

## 7. Phase 0 intake questions

Fixed set, asked before any model call:

1. **Do you already own a version of this product?** → new purchase vs. upgrade. Gates `KEEP_CURRENT`; if upgrading, follow up for the specific current model.
2. **Budget:** hard ceiling / flexible-if-quality-justifies / no limit. A hard budget still yields an above-budget reference row (§5.1).
3. **Required features** — these become **filters, not preferences**: a product missing a required feature is excluded, not down-scored.
4. **Products already under consideration** — named candidates enter the shortlist automatically and get the same treatment as discovered ones, including honest cons.

---

## 8. Sparse and niche categories

**The problem.** The §11 tiering rules are a quality floor that quietly assumes an abundant category. In a niche category — obscure hobby equipment, professional tools with a handful of manufacturers, regional brands, anything without an enthusiast review economy — that floor is unsatisfiable. The dangerous failure isn't the agent erroring out; it's the agent silently relaxing its standards and presenting thin data with the same confidence it presents rich data.

**The principle: degrade explicitly, never silently.**

### 8.1 Phase 1 — the probe

Before any expensive work, run a cheap coverage check on Haiku (search only, no fetching; cap at ~4 searches). Produce a `CoverageReport`:

| Coverage | Signal | Action |
|---|---|---|
| `rich` | ≥8 products, ≥5 independent review sources, methodology-backed testing exists | Proceed normally |
| `moderate` | 4–8 products, 2–4 independent sources | Proceed, enable low-evidence mode for under-covered products only |
| `sparse` | <4 products, or ≤1 independent source | **Halt and ask the user** |
| `barren` | Effectively no independent coverage | **Halt and ask the user** |

### 8.1a The broadening interrupt

On `sparse` or `barren`, **halt before spending anything on extraction** and interrupt the user once. The cost logic is the whole point: a full research run on a category with nothing to find is the most expensive way to learn the category has nothing to find, and the probe is cheap enough to catch it first.

The probe also returns `suggested_broader_categories`. Present **all of them in a single interrupt** — never one at a time, never a second round:

```
Coverage check: "vintage film scanners" has thin independent review coverage.
  Found: 3 products, 1 independent review source, no methodology-backed testing.

  → Research "film scanners" instead          (moderate coverage)
  → Research "flatbed scanners with film adapters"  (rich coverage)
  → Keep my original scope, proceed anyway    (low-evidence mode)
  → Stop here
```

**One interrupt maximum, enforced by a latch.** Set `RunRecord.category_broadening_offered = True` the first time this fires, and gate the interrupt on it. The edge case that matters: the user picks a broader category, and *that* category also probes as sparse. Do **not** interrupt again — proceed in low-evidence mode and note it in `caveats`. Re-probing after a broaden is fine and cheap; re-*asking* is not, because a user who has already declined to broaden once has answered the question.

If the user keeps their original scope, record `original_product_type` and continue in low-evidence mode without further prompting. If they broaden, set `original_product_type` to what they first asked for, so the report can say plainly that the scope shifted.

### 8.2 Low-evidence mode

When enabled, four things change:

**Tier 5 becomes admissible.** Forum threads, subreddit consensus, YouTube reviews, and retailer owner-reviews are normally excluded. In low-evidence mode they're admissible — but *only* labeled as such, and *never* as the sole basis for a spec value (only for judgment). Manufacturer performance claims become admissible too, marked `unverified manufacturer claim` in every place they appear.

**Table constraints scale down** (§5.1) rather than failing. Log every relaxation to `caveats`.

**Confidence compresses downward.** In low-evidence mode, clamp computed confidence to a hard ceiling of `0.75`, putting `high` out of reach by construction. Apply the clamp *after* `compute_confidence()` and record both values, so the run record shows what was computed and what was capped. The scale's bands widen accordingly — the reader should see at a glance that this is a different quality of answer. Flip points mostly vanish as a consequence (§5.2), which is correct.

**The report leads with the limitation.** A banner above the table stating the coverage finding and what it means, not a footnote at the bottom.

### 8.3 The `INSUFFICIENT_EVIDENCE` verdict

When even low-evidence mode can't support a call, the honest output is to say so. This is a feature, not a failure — an agent that manufactures confident recommendations from three forum posts is worse than one that says "there isn't enough here, and here's specifically what's missing."

That verdict still ships the full report: the products found, their specs with provenance, the timing assessment, and the top 2–3 picks with their (low) confidence stated. It adds a section naming what would actually resolve the question — a specific community that would know, a retailer with a generous return window, a spec the user could measure themselves.

**Do not let this trigger become a shrug.** It requires the same research effort as any other run; it changes the *claim strength*, not the *diligence*.

---

## 9. The "not sure" loop

A deterministic state machine in `phases/refine.py`. Do not leave this to model discretion.

```
ATTEMPT 1
  Opus generates a secondary question grounded in the ACTUAL researched candidates,
  with 2–4 options.
  The port appends a final option: "Not sure — explain what this changes".

  If the user picks it:
    a. Call Opus with the question + full research context.
    b. Generate an explanation GROUNDED IN THE ACTUAL CANDIDATES, never generic.
         Bad:  "Dual motors are faster and quieter than single motors."
         Good: "Among your five candidates this is the $249 EN1 vs the $199 Fezibo —
                and it matters more than usual for you because a monitor arm loads
                one side of the desk, which is where single motors wobble."
    c. Go to ATTEMPT 2.

ATTEMPT 2
  Re-ask the SAME question with the explanation displayed above it.
  The escape-hatch option is now "No preference — pick a sensible default for me".
  "Not sure" is NOT offered again.

  If the user picks "No preference":
    - record the answer as `no_preference`
    - choose the safer default
    - append to report caveats: "You had no preference on X; I assumed Y.
      If that's wrong, Z changes."
```

The escape hatch is swapped rather than counted — the loop is bounded by what the UI offers, not by a counter the model could talk its way around. `QuestionPort.ask()` takes the `escape_hatch` parameter for exactly this; the port owns which hatch is rendered, and no phase code needs to track attempt state.

---

## 10. Hooks worth adding

For an SDK app the honest answer is *mostly no* — you own the control loop, so orchestration belongs in Python. Three exceptions earn their place:

**`PreToolUse` on `WebFetch` — source guard.** Check the target domain against the tier list before spending a fetch. Deny known content farms outright via `permissionDecision: "deny"` with a reason the model can read and route around. **In low-evidence mode this guard must loosen**, or the agent will be blocked from the forum and community sources that are the only evidence available — wire it to read the mode flag.

**`PostToolUse` on `WebFetch` — provenance ledger.** Append every fetched URL, status, and timestamp to the run record automatically. Makes §5.4 and §5.5 free rather than something the model must remember, and captures failed fetches the model would otherwise silently omit.

**`PreToolUse` global — cost cap.** Hard-stop at N fetches / M searches. Research fans out easily; without a cap a run can quietly cost more than the price difference being researched.

**Do not** implement intake questions as a hook. They're deterministic and belong in `phases/intake.py`, before any model call.

---

## 11. Skills to build

Five skills under `.claude/skills/`. Keeping this logic in skills rather than Python string constants means it can be revised without touching code, and the same protocol ports cleanly if this later becomes a plugin.

| Skill | Read by | Contents |
|---|---|---|
| `intake-protocol` | Phase 0/6 | Preliminary question bank, per-category adaptation, the §9 loop contract |
| `research-protocol` | Haiku phases | Source tiering below, extraction rules, conflict handling, low-evidence mode rules |
| `market-timing` | Phase 4 | Release cadence, price cycles, tech transitions; what counts as credible; speculation labeling |
| `recommendation-logic` | Phase 7 | §6 in full — verdict triggers, scoring anchors, confidence separation, flip points, the anti-nagging caveat |
| `report-contract` | Phase 7/8 | §5 in full — table constraints, scale rules, section order |

### Source tiering

1. **Tier 1 — Manufacturer / first-party spec pages.** Authoritative for specs, useless for judgment.
2. **Tier 2 — Independent testing outlets** with a stated methodology (they say how many units they tested, or publish measurements).
3. **Tier 3 — Review aggregators and enthusiast sites.** Good for consensus signal, variable rigor.
4. **Tier 4 — Retailer listings.** Current price only, with timestamp. Never for specs.
5. **Tier 5 — Community sources** (forums, subreddits, video reviews, owner reviews). **Excluded by default; admissible only in low-evidence mode**, always labeled, never as the sole basis for a spec value.

**Standard mode requires:** specs from Tier 1 where obtainable, judgment from ≥2 Tier 2/3 sources, price from Tier 4 with a timestamp. Reject anything without a disclosed testing methodology as the *sole* source for a performance claim.

**Low-evidence mode relaxes to:** any tier admissible, every claim labeled with its tier, `high` confidence unreachable, and every relaxation logged to `caveats`.

---

## 12. Trusted sources (schema seam — not wired in v1)

The requester plans to let users nominate trusted sources that get prioritized in research. **Build the schema and config seam now**; leave the behavior unimplemented. Retrofitting fields into already-persisted run records is the expensive path.

**Reserve now:**
- `SourcedValue.from_trusted_source: bool` (always `False` in v1)
- `RunRecord.trusted_sources: list[str]` (always `[]` in v1)
- A config key `trusted_sources` reading from `~/.product-scout/config.toml`, parsed but unused

**When it is built, three design constraints matter more than the weighting itself:**

**Trusted sources get a tiebreak nudge, never exclusive authority.** The requester's own framing — "prioritize slightly, but still do a holistic review" — is the right instinct and should be enforced structurally, not left to prompt wording. Concretely: a trusted source promotes one tier for ordering and breaks ties between comparable claims. It never suppresses a contrary source and never becomes the sole basis for a claim.

**Divergence is the most valuable output, not a problem to smooth over.** When a trusted source disagrees with broader consensus, that disagreement is surfaced explicitly: "Your trusted source rates this highly; three other testers found X." Silently letting the trusted source win would produce a report that merely reflects the user's existing beliefs back at them, with a research process laundering it into apparent objectivity. That's the central risk of this feature and the thing to design against.

**Trusted-source influence is always disclosed in the report.** Any claim or ranking materially affected gets marked, so the user can tell what came from their own priors versus independent research.

---

## 13. `rescore` and `history`

```
scout rescore <run_id> --set "FlexiSpot EN1=199" --set "Uplift V2=499"
```

Loads the run record, overrides named prices, re-runs **only Phase 7** (Opus scoring) against already-extracted product data, and re-renders. No searching, no fetching. Preserves the original run and writes a new one linked to it. Warn on staleness when the source run is >30 days old.

```
scout history [--category <type>]
```

Indexed history in `~/.product-scout/`:

```
~/.product-scout/
├── config.toml            # trusted_sources (§12), caps, defaults
├── index.json             # run_id → {category, created_at, verdict, top_pick, run_path}
└── runs/<run_id>/
    ├── record.json
    └── report.html
```

Keep `index.json` a derived artifact — rebuildable by scanning `runs/`, so a corrupted index is never data loss. `history` supports "what did I look at last time" and makes repeat research in the same category cheap to compare.

---

## 14. Build order

1. Data model (`models.py`) + run store + index, **including `compute_confidence()` with the §4.1 assertion table as unit tests**. Everything hangs off the schemas, and confidence being deterministic means it's fully testable before any model call exists.
2. `QuestionPort` + CLI port, including the two-attempt escape-hatch swap (§9). Get the interaction seam right before anything depends on it.
3. Phase 0 intake, end to end, no model calls.
4. Phase 1 probe + the coverage gate (§8.1). Cheap, and it shapes every downstream phase.
5. Discovery + extraction on Haiku with `record_product`. Verify the source-URL requirement holds under pressure.
6. Renderer with fixture data — build the HTML and the SVG scale (including confidence bands) against a hand-written `RunRecord` before wiring Opus.
7. Phase 7 analysis on Opus, with table/scale constraints enforced in code.
8. Timing and prior-gen passes.
9. Low-evidence mode end to end. **Test against a genuinely obscure category**, not a simulated one.
10. Hooks and cost caps.
11. `rescore` and `history`.

Steps 1–6 are testable without spending much on models. Do them first.

---

## 15. Known risks

- **Fetch failures are routine, not exceptional.** In hand-testing, two intended sources were blocked by robots.txt in a single run. Handle as a normal path: report what couldn't be reached, don't retry around it, don't route through alternative fetch methods.
- **Review sites are structurally biased toward current models** because affiliate revenue lives there. The prior-generation pass (§6.3) is the counterweight; expect it to fight the grain of the source material.
- **Prices are the most volatile field and the most decision-relevant.** Always timestamp; never present a promo price as list price.
- **Model-assigned scores drift between model versions.** Pin model IDs in the run record so a `rescore` months later is comparable.
- **`KEEP_CURRENT` will over-fire if under-specified.** Test explicitly against a case where the upgrade *is* justified, and confirm the app doesn't talk the user out of a reasonable purchase.
- **Low-evidence mode is the most likely place for the app to become dishonest**, because every incentive points toward producing a confident-looking report. The banner, the compressed confidence ceiling, and the `INSUFFICIENT_EVIDENCE` verdict are the three guards; test that all three actually fire.
- **The confidence dimension can be misread as statistical.** It is a computed evidence tally rendered as a width, not a probability. Keep the legend plain-language, never attach probabilities to the bands, and never call the band a confidence interval in user-facing copy.
- **Opus will cluster scores on round numbers** unless checked. Watch for tables where every score ends in `.0` or `.5` — that means the model is bucketing into bands rather than discriminating between products, which quietly defeats the scale. The §5.2 validation warning exists for this.

---

## 16. Open questions

None blocking. Every previously-open item is resolved and folded into §0 and the relevant sections. Two things to revisit **after** the first real runs, when there's evidence to decide on:

1. **Tune `compute_confidence()` weights against real runs (§4.1).** The five weights are a reasoned starting point, not a validated model. After ~10 runs across varied categories, check whether the outputs match intuition — particularly whether `independent_review_count` at 40% is over-weighted for categories where three reviews all trace back to the same press release. Adjust the weights, not the mechanism.
2. **Whether `0.70` is the right flip-point floor (§5.2).** Chosen to sit just above the `moderate` band's lower bound. If real runs show useful flip points being suppressed on products that are actually well-understood, lower it to `0.65` to align exactly with the band boundary.
