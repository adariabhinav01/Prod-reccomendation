# Next steps — Product Scout

Working session-to-session notes on what's left, updated as sessions
finish. Distinct from `docs/handoff.md`, which is the frozen v7 build
spec — this file is status, not design. If you're a new session picking
this up: read this file first, then `CLAUDE.md`, then `docs/handoff.md`
§17.1/§17.2 for the spec this all satisfies.

## Current state

CLAUDE.md's checklist: 1–14 done. **Step 15 (golden set, §17.1) is
1/5 complete** — `eval/cases/software/` exists (password managers), the
other 4 categories (rich, sparse, commodity, cross_border) don't yet.
`scout eval --stable-only` currently passes with that 1 case
(`[PASS] software`). Checkbox 15 stays unchecked until all 5 exist — the
spec is explicit that it's 5 categories, not "at least one."

A real `ANTHROPIC_API_KEY` is confirmed working and funded as of this
session (the software capture cost ~$2 in practice, close to the ~$1-2
estimate). Balance can drain between sessions — check the
[Anthropic console usage page](https://console.anthropic.com/settings/usage)
before assuming it's still funded.

## What's left, in priority order

### 1. Investigate: EXTRACTION returns zero products on rich/high-differentiation categories — do this before capturing the `rich` case

Reproduced live, twice in a row, on "Pickleball Paddles" (`coverage=rich`,
`differentiation=high`, 35 then 150 `estimated_product_count`): SURVEY
succeeds, `_extraction_candidates()` hands EXTRACTION a full 12 real
candidates, and EXTRACTION comes back with **zero** validated products —
not a cost-cap truncation (`truncated_at_phase` is `null`, `RunBudget`
never tripped) — so SCORING/SYNTHESIS correctly fall through to
`INSUFFICIENT_EVIDENCE` on an empty shortlist. This is the same shape the
"Resolved this session" note above (`ROW_CAP=12`,
`MAX_EXTRACTION_FETCHES_PER_PRODUCT` 6→4) targeted for the standing-desks
failure — worth checking whether that fix actually holds for `rich`
categories in general, or just happened to fix standing desks.

New progress-tick instrumentation landed this session specifically to
chase this (`hooks/progress.py`'s `PostToolUse` ticks on every
`WebFetch`/`WebSearch`; `tools/record_product.py` ticks on every
`record_product` call, success or rejection; every research phase now
also ticks `format_result_progress` — the terminal `ResultMessage`'s
`terminal_reason`/`num_turns` — when its `query()` call ends). One live
rerun with this instrumentation (second Pickleball Paddles attempt) showed
EXTRACTION making 20 real `WebFetch` calls and **zero** `record_product`
calls of any kind — not rejections, none at all. Several candidates (e.g.
11six24 Power Series, Franklin C45 Dynasty, JOOLA Ben Johns Perseus) got
exactly one clean manufacturer-page fetch and nothing else; meanwhile the
model burned other fetches on search-result pages (`amazon.com/s?k=...`,
site `?q=` search endpoints) rather than real product pages, and never
circled back to call `record_product` for anything, including the easy
candidates. The `terminal_reason` tick wasn't present yet for that run —
next rerun will show it.

**Next step:** rerun the same category (or any `rich`/high-differentiation
category) and read the final tick per phase:
- `terminal_reason=max_turns` (or similar cutoff) → confirms a turn-budget
  problem: the model gets stuck chasing dead-end sources for hard
  candidates and runs out of turns before recording anything, even the
  easy ones. Fix is probably in `EXTRACTION_PROMPT_TEMPLATE`
  (`phases/extraction.py`) — e.g. instruct it to call `record_product` for
  each candidate as soon as it has enough, rather than working through all
  12 before recording any.
- `terminal_reason=completed` with the same zero-`record_product` outcome
  → the model chose to stop on its own without recording anything, which
  points at the extraction research-protocol skill needing a stronger
  nudge rather than a budget fix.

Resolve (or at least understand) this before spending money on the `rich`
golden-set capture below — espresso machines is exactly this
`coverage=rich` shape, and capturing it while this bug is live risks
freezing a broken/empty case into the golden set.

### 2. Capture the other 4 golden-set cases (§17.1) — main remaining item

`eval/capture.py` (committed) is proven end-to-end for one category —
adapt it for the remaining 4. Recommended categories, reasoned out in a
prior session's plan (probe first with the technique below, adjust if a
probe result disagrees):

| Case | Category | Location | Why |
|---|---|---|---|
| commodity | USB-C cables | US | Canonical `differentiation=="low"`, count well over `COMMODITY_CATALOG_FLOOR=10` |
| rich | espresso machines | US | Bounded into ~3-4 mechanism-based clusters (manual lever / semi-auto / super-auto / pod) rather than fragmenting like standing desks did; deep methodology-backed review coverage exists |
| cross_border | mechanical-keyboard artisan keycaps/switches | SG (Singapore, SGD) | Hobby dominated by US/China DTC vendors with explicit international shipping policies — high-confidence `ships_from`; Singapore has no domestic vendors in this niche |
| sparse | probe among: Great Highland Bagpipes, curling brooms, competition fencing sabres | US | Physical niches with a handful of specialist manufacturers and no consumer review-site coverage — strong `coverage in (sparse, barren)` candidates; whichever the probe confirms is safe to use |

