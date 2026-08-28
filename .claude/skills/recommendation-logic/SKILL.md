---
name: recommendation-logic
description: Use during Phases 6a SCORING and 6b SYNTHESIS — how to score holistically, judge counterfactuals, assign roles and archetypes, and independently compute the verdict and write-up, against the run's already-researched products.
---

# Recommendation Logic

You are judging an already-researched set of products for a buyer shopping
in this category. Nothing here asks you to research anything further —
every spec, price, pro, con, and evidence signal you need is already in
front of you. Your job is judgment: what fits this buyer, how confident
that fit is, and what to tell them.

## Score is judgment, not a formula

`score` (0–10, holistic) is the one number in this whole pipeline that is
never computed. `confidence` is computed elsewhere, in Python, from the
evidence you were given — how many sources, whether they agree, whether
anyone disclosed a testing methodology. **Never fold confidence into
score.** A product with one glowing, uncorroborated review can legitimately
score 8.0 at very-low confidence — that combination is informative on its
own, and collapsing it to a mediocre 6.0 "to be safe" destroys the
information the reader needs. Confidence is rendered separately, alongside
your score, not baked into it.

The scale:

| Band | Meaning |
|---|---|
| 9.0–10.0 | Best fit. Buy this. No material reservation. |
| 7.5–8.9 | Strong. Recommend with one named tradeoff. |
| 6.0–7.4 | Solid, defensible. Real compromises. |
| 4.5–5.9 | Situational. Only if one preference dominates. |
| 3.0–4.4 | Weak. Better options at similar price. |
| 0.0–2.9 | Do not recommend. |

Near-ties are expected and fine — when two products land within 0.2 of each
other, say so and say why, rather than manufacturing separation. Nobody
will force your scores apart.

**Use the decimal place meaningfully.** A `.0` or `.5` score is legitimate
sometimes, but if most of your scores land on one of those two values,
you're bucketing into a handful of tiers instead of actually discriminating
between products — go back and ask what specifically separates a 7.5 from
a 7.8, or a 6.0 from a 6.3.

## Counterfactual scores are the same judgment at a different price

For any product asked, re-judge holistically as if its price were 10%,
20%, and 30% lower — same product, same specs, same tradeoffs, only the
price changes. This is not arithmetic and not a guess at "how much better
does cheaper feel" — actually re-run your judgment. A product whose specs
are the weak point won't gain much from a cheaper price; a product that's
only mediocre *because* it's overpriced for what it offers will gain a
lot. The difference between those two cases is exactly the signal a flip
point is meant to capture. Whether these counterfactual scores end up
mattering (they only do for the small set of well-evidenced products a
price comparison can actually be priced against) is not your concern —
report your honest judgment at each price point regardless.

## Role and archetype

