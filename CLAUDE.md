# Product Scout

Python application on the Claude Agent SDK that produces balanced, research-backed
product recommendations for any product category.

## The spec

**`docs/handoff.md` is the authoritative build spec. Read it before doing any work.**
It is **version 7** and supersedes all earlier versions — several v4, v5, and v6
decisions were found to be wrong and are corrected there. If anything in this file conflicts
with it, the spec wins. Don't re-derive design decisions already settled in §0.

## Phase sequence

Fixed. Decided at design time, encoded in `orchestrator.py` as one `query()` per
phase. Nothing dispatches subagents.

```
0  INTAKE      Python ── fixed questions, no model calls
1  SURVEY      Haiku  ── coverage, differentiation, pricing complexity, secondhand
                         risk, region scope, clusters, dimensions
2  REFINE      Opus   ── secondary questions grounded in the survey
3  EXTRACTION  Haiku  ── deep pass, representatives chosen post-requirements
4  TIMING      Haiku
5  PRIOR-GEN   Haiku
6a SCORING     Opus   ── scores, counterfactuals, flip points
6b SYNTHESIS   Opus   ── verdict, write-up
7  RENDER      Python ── HTML + JSON, no model calls
```

REFINE runs **before** EXTRACTION deliberately — see spec §1.

## Status

Build order is §17 of the spec — that is the source of truth for what each step
contains. Keep only checkboxes here.

- [x] 1. Data model, store, checkpointing, `confidence.py` tests
- [x] 2. `QuestionPort` + CLI port
- [x] 3. Settings + `scout config`
- [x] 4. Phase 0 intake
- [x] 5. Phase 1 SURVEY + clusters/dimensions + broadening interrupt
- [x] 6. Renderer against fixture data
- [x] 7. Phase 2 REFINE
- [x] 8. Phase 3 EXTRACTION + ledger validation
- [x] 9. Phases 6a/6b + constraint enforcement
- [x] 10. Phases 4–5 timing + prior-gen
- [ ] 11. Location end to end
- [ ] 12. Low-evidence + commodity modes
- [ ] 13. Hooks, cost caps, truncation
- [ ] 14. `rescore` + `history`
- [ ] 15. Golden set (§17.1) — gates skill edits from here on

## Invariants

Eleven rules that silently break the design if violated. Check against these before
finishing any step.

1. **Model routing.** Gathering and extraction run on Haiku (phases 1, 3, 4, 5).
   Judgment runs on Opus (phases 2, 6a, 6b). Extraction must never drift onto Opus —
   model routing is the biggest cost lever in the design. (Note: the §5.1 row cap is
   *not* a cost control; legibility is the binding constraint there and it happens to
   be tighter than cost would be.)
2. **Fixed phase sequence.** The order above is decided in Python. **No model may
   skip or reorder a phase** — nothing dispatches subagents, so this is structural,
   not aspirational. Orchestrator-level termination on resource limits is permitted
   and defined in §13.1. Discretion *within* a phase is fine.
3. **No source, no field — and the source must be real.** Every `SourcedValue`
   requires a `source_url` the run's ledger has seen (§4.3). Spec values require a
   `fetched` entry; judgment-bearing SURVEY values may cite `seen_not_fetched`.
   Presence alone was never a hallucination guard; a model can invent a URL.
4. **Score and confidence are different things.** `score` is a model judgment and
   must never be reduced to a formula. `confidence` is computed in Python — **and so
   is every other field of `EvidenceProfile`** (§4.0d). A model that can't set
   `confidence` but can set its inputs isn't out of the loop. Never resolve
   uncertainty by lowering a score. The same principle governs
   `ships_from_confidence` (§10.6).
5. **The evidence ratios have fixed, different denominators** (§4.0a/§4.0b).
   Corroboration is over `SurveyReport.comparison_specs` — never
   `len(product.specs)`, which would make extraction verbosity the dominant input
   to confidence. Conflict is over the *extracted* subset, so a thinly-researched
   product can't cap its own penalty.
6. **Every product needs at least one con.** An empty `cons` list is a validation
   failure. This app is not a sales tool.
7. **Verdict is computed independently of ranking**, and the top 2–3 picks are shown
   even when the verdict is "don't buy."
8. **Axis answers are soft weights, never filters.** Only gate answers
   (`must_have` / `must_avoid`) filter. An axis lean must never remove the opposite
   pole from the table — that is the exact failure axes exist to prevent.
9. **All user interaction routes through `QuestionPort`.** No phase calls terminal
   input directly. This is the only thing that makes the later web port expensive.
10. **Low-evidence honesty.** Three checkable guards: the banner renders whenever
    `low_evidence_mode` is true, `INSUFFICIENT_EVIDENCE` is reachable in the verdict
    enum, and the `0.75` clamp is exercised by a unit test against a synthetic profile
    that would otherwise exceed it. (The clamp rarely binds in practice — a typical
    low-evidence profile computes to ~`0.43` — so test it, don't assume it fires.)
11. **No conversational carryover between phases.** Each phase is a fresh `query()`.
    Anything a later phase needs must be in the run record. This is what makes
    `rescore` work, and it is the invariant most likely to be violated by someone
    finding it convenient to thread context along.

## Commands

```
scout research [--location XX] [--resume <run_id>]
scout rescore <run_id> --set "Name=199"
scout history [--category <type>]
scout eval [--stable-only]           # golden set; §17.1
scout config set location.country DE
```

## Notes

- Do not run `/init` — it will overwrite this file.
- Pin fully-qualified model IDs in `config.py`; never ship bare `"haiku"`/`"opus"`
  aliases into stored run records.
- Assert at startup that `.claude/skills/` actually loaded. Running without
  `recommendation-logic` produces plausible-looking garbage rather than an error.
