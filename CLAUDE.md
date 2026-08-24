# Product Scout

Python application on the Claude Agent SDK that produces balanced, research-backed
product recommendations for any product category.

## The spec

**`docs/handoff.md` is the authoritative build spec. Read it before doing any work.**
If anything in this file conflicts with it, the spec wins. Don't re-derive design
decisions that are already settled there — §0 lists the locked ones.

## Status

Build order is §14 of the spec.

- [x] 1. Data model + run store + index + `compute_confidence()` unit tests
- [x] 2. `QuestionPort` + CLI port, incl. two-attempt escape-hatch swap
- [x] 3. Phase 0 intake
- [x] 4. Phase 1 probe + coverage gate
- [x] 5. Discovery + extraction (Haiku)
- [ ] 6. Renderer against fixture data
- [ ] 7. Phase 7 analysis (Opus) + constraint enforcement
- [ ] 8. Timing + prior-gen passes
- [ ] 9. Low-evidence mode end to end
- [ ] 10. Hooks + cost caps
- [ ] 11. `rescore` + `history`

Update this list as steps complete.

## Invariants

Five rules that silently break the design if violated. Check against these before
finishing any step.

1. **Model routing.** Phases 1–5 run on Haiku, phases 6–7 on Opus. Extraction must
   never drift onto Opus — it is the single biggest cost lever in the design.
2. **No source, no field.** Every `SourcedValue` requires a real `source_url`. An
   extractor that can't cite one omits the field rather than inventing it. Enforced
   in validation, not just in prompts.
3. **Score and confidence are different things.** `score` is a model judgment and
   must never be reduced to a formula. `confidence` is computed in Python from
   evidence counts and is never model-assigned. Do not conflate them, and never
   resolve uncertainty by lowering a score.
4. **Every product needs at least one con.** An empty `cons` list is a validation
   failure. This app is not a sales tool.
5. **Verdict is computed independently of ranking**, and the top 2–3 picks are shown
   even when the verdict is "don't buy."

## Commands

```
scout research            # full interactive run
scout rescore <run_id>    # re-score stored run against new prices, no re-research
scout history             # indexed past runs
```

## Notes

- Do not run `/init` — it will overwrite this file.
- Pin fully-qualified model IDs in `config.py`; never ship bare `"haiku"`/`"opus"`
  aliases into stored run records.
