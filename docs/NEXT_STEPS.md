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

A real `ANTHROPIC_API_KEY` is confirmed working and funded as of the
2026-08-31 session (the software capture cost ~$2 in practice, close to
the ~$1-2 estimate; that same session's EXTRACTION bug investigation below
spent ~$6.49 more). Balance can drain between sessions — check the
[Anthropic console usage page](https://console.anthropic.com/settings/usage)
before assuming it's still funded.

**2026-08-31 session:** found and fixed the EXTRACTION-returns-zero-
products bug that was blocking the `rich` golden-set capture — see
"Resolved this session" below for the full writeup (root cause, what was
ruled out, the fix, and live verification). The `rich`/espresso-machines
capture (priority 1 below) is now unblocked.

## What's left, in priority order

### 1. Capture the other 4 golden-set cases (§17.1) — main remaining item

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

### 2. Once all 5 exist

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

### 3. §17.2 build-time verification items — not started, need a live run

- **Geo-redirect check (§10.6).** Fetch several known geo-redirecting
  retailers, observe what actually comes back. Can't be faked or unit
  tested — needs real fetches.
- **`KEEP_CURRENT` false-positive test.** Confirm the app doesn't talk a
  user out of a legitimate multi-tier upgrade. This is Opus's judgment
  call in SYNTHESIS, not Python logic — needs a real run against a genuine
  upgrade scenario (e.g. intake answering "yes, I own an older/base-tier
  model" with a stated preference for the top end regardless of cost).

## Resolved this session — don't re-litigate

- **EXTRACTION recorded zero products on `rich`/high-differentiation
  categories (2026-08-31 session).** Reproduced live, repeatedly, on
  "Pickleball Paddles" (`coverage=rich`, `differentiation=high`): SURVEY
  and REFINE succeeded, EXTRACTION got a real 10-candidate list, made real
  fetches, and recorded **zero** products — not a cost-cap truncation
  (`RunBudget` never tripped).

  Four candidate explanations were checked, in order, each with evidence,
  three ruled out:
  1. *App-level orchestration loop bug* in `run_survey`'s §8.2 broadening
     interrupt — ruled out for free (zero API cost):
     `tests/test_survey.py`'s `FakeSurveyor` tests (35/35 passing) already
     prove `.survey()` is called at most once when the port answers "keep"
     (which `EvalQuestionPort` always does).
  2. *SDK/session-level restart* — a single `query()` call's own message
     stream can emit more than one terminal `ResultMessage` (confirmed as
     real, separate SDK behavior on SURVEY itself: a method-level call
     counter showed `call_counts=1` while `tick_counts=2`, i.e. one Python
     call, two billed segments) — checked directly against EXTRACTION with
     the same instrumentation and ruled out as *this* bug's cause:
     `call_counts=1`/`tick_counts=1`, exactly 1:1, still 0 products.
  3. *Turn-budget cutoff* — ruled out: `terminal_reason=completed`,
     `num_turns=102`, the model finished on its own initiative, not cut
     off mid-research.
  4. *Batching every `record_product` call to the end* — tested with a
     prompt fix ("work one candidate at a time, record before moving on")
     and verified live: turn count dropped sharply (102→19) but still 0
     products recorded. Ruled out.

  **Actual root cause**, found in the fetch log: EXTRACTION's
  `allowed_tools` never included `WebSearch` (`docs/handoff.md` §3's own
  original worked example — `["WebFetch", "mcp__scout__record_product"]`
  only), a deliberate v7 design choice assuming a candidate's manufacturer
  page could always be reached by a URL guessed from its bare name
  (`Cluster.exemplar_products` is deliberately unsourced, §8.1a). For a
  many-small-brand `rich` category that assumption fails: the model fell
  back to fetching retailer search-result pages and brand homepages, never
  a real per-product page, so it never had citable evidence to record.

  **Fix applied and verified live:** gave EXTRACTION `WebSearch`, capped at
  a new `config.MAX_EXTRACTION_SEARCHES_PER_PRODUCT=1` per candidate (soft,
  prompt-referenced, same pattern as the other per-phase caps — enough to
  resolve "what's the real URL," not a second broad research pass);
  `MAX_RUN_SEARCHES` raised 40→55 for headroom; `EXTRACTION_PROMPT_TEMPLATE`
  explains when to search and warns against ever treating a retailer
  search-results page as a source; `docs/handoff.md` §3 updated with a
  dated inline amendment note (not a version bump — this is a narrow,
  evidence-backed post-freeze correction, the same pattern
  `_repair_comparison_specs`/§4.3-on-`secondhand_risk_factors` already set,
  not a new external-review round). All 827 tests still pass. **Verified
  live: 10/10 candidates recorded** (was 0/10, repeatedly, before the fix)
  — $1.11, `fetches=42/120`, `searches=13/55`, clean single call/tick, no
  session-restart recurrence.

  Not yet re-verified against a second `rich`-shaped category — the
  espresso-machines golden-set capture (item 1 above) doubles as that
  second confirmation.

  Diagnostic method worth reusing if a similar live-SDK-behavior question
  comes up again: a throwaway probe script (not committed) wrapping each
  phase's bound `query` name to track cumulative `total_cost_usd` and print
  one line per `ResultMessage`; a method-level call counter around the
  `Sdk*` class method itself (not just the message stream) to distinguish
  "called once" from "one call, multiple billed segments"; a self-enforcing
  cost cap that raises from inside the wrapped generator instead of relying
  on a human to notice and kill the process; and JSON caching of
  intermediate phase outputs so a phase already paid for is never re-paid
  for. Total live spend across this investigation: ~$6.49.

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
- `src/product_scout/hooks/progress.py` — per-tool-call and per-phase
  `ResultMessage` progress ticks (§16.2); the EXTRACTION bug investigation
  above relied on this instrumentation.
- `src/product_scout/phases/extraction.py` — `EXTRACTION_PROMPT_TEMPLATE`
  and `SdkExtractor.extract()`'s `allowed_tools`, both changed by the
  2026-08-31 fix above.
- `src/product_scout/config.py` — `MAX_EXTRACTION_SEARCHES_PER_PRODUCT`
  (new) and `MAX_RUN_SEARCHES` (raised), same fix.
- `docs/handoff.md` §3 (EXTRACTION's tool list, amended post-v7) and
  §17.1/§17.2 — the authoritative spec for all of this.
- `CLAUDE.md` — checklist, the eleven invariants, commands.
