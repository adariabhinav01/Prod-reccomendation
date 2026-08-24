"""Phase 3 — EXTRACTION (spec docs/handoff.md §1/§3/§4, build order step 5).

Fetch pages, extract specs into the validated schema, Haiku, `WebFetch` +
`mcp__scout__record_product` (§3's `scout-extractor` `AgentDefinition`).
Mirrors `phases/discovery.py`'s convention: an `Extractor` seam, thin pure
orchestration on top of it, and a real SDK-calling adapter.

Unlike discovery, extraction's correctness does not depend on parsing any
free text at all: every `Product` that ends up in the result arrived through
a validated `record_product` tool call (schema pre-validation, then
`tools/record_product.py`'s pydantic construction) — never through
interpreting the model's prose. If the model's chatter around the tool calls
is garbage, it simply doesn't matter; only the tool calls do.

`SdkExtractor` is not unit tested here (no `ANTHROPIC_API_KEY` in this
suite) — only `run_extraction`'s pass-through/short-circuit logic (against a
fake) is. `record_product`'s own validation is what step 5 explicitly asks
to verify, and that's covered directly in `tests/test_record_product.py`
without needing any SDK call at all.

`SdkExtractor` depends on `.claude/skills/research-protocol/SKILL.md` (§11
— source tiering, extraction rules, conflict handling, low-evidence rules)
being loaded for the session, since `EXTRACTION_PROMPT_TEMPLATE` itself only
covers the omit-don't-invent rule and fetch-failure handling, not the full
Tier 1-5 system or the "≥2 Tier 2/3 sources" / "no sole-source performance
claims without methodology" rules. `assert_skill_loaded` (§3's "fail loudly"
requirement) checks the skill file exists before any call is made.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from product_scout import config
from product_scout.models import Product
from product_scout.skills import assert_skill_loaded
from product_scout.tools.record_product import ListProductSink
from product_scout.tools.server import build_scout_server

EXTRACTION_PROMPT_TEMPLATE = """You are scout-extractor. For each candidate \
product below in "{product_type}", fetch its manufacturer page and up to \
{max_fetches_per_product} independent review/spec pages, then call \
record_product exactly once per product with everything you found.

If you cannot find a real, citable http(s) URL for a spec, OMIT that spec \
entirely from the `specs` you send — never invent or guess a URL. If \
record_product rejects your call, its error message tells you exactly \
which field(s) to drop; drop them and call record_product again for that \
same product.

A fetch failure is routine, not exceptional — report what you couldn't \
reach and move on to the next source; don't retry the same URL and don't \
route around it through another fetch method.

Candidates:
{candidate_list}"""


@runtime_checkable
class Extractor(Protocol):
    """Seam for Phase 3's actual fetch + extract."""

    async def extract(
        self, product_type: str, candidates: list[str]
    ) -> list[Product]: ...


async def run_extraction(
    product_type: str,
    candidates: list[str],
    extractor: Extractor,
) -> list[Product]:
    """Thin by design. Short-circuits to `[]` without calling the extractor
    when there's nothing to extract, so an empty shortlist never spends a
    model call. Named as a seam (rather than every future caller invoking
    `extractor.extract()` directly) so cross-cutting logic — e.g. wiring in
    `intake.filter_by_required_features`, or a policy for when fewer
    products come back than candidates went in — has a home later without
    every caller needing to know about it. Nothing more is added
    preemptively; no real caller (`orchestrator.py`) exists yet.
    """
    if not candidates:
        return []
    return await extractor.extract(product_type, candidates)


class SdkExtractor:
    """Real `Extractor` — calls `scout-extractor` via the SDK with
    `record_product` wired to a fresh `ListProductSink` per call. Not unit
    tested (see module docstring)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def extract(
        self, product_type: str, candidates: list[str]
    ) -> list[Product]:
        # §3: "add a startup assertion that skills actually loaded — fail
        # loudly rather than silently running without the recommendation
        # logic." Checked before spending anything on fetches.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)

        sink = ListProductSink()
        scout_server = build_scout_server(sink)
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            product_type=product_type,
            max_fetches_per_product=config.MAX_EXTRACTION_FETCHES_PER_PRODUCT,
            candidate_list="\n".join(f"- {c}" for c in candidates),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            agents={
                "scout-extractor": AgentDefinition(
                    description=(
                        "Fetches product and review pages and extracts "
                        "specs into the required schema."
                    ),
                    prompt=prompt,
                    tools=["WebFetch", "mcp__scout__record_product"],
                    model=self._model,
                    skills=[config.RESEARCH_PROTOCOL_SKILL],
                ),
            },
            mcp_servers={"scout": scout_server},
            allowed_tools=["WebFetch", "mcp__scout__record_product"],
            permission_mode="acceptEdits",
            setting_sources=["project"],
            # See discovery.py's SdkDiscoverer for why this is set at both
            # levels — the main session (not a dispatched subagent) is what
            # actually runs `prompt` below.
            skills=[config.RESEARCH_PROTOCOL_SKILL],
        )

        async for _message in query(prompt=prompt, options=options):
            pass  # side effects land in `sink` via record_product; nothing
            # to collect from the message stream itself

        return sink.products
