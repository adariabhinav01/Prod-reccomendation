---
name: market-timing
description: Use during Phase 4 TIMING — how to research and report release cadence, price trends, and technology transitions without turning speculation into bare fact.
---

# Market Timing

You are researching whether *now* is a good moment to buy in this
category, or whether waiting would materially change the picture — an
imminent successor, a category-wide price movement, or a landing
technology transition. You are not deciding whether to recommend
waiting; that's a later judgment call made against your findings. Your
job is to report what's actually out there, honestly.

## Every claim carries its basis

This is the one rule everything else here serves. A timing claim is
useless without knowing why it should be believed — *"historically
refreshed each September"* and *"manufacturer announced a Q4 launch"* are
both fine; *"a new model is expected soon"* on its own is not, because
there's nothing behind it a reader could check.

**Never state a timing claim as bare fact.** Every entry in `basis_notes`
should let a skeptical reader see exactly what you're basing the claim on
— a release-history pattern you observed, an official announcement, a
credible rumor from a source you'd name, a filed patent, an executive
statement. If you can't point to something like that, you don't have a
timing claim yet, just a guess.

**If nothing credible turns up, say so.** `signal_found: false` and "no
timing signal found" is a complete, honest answer — a better one than
inventing a plausible-sounding refresh cycle to fill the field. Silence
here isn't a failure to find something; category refresh cycles that
don't exist, or aren't publicly telegraphed, are common and normal.

## Hardware and software signals are different shapes

For a physical product, look for: historical release cadence (does this
company refresh annually, biennially, irregularly?), an official
announcement or leak of a successor, a category-wide price trend (a
component getting cheaper, a tariff change, a seasonal sale pattern), or
a landing technology transition (a new standard, material, or platform
that's about to make the current generation look dated).

For software and services, the signals are different and easy to miss if
you only look for "release cadence":

- **Announced price increases** — a vendor raising prices soon is a
  reason to consider buying/subscribing now rather than later.
- **Acquisitions** — often a *leading indicator* of degradation or
  shutdown, not a neutral event. An acquired product's roadmap, support
  quality, and pricing frequently change for the worse within a year or
  two. Flag an acquisition you find, with your basis, even if nothing
  bad has visibly happened yet.
- **Sunset notices** — an explicit end-of-life or deprecation
  announcement is the strongest possible signal and should never be
  missed if it exists.

Don't default to a hardware mental model ("when's the next version
coming out?") for a subscription product — the more decision-relevant
question is often about the vendor's trajectory, not a release date.

## Report the trend, not a verdict

`price_trend` and `technology_transition` describe what you found, in
plain language, with their basis — not a recommendation. Whether a given
trend is worth waiting for is downstream judgment, made later, against
what you report here. Don't hedge your findings into vagueness to avoid
sounding like you're giving buying advice; report the concrete signal
you found and let the basis speak for itself.
