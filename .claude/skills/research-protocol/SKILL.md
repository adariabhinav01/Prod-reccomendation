---
name: research-protocol
description: Use during Discovery and Extraction (and any other Haiku research phase) — source tiering, extraction rules, conflict handling, and low-evidence mode rules for researching product specs, prices, and reviews.
---

# Research Protocol

This is the shared research contract for every Haiku phase (Discovery,
Extraction, Timing, Prior-Gen). It does not decide what to buy — that's
Phase 7's job on Opus. It decides what counts as evidence and how to cite
it.

## Source tiering

1. **Tier 1 — Manufacturer / first-party spec pages.** Authoritative for
   specs, useless for judgment.
2. **Tier 2 — Independent testing outlets** with a stated methodology (they
   say how many units they tested, or publish measurements).
3. **Tier 3 — Review aggregators and enthusiast sites.** Good for consensus
   signal, variable rigor.
4. **Tier 4 — Retailer listings.** Current price only, with timestamp.
   Never for specs.
5. **Tier 5 — Community sources** (forums, subreddits, video reviews, owner
   reviews). **Excluded by default; admissible only in low-evidence mode**,
   always labeled, never as the sole basis for a spec value.

**Standard mode requires:** specs from Tier 1 where obtainable, judgment
from ≥2 Tier 2/3 sources, price from Tier 4 with a timestamp. Reject
anything without a disclosed testing methodology as the *sole* source for a
performance claim.

**Low-evidence mode relaxes to:** any tier admissible, every claim labeled
with its tier, `high` confidence unreachable, and every relaxation logged to
`caveats`.

## Extraction rules — no source, no field

Every spec value you record must carry a real, citable `http(s)` URL. If
you cannot find one, **omit the field entirely** — never invent a URL, and
never pass a placeholder like `"N/A"`, `"unknown"`, or an empty string. This
is enforced in code, not just here: `record_product` rejects the entire
call and tells you exactly which field(s) to drop. Drop them and call
`record_product` again — don't give up on the whole product over one
missing spec.

A price needs a real Tier 4 source and an observed timestamp — prices move,
and a stale or guessed price is worse than a missing one.

A fetch or search failure is routine, not exceptional. Report what you
couldn't reach and move on to the next source; don't retry the same URL and
don't route around a failure through another fetch method.

## Conflict handling

When two sources disagree on the same spec, do not silently pick one. Keep
the value you trust most as `SourcedValue.value`, and record every other
observed value in `conflicting_values` — the report surfaces the
disagreement to the user rather than presenting a single number with false
certainty. When a second source independently confirms a value, add its URL
to `corroborated_by` instead.

## Low-evidence mode

When told a run is in low-evidence mode:

- Tier 5 sources become admissible, but must be labeled as such and never
  used as the sole basis for a spec value — only for judgment/consensus.
- Manufacturer performance claims (normally Tier 1 = specs only, not
  judgment) become admissible for judgment too, but must be marked
  `unverified manufacturer claim` everywhere they appear.
- You are not being asked to lower your standards quietly — every relaxation
  you make must be something the report can name explicitly.

Confidence itself is never something you assign — it's computed
deterministically from the evidence you report (review count, tier,
corroboration, conflicts). Your job is to report the facts (how many
independent sources, whether any discloses a methodology) accurately, not to
characterize how confident the result should be.
