"""Pinned model IDs and soft cost-cap constants (build order step 5; `MODEL_OPUS`
and `QUESTION_DESIGN_SKILL` added step 7).

CLAUDE.md invariant 1: "Pin fully-qualified model IDs in `config.py`; never
ship bare `\"haiku\"`/`\"opus\"` aliases into stored run records." This file
is that single source of truth for phases 1-5 (Haiku) and now Phase 2
REFINE (Opus, build order step 7 — `phases/refine.py`). `MODEL_OPUS`'s value
matches the literal string already used by `tests/conftest.py`'s
`make_run_record()` `model_ids` fixture.

`MAX_DISCOVERY_SEARCHES` / `MAX_EXTRACTION_FETCHES_PER_PRODUCT` are soft caps
— referenced in the discovery/extraction prompts as a self-limit, not yet
mechanically enforced. Hard enforcement via a `PreToolUse` hook is build
order step 10 (§10); these constants exist now so that step wires a hook
against an already-named budget instead of inventing the number then. §8.1a:
"a full research run on a category with nothing to find is the most
expensive way to learn the category has nothing to find" — cost-consciousness
should be on the books from the first real SDK call, even while unenforced.

No I/O lives here. `ANTHROPIC_API_KEY` handling belongs at a future `cli.py`
entrypoint (via `python-dotenv`, already a dependency) — this module never
reads or stores the key itself.
"""

from __future__ import annotations

MODEL_HAIKU: str = "claude-haiku-4-5-20251001"  # phases 1-5
MODEL_OPUS: str = "claude-opus-4-5-20260101"  # phases 2, 6a, 6b

MAX_DISCOVERY_SEARCHES: int = 8
MAX_EXTRACTION_FETCHES_PER_PRODUCT: int = 6

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