You may propose a `role` for each product from: `recommendation`,
`baseline_current`, `reference_above_budget`, `reference_unavailable`,
`reference_displaced`. Most products keep `recommendation`. Use a
`reference_*` role only when a product genuinely doesn't belong among the
shown recommendations but is still worth keeping visible for context —
never as a way to quietly get rid of a product you don't want to think
about. An over-budget product that's still worth recommending anyway
(because it's simply the best available and the reasoning says so) stays
`recommendation`, `in_budget=False` and all — that combination is exactly
what an "above-budget standout" looks like, and burying it under
`reference_above_budget` would remove it from the table for no honest
reason. Reserve `reference_above_budget` for products so far outside the
budget that recommending them outright would be perverse.

**Archetype** (`strength_archetype`) is the narrative reason to prefer a
product, not the cluster it belongs to — those are different jobs (a
cluster is "what counts as a distinct option"; an archetype is "why you'd
pick this one over another distinct option"). Base vocabulary — `value`,
`performance`, `aesthetic`, `durability`, `ergonomics`, `features`,
`support` — is available to any category and is never mandatory. You may
propose a category-specific archetype (accessibility, longevity, privacy,
integration, learning-curve, or something else entirely) when a product's
actual pros and cons justify it — the same sourced justification a pro or
con requires, not a label invented to hit a diversity target. Across the
whole shown set, keep the total distinct archetypes to 6–8 (default
around 7) and make sure genuinely different products get genuinely
different labels — three products all tagged `value` when one is cheap
and rough, one is cheap and excellent, and one is merely not-expensive is
under-differentiating, not being consistent.

If you're told your archetype assignments don't yet give at least three
in-budget products distinct archetypes, or that no above-budget product
was kept as a real recommendation despite one existing, that's a
correction to make, not a target to fight — look again at what actually
separates the products in question and say so honestly; don't invent a
distinction that isn't there if one truly doesn't exist (a low-
differentiation, commodity-like set is a real finding, not a failure).

**When the prompt tells you `COMMODITY CATEGORY: True`, or `LOW-EVIDENCE
MODE: True`**, you won't be re-prompted for archetype diversity at all
either way — a commodity run has been flagged as low-differentiation
against a large catalog (§8.4); a low-evidence run's thin evidence
similarly doesn't support manufacturing narrative distinctions (§8.3's
"table constraints scale down"). Either relaxes the requirement
structurally, not something you need to argue your way out of. Say so
plainly wherever it's relevant — in `strength_archetype` choices and,
especially, in the write-up — rather than manufacturing distinctions to
look thorough. The two can apply together or separately; when relevant,
name whichever actually applies rather than assuming they're the same
thing.

## Verdict is independent of ranking

Decide `Verdict.action` on its own terms, separately from which product
scores highest. **Always assume the top 2–3 picks will still be shown to
the buyer, even when your verdict is "don't buy."** An engine that answers
"buy nothing" and stops there is useless — the buyer still wants to know
what the best of a bad set looks like, or when a good set exists but isn't
worth acting on yet.

Five possible actions:

- **`CONSIDER_CHEAPER_CATEGORY`** — an adjacent, cheaper category would
  satisfy every requirement the buyer actually stated. Worth checking
  early; it can reframe the whole answer.
- **`KEEP_CURRENT`** — the buyer is upgrading, and the delta over their
  current product doesn't justify paying the *entire* price of the new
  one (not just the marginal improvement). **Do not over-apply this.** If
  the buyer is jumping several tiers, or has explicitly said they want the
  top end and accepted the cost, that's a legitimate purchase — name the
  value math once if it's relevant, then respect the stated preference.
  Reflexive frugality nagging is a known failure mode here; a buyer who
  said "I want the best, budget isn't the constraint" should not be
  argued out of it.
- **`WAIT`** — a genuinely imminent successor, a category-wide price
  movement, or a landing technology transition. Every timing claim you
  make must carry its basis ("historically refreshed each September,"
  "manufacturer announced a Q4 launch") — never state one as bare fact,
  and if nothing credible turned up, say "no timing signal found" rather
  than manufacturing one. This includes software-specific signals:
  announced price increases, acquisitions (often a leading indicator of
  degradation or shutdown), sunset notices — not just release cadence.
- **`INSUFFICIENT_EVIDENCE`** — even with low-evidence mode's relaxed
  bar, there isn't enough here to support a call. Say so plainly, and name
  what would resolve it: a specific community, a retailer with a return
  window, a spec the buyer could go measure themselves. This is a more
  honest answer than a confident-looking recommendation built on three
  forum posts.
- **`BUY`** — none of the above applies.

## How the buyer's answers should move your scoring

| Input | Effect |
|---|---|
| A gate answered `must_have` / `must_avoid` | Already a hard filter — applied before you ever see the excluded products. Nothing left to do here. |
| A gate answered `persuadable` / `no_preference` | No filtering happened. `no_preference` means down-weight that dimension. |
| A position-axis answer | Shifts your scoring **toward** the stated pole. **Never** treat it as a reason to exclude the opposite pole — a buyer at 0.7-toward-control should still see the strongest power-leaning option, argued honestly, not quietly dropped. |
| An importance-axis answer | Scales how much that dimension should move your score, not which direction. |
| Free text | Interpreted directly — it may encode conditionals or tie-break rules ("performance first, aesthetics as a tiebreak within 10%"). Honor it literally. |

Axis answers are soft weights, full stop. Turning a lean into a filter is
the exact failure the axis system exists to prevent — resist the pull to
"clean up" the table by dropping the weaker-leaning pole.

## The write-up

For the top 2–3 picks (by score, among real recommendation candidates —
never the reference rows), write two-to-three sentences of real prose per
product: prose, not bullets, because this is where the tradeoff actually
gets argued. If a top pick sits at low or very-low confidence, say so in
the words themselves — don't leave it for a reader to notice on a chart.
Where the buyer's own budget note carried a conditional ("under $220 for
something good, under $150 for adequate"), resolve it explicitly here:
*"the $195 option is only marginally better than the $140 one — at that
gap I'd take the cheaper."* This prose is the one place in the whole
report where a tradeoff gets argued in full sentences rather than tabulated
— use it that way.
