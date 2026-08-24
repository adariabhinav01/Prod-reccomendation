"""Phase 2 — DISCOVERY (spec docs/handoff.md §1/§3/§7, build order step 5).

Search + shortlist candidates, Haiku, `WebSearch`/`WebFetch` only — no
custom MCP tool (§3's `scout-researcher` `AgentDefinition`). This module
owns two things, mirroring `phases/probe.py`'s convention:

1. `Discoverer` — the seam through which the actual search happens.
2. `run_discovery` — pure orchestration on top of that seam: merge §7 item
   4's user-named candidates ahead of what's discovered, dedupe, cap the
   shortlist width.

`SdkDiscoverer` is the real, SDK-calling implementation of `Discoverer` —
unlike `probe.py`'s still-deferred `CoverageProber`, this one is built for
real (per build order step 5's charter: "discovery + extraction on Haiku").
It is not unit tested here (no `ANTHROPIC_API_KEY` in this suite, consistent
with how the rest of the repo tests everything else) — only `run_discovery`'s
merge/dedup/cap logic (against a fake) and `_parse_candidate_list` (a pure
function, plain string fixtures) are.

### SPEC GAP-FILL — shortlist width is a fixed cap of 8

§0: "8 candidates discovered -> 6-8 in the final table (never fewer than 6
when 6 exist; see §8 for sparse categories)." Discovery's own job is only
the first half — find up to 8 real candidates, `MAX_DISCOVERY_CANDIDATES`
below. The "never fewer than 6" floor is about which of those make the
*final rendered table* (§5.1's row-selection logic, near-duplicate
narrowing) — that's Phase 7/8 work against a `CoverageReport` this module
never receives, not something `run_discovery` needs to guarantee itself.

### `research-protocol` skill dependency

`SdkDiscoverer` depends on `.claude/skills/research-protocol/SKILL.md`
(§11 — source tiering, extraction rules, low-evidence rules) being loaded
for the session; `assert_skill_loaded` (§3's "fail loudly" requirement)
checks this before any call is made. Discovery itself doesn't call
`record_product` or assign source tiers, but the skill is shared across all
Haiku research phases per §11's table ("research-protocol | Haiku phases"),
and a discovery session run without it would search without the same
source-quality standards Extraction is held to.

### SPEC GAP-FILL — discovery's output contract

§3 gives `scout-researcher` only `WebSearch`/`WebFetch` — no custom tool for
structured output, unlike extraction's `record_product`. `_parse_candidate_list`
is this module's answer: the prompt instructs the agent to end its final
message with nothing but a JSON array of candidate name strings, and the
parser is deliberately lenient (tolerates a markdown fence or surrounding
prose) and never raises — a garbled final message is a routine, handleable
outcome (same spirit as §15's "fetch failures are routine, not exceptional"),
not a crash. An unparseable result degrades to `[]`, i.e. "discovery found
nothing," which `run_discovery`/`run_extraction` already handle as a
legitimate empty shortlist.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

from product_scout import config
from product_scout.phases.intake import Intake
from product_scout.skills import assert_skill_loaded

MAX_DISCOVERY_CANDIDATES: int = 8  # §0 — see module SPEC GAP-FILL note

DISCOVERY_PROMPT_TEMPLATE = """You are scout-researcher. Search the web and \
shortlist candidate products in the category "{product_type}".

Aim for up to {max_candidates} real, currently-purchasable candidates — or \
fewer if the category doesn't support that many. Do not pad the list with \
near-duplicates or discontinued models just to hit a number. Budget \
yourself to roughly {max_searches} searches.

A fetch or search failure is routine, not exceptional — report what you \
couldn't reach and move on; don't retry the same query and don't route \
around a failure through another method.

