"""Phase 3 — EXTRACTION (spec docs/handoff.md §1/§3/§4/§4.3, build order
step 8).

Fetch pages, extract specs into the validated schema, Haiku, `WebFetch` +
`WebSearch` + `mcp__scout__record_product`. Mirrors `phases/survey.py`'s
convention: an `Extractor` seam, thin pure orchestration on top of it, and
a real SDK-calling adapter — `phases/survey.py`'s own docstring is the
template this module follows, including its two deliberate departures from
the pre-v7 debt this file used to carry.

### `WebSearch` — added post-v7, not in the original spec

§3's original worked example gave this phase `WebFetch` only, on the
assumption a candidate's manufacturer page could always be reached by a
guessed/constructed URL from its bare name (`Cluster.exemplar_products`,
§8.1a — deliberately unsourced, no URL attached). Live verification
against a `rich`/high-differentiation category (many small brands, no
single guessable URL pattern) showed this fails in exactly that shape: the
model fell back to fetching retailer search-result pages and brand
homepages, never reached a real per-product page, and recorded zero
products across every candidate despite genuine effort (102 turns, 67
fetches, `terminal_reason=completed`) — not a turn-budget cutoff, not a
batching problem (tested independently first), a genuine discovery gap.
`WebSearch` is capped far tighter than SURVEY's own budget
(`config.MAX_EXTRACTION_SEARCHES_PER_PRODUCT`, see that constant's own
comment) — a fallback for resolving "what's the real URL for this
candidate," not a second broad research pass. §4.3's admissibility rule is
unchanged: a URL only ever seen via search still isn't admissible as a
spec/price source until it's actually fetched with `WebFetch` in the same
conversation — search only helps find WHAT to fetch, never substitutes for
fetching it. `docs/handoff.md` §3 has the matching amendment note.

Unlike SURVEY, extraction's correctness does not depend on parsing any
free text at all: every `Product` that ends up in the result arrived
through a validated `record_product` tool call (JSON-schema pre-
validation, then `tools/record_product.py`'s pydantic construction plus
§4.3 ledger check) — never through interpreting the model's prose. If the
model's chatter around the tool calls is garbage, it simply doesn't
matter; only the tool calls do.

### What this rewrite fixes relative to the module it replaces

The version built at build order step 5 predates `phases/survey.py`'s
fixes and still carried both of the mistakes that module's docstring
warns about:

1. **`agents={"scout-extractor": AgentDefinition(...)}`** — a dispatched
   subagent, which §3 rules out entirely ("nothing dispatches subagents...
   `\"Agent\"` is never in `allowed_tools`"). `SdkExtractor` below calls
   `query()` directly instead, exactly like `SdkSurveyor`.
2. **`permission_mode="acceptEdits"`** — §3.2 names this specific setting
   as an earlier draft's error: "granting a permission nothing in the
   design uses." Fixed to `"default"`.

It also updates the `Extractor`/`SdkExtractor` contract for two things
`record_product.py`'s own rewrite (this same build step) now needs:
`survey: SurveyReport` (so `record_product` can compute each `Product`'s
`EvidenceProfile` against `survey.comparison_specs`/`category_kind`, per
§4.0d) and `ledger: FetchLedger` (§4.3 — see `hooks/ledger.py`'s "run-
scoped, not phase-scoped" note on why this is a required parameter with no
default).

`SdkExtractor` is not unit tested here (no `ANTHROPIC_API_KEY` in this
suite, consistent with `SdkSurveyor`) — only `run_extraction`'s pass-
through/short-circuit logic (against a fake) is. `record_product`'s own
validation — including the three §4.3 cases this build step names — is
covered directly in `tests/test_record_product.py` and
`tests/test_ledger.py`, without needing any SDK call at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from product_scout import config
from product_scout.hooks.budget import (
    RunBudget,
    cost_cap_post_tool_use_matchers,
    cost_cap_pre_tool_use_matchers,
)
from product_scout.hooks.ledger import FetchLedger, ledger_hook_matchers
from product_scout.hooks.progress import ProgressFn, format_result_progress, progress_hook_matchers
from product_scout.hooks.source_guard import source_guard_hook_matchers
from product_scout.models import Location, Product, SurveyReport
from product_scout.skills import assert_skill_loaded
from product_scout.tools.record_product import ListProductSink
from product_scout.tools.server import build_scout_server

EXTRACTION_PROMPT_TEMPLATE = """You are scout-extractor. For each candidate \
product below in "{product_type}", fetch its manufacturer page and up to \
{max_fetches_per_product} independent review/spec pages, then call \
record_product exactly once per product with everything you found. Report \
generation as "current" — prior-generation research is a separate pass, \
not your job here.

WORK ONE CANDIDATE AT A TIME, IN ORDER. Research a single candidate — its \
manufacturer page plus up to {max_fetches_per_product} review/spec pages — \
then call record_product for THAT candidate before starting research on \
the next one. Do not fetch broadly across many or all candidates first and \
save every record_product call for the end: if you run out of turns, a \
partially-researched product you already recorded is far more useful than \
a well-researched one you never recorded. A thin-but-recorded entry beats \
a thorough-but-missing one every time.