**Cost-saving technique, validated this session:** before any full capture,
run a cheap SURVEY-only probe (Haiku-only, zero Opus, well under 30 tool
calls) against the candidate `(product_type, location)` pair — it exposes
`coverage`/`differentiation`/`category_kind`/`estimated_product_count`/
cluster count without paying for REFINE/EXTRACTION/TIMING/PRIOR-GEN/
SCORING/SYNTHESIS. The probe script isn't committed (throwaway, written
to a session scratchpad last time) — recreate it by calling
`phases.survey.run_survey(product_type, location, SdkSurveyor(),
EvalQuestionPort(["yes"]), FetchLedger(), RunBudget(max_fetches=30,
max_searches=15))` directly; see `eval.py`'s `EvalQuestionPort` for why
`["yes"]` not `[]` (SURVEY's zero-broader-category fallback needs one
harmless answer available). Only commit to a full `eval/capture.py` run
once the probe's classifier signal looks right.

**Budget:** the 5-case-total estimate from a prior session was ~$14-36;
1 case (software) actually cost ~$2. Scaling that, the remaining 4 should
run somewhere in the **$8-15** range, plus probe cost (small, Haiku-only).
Check real spend after each capture rather than trusting the estimate
blind — same caution that applied to the first case.

**Cost optimization for re-verification once more cases exist:** running
`scout eval --stable-only` re-invokes live Opus (REFINE+SCORING+SYNTHESIS)
for *every* case directory present, every time. Once multiple cases exist,
isolate a verification run to just the case(s) it actually needs by
temporarily moving the others out of `eval/cases/` — most relevant for
the regression test below, which only needs the `rich` case in play.

### 3. Once all 5 exist

- Run `scout eval --stable-only` for real, confirm all 5 pass.
- **The prose-edit regression test, deferred this session** because no
  `rich` case existed yet: temporarily invert
  `.claude/skills/recommendation-logic/SKILL.md`'s `INSUFFICIENT_EVIDENCE`
  bullet (e.g. "prefer this verdict whenever the evidence is imperfect in
  ANY way, even for a well-covered category"), re-run
  `scout eval --stable-only`, confirm the **rich** case now fails on
  `check_verdict_shape`'s verdict-shape check, then
  `git checkout -- .claude/skills/recommendation-logic/SKILL.md` and
  re-verify it passes again. This is the check that targets pure live-Opus
  judgment with no code-level backstop — more precise than the
  disable-the-whole-skill-directory test this session used as a
  case-kind-independent substitute (that test still passed and is
  reusable, just blunter — it proves a skill is *present*, not that its
  *content* matters).
- Exercise `scout eval` (no flag — live/decaying mode) at least once,
  since it's never been run.
- Flip CLAUDE.md's step 15 checkbox.

### 4. §17.2 build-time verification items — not started, need a live run

- **Geo-redirect check (§10.6).** Fetch several known geo-redirecting
  retailers, observe what actually comes back. Can't be faked or unit
  tested — needs real fetches.
- **`KEEP_CURRENT` false-positive test.** Confirm the app doesn't talk a
  user out of a legitimate multi-tier upgrade. This is Opus's judgment
  call in SYNTHESIS, not Python logic — needs a real run against a genuine
  upgrade scenario (e.g. intake answering "yes, I own an older/base-tier
  model" with a stated preference for the top end regardless of cost).

## Resolved this session — don't re-litigate

- **The extraction-candidate-scope-vs-run-budget tradeoff** (previously
  flagged as a deliberately-deferred open question) **is now fixed**:
  `orchestrator.py`'s `_extraction_candidates()` caps at `ROW_CAP=12` via
  round-robin across surviving clusters (not truncation in cluster order,
  which would starve later clusters), with a caveat emitted whenever the
  cap actually drops something. `MAX_EXTRACTION_FETCHES_PER_PRODUCT` was
  also cut 6→4 (was ~60% of the entire run's fetch budget). Verify this
  holds when the `rich`/espresso-machines case is captured — that's the
  category most likely to exercise many clusters, same shape as the
  standing-desks failure this fix targets.

## Bugs fixed in earlier sessions — confirmed still fixed, don't rediscover

- `config.py`: `MODEL_OPUS` — was a stale ID (`claude-opus-4-5-20260101`),
  rejected outright by the API. Now `claude-opus-5`.
- `phases/refine.py` (and `scoring.py`/`synthesis.py`, same architecture):
  Opus backslash-escapes apostrophes in JSON (`\'`, invalid escape) and
  occasionally flattens nested-field prompts (same field name at two
  nesting levels). Both repaired; retry loop widened to cover
  schema-validation failures too.
- `eval.py`: `SuiteReport.ok` was vacuously `True` for zero cases — fixed
  to fail loudly instead (confirmed still behaves this way).
- `cli.py`: `--location` combined with `--resume` is refused rather than
  silently doing nothing.

## Key files

- `src/product_scout/eval.py` — the whole golden-set harness
  (`capture_case`, `check_verdict_shape`, `run_stable`, `run_live`,
  `run_eval_suite`).
- `eval/capture.py` — the capture CLI, committed, reusable for the
  remaining 4 cases and for §17.1's quarterly decaying-data refresh.
- `eval/cases/software/` — the one captured case so far.
- `src/product_scout/orchestrator.py` — `_extraction_candidates()`, now
  fixed.
- `src/product_scout/hooks/progress.py` — new this session: per-tool-call
  and per-phase `ResultMessage` progress ticks (§16.2); the diagnostic
  instrumentation item 1 above depends on.
- `docs/handoff.md` §17.1/§17.2 — the authoritative spec for all of this.
- `CLAUDE.md` — checklist, the eleven invariants, commands.