When you are done, your FINAL message must be, and contain nothing except, \
a JSON array of candidate product name strings, e.g.:
["Acme Widget Pro", "Zenith Model 4", "Contoso Elite"]
No prose before or after it, no markdown code fence."""


@runtime_checkable
class Discoverer(Protocol):
    """Seam for Phase 2's actual candidate search."""

    async def discover(self, product_type: str) -> list[str]: ...


_UNPARSED = object()  # sentinel distinguishing "failed to parse" from a
# successfully-parsed `None`/falsy JSON value


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_candidate_list(text: str) -> list[str]:
    """Extract a JSON array of candidate names from the model's final
    message. Tries the whole message as JSON first — so a well-formed but
    non-array root (e.g. `{"candidates": [...]}`) is correctly rejected as
    the wrong shape, not silently unwrapped. Only on failure does it fall
    back to locating the outermost `[` ... `]` span and parsing just that,
    to tolerate a surrounding markdown fence or prose (models do this
    despite being told not to). Never raises — returns `[]` on anything that
    doesn't parse to a list of non-empty strings; see module SPEC GAP-FILL
    note above."""
    text = text.strip()
    if not text:
        return []

    parsed = _try_json_loads(text)
    if parsed is _UNPARSED:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        parsed = _try_json_loads(text[start : end + 1])

    if parsed is _UNPARSED or not isinstance(parsed, list):
        return []
    return [
        item.strip()
        for item in parsed
        if isinstance(item, str) and item.strip()
    ]


async def run_discovery(
    product_type: str,
    intake: Intake,
    discoverer: Discoverer,
) -> list[str]:
    """§7 item 4: named candidates enter the shortlist automatically and get
    the same treatment as discovered ones. Read here as "at least equal
    priority": named candidates are placed first in merge order so the cap
    never drops one of them ahead of a discovered candidate.

    Dedup is case-insensitive *exact* match only (first occurrence's casing
    wins) — deliberately not fuzzy. "iPad Air" and "iPad Air (2024)" are
    genuinely different candidates to verify against real pages; collapsing
    them here would silently drop a real product.
    """
    named = [c.strip() for c in intake.named_candidates if c.strip()]
    discovered = await discoverer.discover(product_type)

    seen: set[str] = set()
    merged: list[str] = []
    for name in named + discovered:
        key = name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(name)

    return merged[:MAX_DISCOVERY_CANDIDATES]


class SdkDiscoverer:
    """Real `Discoverer` — calls the `scout-researcher` Haiku agent via the
    SDK. Not unit tested (see module docstring)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def discover(self, product_type: str) -> list[str]:
        # §3: "add a startup assertion that skills actually loaded — fail
        # loudly rather than silently running without the recommendation
        # logic." Checked before anything else — no point spending a call
        # only to discover the research protocol wasn't there to read.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)

        prompt = DISCOVERY_PROMPT_TEMPLATE.format(
            product_type=product_type,
            max_candidates=MAX_DISCOVERY_CANDIDATES,
            max_searches=config.MAX_DISCOVERY_SEARCHES,
        )
        options = ClaudeAgentOptions(
            model=self._model,
            agents={
                "scout-researcher": AgentDefinition(
                    description=(
                        "Searches the web and shortlists candidate "
                        "products in a category."
                    ),
                    prompt=prompt,
                    tools=["WebSearch", "WebFetch"],
                    model=self._model,
                    skills=[config.RESEARCH_PROTOCOL_SKILL],
                ),
            },
            allowed_tools=["WebSearch", "WebFetch"],
            permission_mode="acceptEdits",
            setting_sources=["project"],
            # The main session (not a dispatched subagent) is what actually
            # runs `prompt` below — ClaudeAgentOptions.skills is "the single
            # place to turn skills on" for it per the installed SDK's own
            # docs; AgentDefinition.skills above is set too so this graduates
            # cleanly once an orchestrator dispatches through `agents`.
            skills=[config.RESEARCH_PROTOCOL_SKILL],
        )

        final_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text  # keep overwriting; last wins

        return _parse_candidate_list(final_text)
