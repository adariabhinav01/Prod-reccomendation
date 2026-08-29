"""Pinned model IDs and soft cost-cap constants (build order step 5; `MODEL_OPUS`
and `QUESTION_DESIGN_SKILL` added step 7).

CLAUDE.md invariant 1: "Pin fully-qualified model IDs in `config.py`; never
ship bare `\"haiku\"`/`\"opus\"` aliases into stored run records." This file
is that single source of truth for phases 1-5 (Haiku) and now Phase 2
REFINE (Opus, build order step 7 — `phases/refine.py`). `MODEL_OPUS`'s value
matches the literal string already used by `tests/conftest.py`'s
`make_run_record()` `model_ids` fixture.

`MAX_DISCOVERY_SEARCHES` / `MAX_EXTRACTION_FETCHES_PER_PRODUCT` (and
`MAX_SURVEY_SEARCHES`/`MAX_TIMING_SEARCHES`/`MAX_PRIOR_GEN_SEARCHES` below)
are soft, PER-PHASE caps — referenced in each phase's own prompt as a
self-limit, never mechanically enforced (`MAX_DISCOVERY_SEARCHES` is dead
weight: `discovery.py` itself is an unused, superseded prototype —
`survey.py` absorbed its job). `MAX_RUN_FETCHES`/`MAX_RUN_SEARCHES` below
are the different thing §13 actually hard-enforces: one GLOBAL total across
the whole run, via a `PreToolUse` hook reading a counter `orchestrator.py`
(build order step 13) owns — see that constant's own comment and
`hooks/budget.py` for why a per-phase soft limit and a run-wide hard cap are
deliberately two different mechanisms, not one. §8.1a: "a full research run
on a category with nothing to find is the most expensive way to learn the
category has nothing to find" — cost-consciousness should be on the books
from the first real SDK call, even while the per-phase numbers stay
unenforced.

No I/O lives here. `ANTHROPIC_API_KEY` handling belongs at a future `cli.py`
entrypoint (via `python-dotenv`, already a dependency) — this module never
reads or stores the key itself.
"""

from __future__ import annotations

MODEL_HAIKU: str = "claude-haiku-4-5-20251001"  # phases 1-5
MODEL_OPUS: str = "claude-opus-5"  # phases 2, 6a, 6b — current-gen Opus 5 ships no
# date suffix (unlike Haiku 4.5 above, still on a dated snapshot); the previous
# value ("claude-opus-4-5-20260101") was stale and rejected outright by the API
# ("There's an issue with the selected model... It may not exist or you may not
# have access to it") — caught live during build order step 15's golden-set
# capture, the first time this constant was ever exercised against a real key.

MAX_DISCOVERY_SEARCHES: int = 8

# Was 6 — cut to 4 (build order step 15's cost-optimization pass) after
# the `claude-api` skill's methodology flagged this as the single largest
# component of the whole run's fetch budget: with EXTRACTION's candidate
# list now capped at ROW_CAP=12 (see orchestrator.py's
# `_extraction_candidates`), this one constant alone used to drive
# MAX_EXTRACTION_FETCHES_PER_PRODUCT(6) x 12 = 72 of MAX_RUN_FETCHES(120)'s
# 120 fetches — roughly 60% of the entire run's budget. Fewer sources per
# product is a small, honestly-absorbed quality cost, not a silent one:
# it feeds directly into confidence.py's §4.0a/§4.0b corroboration ratio,
# which turns thinner evidence into a lower confidence band rather than a
# wrong spec (CLAUDE.md invariants 4/5) — unlike cutting Opus judgment on
# SCORING/SYNTHESIS, which would be a real quality loss, this only makes
# the app say "less confident" more often when corroboration is thinner.
MAX_EXTRACTION_FETCHES_PER_PRODUCT: int = 4

# SURVEY (build order step 5) does more per call than Discovery's shortlist
# search — coverage assessment, clustering, dimension-building, and
# secondhand-risk research all happen in the same pass (§8.1) — so it gets a
# larger soft budget. No spec-given number exists for this; chosen the same
# way MAX_DISCOVERY_SEARCHES was (a self-limit referenced in the prompt, not
# yet mechanically enforced — see this module's docstring).
MAX_SURVEY_SEARCHES: int = 12

# §11 — read by every Haiku research phase (Discovery, Extraction, and
# later Timing/Prior-Gen). Single source of truth for the skill's directory
# name, shared by product_scout.skills.assert_skill_loaded() and every real
# SDK adapter's ClaudeAgentOptions(skills=[...]).
RESEARCH_PROTOCOL_SKILL: str = "research-protocol"

# §14 — read by Phase 2 REFINE (build order step 7). Same
# assert_skill_loaded()/ClaudeAgentOptions(skills=[...]) contract as
# RESEARCH_PROTOCOL_SKILL above.
QUESTION_DESIGN_SKILL: str = "question-design"

# §6 — read by Phases 6a SCORING and 6b SYNTHESIS (build order step 9). Same
# assert_skill_loaded()/ClaudeAgentOptions(skills=[...]) contract as the two
# skills above. CLAUDE.md invariant: "Running without recommendation-logic
# produces plausible-looking garbage rather than an error" — this is the
# skill that guards against exactly that on the two judgment phases.
RECOMMENDATION_LOGIC_SKILL: str = "recommendation-logic"

# §6.5 — read by Phase 4 TIMING (build order step 10), per §14's skill
# table. Loaded ALONGSIDE RESEARCH_PROTOCOL_SKILL, not instead of it —
# TIMING is still a Haiku research phase (WebSearch/WebFetch), so the
# general source-tiering/conflict rules apply too; this skill adds only
# §6.5's timing-specific content on top.
MARKET_TIMING_SKILL: str = "market-timing"

# §8.1/§12.3 — Phase 5 PRIOR-GEN (build order step 10) does the same kind
# of research EXTRACTION does (find a product, fetch its manufacturer page
# and reviews), just aimed at a predecessor rather than a named candidate,
# so it gets a comparable soft budget to MAX_SURVEY_SEARCHES rather than
# MAX_EXTRACTION_FETCHES_PER_PRODUCT's narrower per-product fetch count —
# it has to search for the predecessor first, which extraction never does.
MAX_TIMING_SEARCHES: int = 8
MAX_PRIOR_GEN_SEARCHES: int = 10

# §13's global cost cap — "Hard-stop at N fetches / M searches" — with no
# spec-given number, unlike the per-phase soft caps above. Sized generously
# above what a full run can realistically spend on each side, since this is
# meant to rarely bind (same spirit as the low-evidence `0.75` clamp and
# `degraded_modes.COMMODITY_CATALOG_FLOOR`): EXTRACTION alone can reach
# roughly MAX_EXTRACTION_FETCHES_PER_PRODUCT(4) x up to 12 candidates
# (§5.1's row-cap ceiling, now mechanically enforced by
# `orchestrator.py`'s `_extraction_candidates`) = ~48 fetches;
# MAX_RUN_FETCHES leaves generous headroom above that plus SURVEY/TIMING/
# PRIOR_GEN's own incidental fetches.
# MAX_SURVEY_SEARCHES(12) + MAX_TIMING_SEARCHES(8) + MAX_PRIOR_GEN_SEARCHES(10)
# = 30; MAX_RUN_SEARCHES leaves headroom above that. Enforced by
# `hooks/budget.py`'s `RunBudget`, wired by `orchestrator.py` (build order
# step 13) — a tuning candidate for §17.1's golden set, same as
# COMMODITY_CATALOG_FLOOR.
MAX_RUN_FETCHES: int = 120
MAX_RUN_SEARCHES: int = 40
