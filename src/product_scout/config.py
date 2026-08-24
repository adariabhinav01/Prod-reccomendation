"""Pinned model IDs and soft cost-cap constants (build order step 5).

CLAUDE.md invariant 1: "Pin fully-qualified model IDs in `config.py`; never
ship bare `\"haiku\"`/`\"opus\"` aliases into stored run records." This file
is that single source of truth for phases 1-5 (Haiku). `MODEL_OPUS` is
intentionally not added yet — phases 6-7 (analysis/scoring) aren't in scope
until build order step 7; add it there rather than guessing at an unused
constant now.

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

MAX_DISCOVERY_SEARCHES: int = 8
MAX_EXTRACTION_FETCHES_PER_PRODUCT: int = 6

# §11 — read by every Haiku research phase (Discovery, Extraction, and
# later Timing/Prior-Gen). Single source of truth for the skill's directory
# name, shared by product_scout.skills.assert_skill_loaded() and every real
# SDK adapter's ClaudeAgentOptions(skills=[...]).
RESEARCH_PROTOCOL_SKILL: str = "research-protocol"
