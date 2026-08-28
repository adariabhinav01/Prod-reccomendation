---
name: question-design
description: Use during Phase 2 REFINE — construction, axis types, checks, and the stopping condition for the secondary interview questions asked after SURVEY, grounded in the run's clusters and dimensions.
---

# Question Design

You are authoring the interview REFINE runs, grounded in the `SurveyReport`
you're given — its `clusters` and `dimensions` — plus the buyer's Phase 0
answers. You do not decide what to buy or run the interview yourself; you
produce the question content, and the orchestrating code decides which of
your questions actually get asked, in what order, and when to stop.

For every `Dimension` you're given, produce one topic: a gate question, an
optional axis, and a free-text prompt. Ground everything in the actual
`Dimension.splits` values and `Cluster.label`s you were handed — never
invent a distinction that isn't there.

## Two-stage construction

```
STAGE 1 — GATE  (whenever the topic could be a hard constraint for anyone)
  must_have / must_avoid / persuadable / no_preference
    → must_have | must_avoid  →  becomes a FILTER, topic ends
    → persuadable | no_preference  →  proceed to stage 2

STAGE 2 — PREFERENCE
  [optional axis: position OR importance]  +  [free text: ALWAYS present]
  Both independently skippable.
```

Write `gate_description` so it says, concretely, what "must-have" and
"must-avoid" mean for *this* dimension — not an abstract gesture. If a
dimension's splits are `{"mid-tier": "dual", "budget": "single"}`, a gate
about motor count should say something like "a must-have excludes
single-motor desks; a must-avoid excludes dual-motor ones," naming the real
values, not "must-have excludes desks without the feature you want."

You must also report, alongside the gate copy, exactly which of the
dimension's own `splits` values its "must-have" sense keeps — grounded in
the literal strings you were given for that dimension, never a paraphrase
or an invented label. This is what makes the downstream elimination logic
computable without asking you again.

## Two axis types

| | Position axis | Importance axis |
|---|---|---|
| Example | power ←→ control | how much noise matters |
| Both ends legitimate? | Yes | No — low end means "don't care" |
| Middle means | genuinely balanced | moderate weight |
| Downstream | shift toward a pole | scale that dimension's weight |

`Dimension.axis_kind` is already decided — set during SURVEY, not
inferred here. Use it as given. If it's `None`, the dimension has no
coherent axis; omit the axis entirely rather than manufacturing one, and
let the gate plus free text carry the topic.

## Free text is always present

Not an optional elaboration field — a permanent half of the preference
stage, present on every topic regardless of whether an axis exists.
Conditional and lexicographic preferences ("performance first, aesthetics
as tiebreaker within 10%") cannot be held by a scalar; free text is where
they live. Keep the axis anyway when one exists — it's far lower-effort,
and someone will tap a number who would bounce off an open prompt.

## Construction checks

Apply these before finalizing a topic:

- **Dead-question filter.** If no reasonable person would give a low
  answer ("how much do you value reliability?"), the question carries zero
  information. Reformulate as a circumstance question instead ("how long
  do you expect to keep this?").
- **Magnitude × direction decomposition.** Where "how much" and "which
  way" vary independently, one question can't hold both — aesthetics needs
  an importance axis *and* a direction question.
- **Compound-tradeoff check.** Verify one coherent underlying quantity
  exists before pairing two things on one axis ("portability vs. capacity"
  fails — decompose or drop to free text).
- **Hidden-threshold check.** A continuous-seeming topic with a real
  cliff (e.g. a legality or compatibility threshold) belongs in its own
  gate, not blended into a smooth axis.

**Not in scope for this build:** the "floor-then-indifference → multiple
choice" pattern (presenting a threshold topic as a standalone multiple-choice
question instead of the gate+axis+text composite) isn't wired yet — every
topic you produce should use the composite shape described above, even for
topics that would ideally be a concrete multiple choice.

## Skip defaults — informational only

You don't need to reason about these; the interaction layer already
enforces them. A skipped gate defaults to `persuadable`; a skipped position
axis defaults to `0.5` (genuinely balanced); a skipped importance axis
defaults to `0.2` (weighted lightly, never invented as neutral). Every
default is logged for the user to see.

## Scope

Not limited to product specs. Any input that would change the
recommendation qualifies — financing structure, ownership duration,
anything in the survey's dimension landscape.

## Cluster and price language

`Cluster.exemplar_products` and `price_range_native` are unsourced —
collected from search snippets before any product has actually been
researched. If your topic copy references clusters or prices at all, use
cluster-level language with indicative prices ("around $200," never
"$199") and never a named product with an exact figure — named products
with exact prices don't exist yet at this phase.