You do NOT already know each candidate's manufacturer page URL. If you \
cannot confidently construct it from the candidate's name alone, use \
WebSearch — at most {max_searches_per_product} searches per candidate — to \
find it, then WebFetch the real page it points to. Never guess a retailer \
search-results URL (e.g. a site's `?q=`/`/s?k=` search endpoint) and treat \
whatever it returns as a product page; those pages are not reliable \
sources and fetching one is not a substitute for finding the real one. A \
URL you only saw in search results is never itself an admissible source \
for a spec or the price — you still have to WebFetch the real page in this \
same conversation before citing it.

If you cannot find a real, citable http(s) URL for a spec, OMIT that spec \
entirely from the `specs` you send — never invent or guess a URL. A \
source_url only ever counts if you actually fetched that page with \
WebFetch in this same conversation; a URL you merely saw in a search \
result is not admissible for a spec or the price, only for ownership_notes \
or review_sources. If record_product rejects your call, its error message \
tells you exactly which field(s) to drop or re-fetch; fix those and call \
record_product again for that same product.

The buyer is shopping from {location_country} ({location_currency}). For \
each product, determine whether the storefront you researched is the \
region-appropriate one for them — the inference ladder and reporting rules \
are in your skill instructions. Only research and report \
shipping_estimate_native/duty_estimate_native once you've confirmed the \
product ships from outside {location_country}; leave both null otherwise.

LOW-EVIDENCE MODE: {low_evidence_mode}. When true, follow your research \
protocol skill's low-evidence-mode section exactly — community sources and \
unverified manufacturer performance claims become admissible (labeled as \
such), and every relaxation you make must be something the report can name \
explicitly. When false, a community source is never admissible as a \
spec's source_type — record_product rejects it; put reliability/ownership \
sentiment in ownership_notes instead.

A fetch failure is routine, not exceptional — report what you couldn't \
reach and move on to the next source; don't retry the same URL and don't \
route around it through another fetch method.

Candidates:
{candidate_list}"""


@runtime_checkable
class Extractor(Protocol):
    """Seam for Phase 3's actual fetch + extract."""

    async def extract(
        self,
        product_type: str,
        candidates: list[str],
        survey: SurveyReport,
        ledger: FetchLedger,
        location: Location,
        low_evidence_mode: bool,
        budget: RunBudget,
        progress: ProgressFn | None = None,
    ) -> list[Product]: ...


async def run_extraction(
    product_type: str,
    candidates: list[str],
    survey: SurveyReport,
    ledger: FetchLedger,
    location: Location,
    low_evidence_mode: bool,
    budget: RunBudget,
    extractor: Extractor,
    progress: ProgressFn | None = None,
) -> list[Product]:
    """Thin by design. Short-circuits to `[]` without calling the
    extractor when there's nothing to extract, so an empty shortlist never
    spends a model call. Named as a seam (rather than every future caller
    invoking `extractor.extract()` directly) so cross-cutting logic — e.g.
    wiring in `intake.filter_by_required_features`, or a policy for when
    fewer products come back than candidates went in — has a home later
    without every caller needing to know about it. Nothing more is added
    preemptively; no real caller (`orchestrator.py`) exists yet.

    `location` (build order step 11) is the run's resolved `Location` —
    threaded straight through to `build_scout_server`'s §10.3
    confirmed-cross-border gate. `low_evidence_mode` (build order step 12)
    is threaded the same way, to both the prompt and §14/§8.3's mode-gated
    `record_product` checks. `budget: RunBudget` (build order step 13) is
    threaded the same way again — required, no default, so the orchestrator
    can't accidentally hand this phase a fresh (i.e. reset) budget; see
    `hooks/budget.py`'s module docstring.
    """
    if not candidates:
        return []
    return await extractor.extract(
        product_type, candidates, survey, ledger, location, low_evidence_mode, budget,
        progress=progress,
    )


class SdkExtractor:
    """Real `Extractor` — calls Haiku directly via the SDK, no dispatched
    subagent (see module docstring). Wires `hooks/ledger.py`'s
    `PostToolUse` hook so this call's own `WebFetch`s populate `ledger` —
    the same instance `record_product` validates against — making the
    fetch -> ledger -> validate loop genuinely functional within this one
    phase's `query()`, even though true cross-phase run-scoping (citing a
    page SURVEY fetched) needs an orchestrator (not yet built) to pass in
    an already-populated `ledger` instead of a fresh one. Not unit tested
    (see module docstring)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def extract(
        self,
        product_type: str,
        candidates: list[str],
        survey: SurveyReport,
        ledger: FetchLedger,
        location: Location,
        low_evidence_mode: bool,
        budget: RunBudget,
        progress: ProgressFn | None = None,
    ) -> list[Product]:
        # §3: "add a startup assertion that skills actually loaded — fail
        # loudly rather than silently running without the recommendation
        # logic." Checked before spending anything on fetches.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)

        sink = ListProductSink()
        scout_server = build_scout_server(
            sink, survey, ledger, location, low_evidence_mode, progress
        )
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            product_type=product_type,
            max_fetches_per_product=config.MAX_EXTRACTION_FETCHES_PER_PRODUCT,
            max_searches_per_product=config.MAX_EXTRACTION_SEARCHES_PER_PRODUCT,
            location_country=location.country,
            location_currency=location.currency,
            low_evidence_mode=low_evidence_mode,
            candidate_list="\n".join(f"- {c}" for c in candidates),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=["WebFetch", "WebSearch", "mcp__scout__record_product"],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            mcp_servers={"scout": scout_server},
            hooks={
                "PreToolUse": source_guard_hook_matchers(low_evidence_mode)
                + cost_cap_pre_tool_use_matchers(budget),
                "PostToolUse": ledger_hook_matchers(ledger)
                + cost_cap_post_tool_use_matchers(budget)
                + progress_hook_matchers(progress),
            },
            skills=[config.RESEARCH_PROTOCOL_SKILL],
        )

        async for message in query(prompt=prompt, options=options):
            # side effects land in `sink` via record_product; nothing else
            # to collect from the message stream — except the terminal
            # ResultMessage, whose terminal_reason/num_turns is otherwise
            # the one signal this phase has for "why did nothing come
            # back" (§16.2).
            if isinstance(message, ResultMessage) and progress:
                await progress(format_result_progress(message))

        return sink.products
