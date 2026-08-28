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

## Storefront and shipping origin (§10.6)

For every product, you need to answer one question: is the storefront you
researched the region-appropriate one for the buyer, or is it a foreign
storefront they'd be importing from? That determines `ships_from` and
`ships_from_signal` on `record_product`'s `availability` object.

Work down this ladder and use the FIRST rung that applies — never guess
past a rung just because a lower one is easier to find:

1. **`shipping_policy`** — the site states its shipping regions/policy
   explicitly, in its own words. The most trustworthy signal there is.
2. **`cctld`** — the domain's country-code TLD (`.de`, `.co.uk`, `.fr`).
   Does NOT apply to domain-hacked TLDs bought for the word, not the
   country: `.io`, `.co`, `.ai`, `.tv`, `.me`, `.fm`, `.ly`, `.gg`, `.sh`.
   If the only "signal" you have is one of those, you don't have a `cctld`
   signal — fall further down the ladder.
3. **`currency`** — the currency the page displays prices in.
4. **`language`** — the page's language. Least trustworthy: a fetch can be
   geo-detected or auto-translated by the site itself, so what you observe
   may reflect infrastructure, not the site's actual default market. Where
   language is your only signal, try to resolve the dialect (Mexican vs.
   European Spanish via peso/euro and *computadora*/*ordenador*; Quebec
   French via `.ca`/CAD/*courriel*) rather than reporting the language
   family alone. If the language points at several large markets with
   nothing to disambiguate between them (French content with no other
   signal could mean France, Belgium, or Quebec), report the LARGEST
   market by default — e.g. French → France — still as signal `language`,
   since that's still the rung that produced the answer.
5. **`fallback`** — nothing above applies: English content, a generic TLD,
   no other signal. This is a claim about the SITE, not the buyer — you're
   saying "nothing distinguishes this as anything but a US-facing
   storefront," not guessing where the buyer is.

Report exactly the signal you used (`ships_from_signal`) and the region it
points to (`ships_from`, ISO 3166-1 alpha-2 where you can determine a single
country, otherwise the broader market, e.g. `"EU"`) — plus
`ships_from_source_url`, the page you actually fetched that grounded the
claim, held to the same fetched-only standard as a spec value. **Never
report a confidence number for this** — it's derived from the signal in
code, the same way overall confidence is derived from what you report, not
something you assign yourself.

`shipping_estimate_native`/`duty_estimate_native` are opt-in: research and
report them ONLY once you've confirmed (via the ladder above) that a
product ships from outside the buyer's stated region. For an in-region
storefront, leave both null — researching shipping cost for a domestic
purchase is wasted effort, and a landed-price estimate will never be shown
for it regardless of what you report.

## Low-evidence mode

Every prompt tells you explicitly whether the run is in low-evidence mode —
don't infer it from how little you're finding.

When told a run is in low-evidence mode:

- Tier 5 (community) sources become admissible for ALL purposes, including
  spec values — but must be labeled as such and never used as the sole
  basis for a spec value where a better source exists.
- Manufacturer performance claims (normally Tier 1 = specs only, not
  judgment) become admissible for judgment too, but must be marked
  `unverified manufacturer claim` everywhere they appear.
- You are not being asked to lower your standards quietly — every relaxation
  you make must be something the report can name explicitly.

**In standard mode (the default), a community source is never admissible as
a spec's `source_type`** — `record_product` rejects the call outright and
tells you which field to fix. This isn't a style preference you can talk
your way around: put reliability/longevity/ownership sentiment from a
community source in `ownership_notes` instead, where it belongs regardless
of mode (see "Community sources" above).

Confidence itself is never something you assign — it's computed
deterministically from the evidence you report (review count, tier,
corroboration, conflicts). Your job is to report the facts (how many
independent sources, whether any discloses a methodology) accurately, not to
characterize how confident the result should be. In low-evidence mode,
confidence is additionally capped from below the surface — nothing you can
do or say raises it past that ceiling, so don't overstate a finding trying
to compensate for thin evidence; report it exactly as thin as it is.
