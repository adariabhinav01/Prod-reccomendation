"""Phase 4 — TIMING (spec docs/handoff.md §1/§6.5, build order step 10).

One Haiku pass produces the run's single, category-level `TimingAssessment`
— an imminent successor, a category-wide price trend, or a landing
technology transition, each claim carrying its basis (§6.5). Unlike SURVEY
or EXTRACTION, this phase has no gate, no interrupt, and no re-prompt loop
in the spec's own text — it's a single research pass, parsed the same way
`phases/survey.py`'s `SdkSurveyor` parses its final JSON message.

This module owns three things:

1. `TimingResearcher` — the seam through which the actual research call
   happens. Named for the phase's job rather than a one-word agent noun
   (`Surveyor`/`Extractor`/`Refiner`/`Scorer`/`Synthesizer`'s pattern) —
   "Timer" reads as a stdlib collision and nothing else fits as cleanly;
   flagged here rather than silently breaking the naming convention.
2. `_enforce_basis_discipline` — the one piece of §6.5 that needs runtime
   logic (see below).
3. `run_timing` — thin orchestration on top of the seam.

### `TimingAssessment` is category-level, not per-product

`RunRecord.timing: TimingAssessment` is a single object, not a
`dict[str, TimingAssessment]` — so this phase reasons about the category
as a whole ("is *a* successor imminent," "is there a category-wide price
trend"), grounded by the current shortlist's names for context ("is one of
these specific products about to be superseded"), not a separate
assessment per product. `run_timing` takes `product_names: list[str]` for
exactly that grounding, mirroring how `phases/extraction.py` takes a
`candidates: list[str]` it doesn't itself choose.

### §6.5's basis discipline is enforced in Python, not just asked for

The skill and prompt both ask Haiku to carry a basis for every claim, but
asking is not enforcing (the same argument invariant 4 makes about
`confidence`, applied to a different field). `_enforce_basis_discipline`
is the one deterministic check available without re-deriving Haiku's own
judgment: a `TimingAssessment` that reports `signal_found=True` with an
EMPTY `basis_notes` is definitionally the bare-claim failure §6.5 forbids,
however good the model's underlying find might have been — there is
nothing here for a reader to check. Rather than reject the whole phase or
issue a re-prompt (no bound for one is given anywhere in the spec for this
phase, unlike §5.1's explicit two-re-prompt allowance for SCORING), this
downgrades the assessment to `signal_found=False` and logs a caveat —
matching `phases/survey.py`'s established "drop what doesn't verify, log
one caveat, never fail the whole phase" precedent (`_verify_exemplars`,
`_validate_secondhand_risk_factors`) rather than inventing a new failure
shape for this phase specifically. This does NOT reach into whether each
individual basis note is *actually* a good basis — that's exactly the kind
of judgment invariant 4 reserves for the model; it only catches the
structurally-checkable case of a signal claimed with no basis at all.

### Not threaded through: `low_evidence_mode`, and §6.5's basis-per-claim
### granularity

Neither this phase's prompt nor its signature mentions `low_evidence_mode`
— an inherited gap, not one introduced here: `phases/extraction.py` (build
order step 8) has the identical gap already. See `phases/prior_gen.py`'s
module docstring, added the same build step, for the fuller note.

Separately, `_enforce_basis_discipline` only checks that `basis_notes` is
non-empty when `signal_found` is true — it cannot verify that EVERY
individual claim (`successor_expected`, `price_trend`,
`technology_transition`) has its own basis, because `basis_notes` is a
flat `list[str]` with no field-to-claim pairing in the locked schema. A
response with one populated basis note and three populated claim fields
passes this check. This catches the structurally-checkable failure (a
signal asserted with NO basis at all) — the finer-grained one is left to
the model's own discipline, same as §6.3's "don't launder confidence into
score" is prose guidance rather than a Python check.

### No ledger *validation* here, only ledger *population*

`TimingAssessment` (models.py) has no `SourcedValue` field anywhere —
`basis_notes` is `list[str]`, plain prose, not `list[SourcedValue]`. So
there is nothing here for §4.3's ledger check to validate against, unlike
`phases/survey.py`'s `secondhand_risk_factors`. `run_timing`/
`SdkTimingResearcher.research()` still take/wire a `FetchLedger` — not to
check anything this phase produces, but because §4.3 is explicit that the
ledger is "run-scoped, not phase-scoped": a later phase's `SourcedValue`
could legitimately cite a page THIS phase fetched, and that only works if
this phase's own `WebFetch`/`WebSearch` calls populate the same
run-scoped ledger instance every other research phase does.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from pydantic import BaseModel

from product_scout import config
from product_scout.hooks.ledger import FetchLedger, ledger_hook_matchers
from product_scout.models import TimingAssessment
from product_scout.skills import assert_skill_loaded

TIMING_PROMPT_TEMPLATE = """You are scout-timer, researching timing signals \
for "{product_type}" — a buyer is comparing: {product_names}.

Look for an imminent successor/refresh, a category-wide price trend, or a \
landing technology transition, following your market-timing skill exactly. \
Every claim you make must carry its basis in basis_notes — never state one \
as bare fact. If nothing credible turns up, set signal_found to false \
rather than manufacturing a signal.

Budget yourself to roughly {max_searches} searches.

A fetch or search failure is routine, not exceptional — report what you \
couldn't reach and move on; don't retry the same query and don't route \
around a failure through another method.

When you are done, your FINAL message must be, and contain nothing except, \
a single JSON object matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


@runtime_checkable
class TimingResearcher(Protocol):
    """Seam for Phase 4's actual research call."""

    async def research(
        self, product_type: str, product_names: list[str], ledger: FetchLedger
    ) -> TimingAssessment: ...


def _enforce_basis_discipline(
    assessment: TimingAssessment,
) -> tuple[TimingAssessment, list[str]]:
    """§6.5: 'Every timing claim carries its basis... never bare fact.' See
    module docstring — this is a structural check (basis present or not),
    not a judgment about basis quality."""
    if assessment.signal_found and not assessment.basis_notes:
        caveat = (
            "Timing research reported a signal with no stated basis (§6.5 "
            "requires one for every claim) — downgraded to 'no timing "
            "signal found' rather than rendering an unbacked assertion."
        )
        downgraded = assessment.model_copy(
            update={
                "signal_found": False,
                "successor_expected": None,
                "price_trend": None,
                "technology_transition": None,
                "recommends_wait": False,
            }
        )
        return downgraded, [caveat]
    return assessment, []


class TimingOutcome(BaseModel):
    timing: TimingAssessment
    caveats: list[str] = []


async def run_timing(
    product_type: str,
    product_names: list[str],
    ledger: FetchLedger,
    researcher: TimingResearcher,
) -> TimingOutcome:
    """Run Phase 4 end to end: research (one Haiku call), then enforce
    §6.5's basis discipline in Python. No gate, no interrupt, no re-prompt
    loop — see module docstring on why this phase doesn't have one."""
    assessment = await researcher.research(product_type, product_names, ledger)
    assessment, caveats = _enforce_basis_discipline(assessment)
    return TimingOutcome(timing=assessment, caveats=caveats)


# ---------------------------------------------------------------------------
# SdkTimingResearcher — real TimingResearcher. Not unit tested here (no
# ANTHROPIC_API_KEY in this suite, consistent with every other real SDK
# adapter in this codebase).
# ---------------------------------------------------------------------------

_UNPARSED = object()


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_timing_json(text: str) -> dict | None:
    """Same whole-message-then-outermost-braces fallback every other real
    adapter in this codebase uses (`survey.py`/`refine.py`/`scoring.py`/
    `synthesis.py`)."""
    text = text.strip()
    if not text:
        return None

    parsed = _try_json_loads(text)
    if parsed is _UNPARSED:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        parsed = _try_json_loads(text[start : end + 1])

    if parsed is _UNPARSED or not isinstance(parsed, dict):
        return None
    return parsed


class SdkTimingResearcher:
    """Real `TimingResearcher` — calls Haiku directly via the SDK, no
    dispatched subagent (§3). Not unit tested (see module docstring)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def research(
        self, product_type: str, product_names: list[str], ledger: FetchLedger
    ) -> TimingAssessment:
        # §3: fail loudly before spending anything if either skill isn't
        # there to be loaded.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)
        assert_skill_loaded(config.MARKET_TIMING_SKILL)

        prompt = TIMING_PROMPT_TEMPLATE.format(
            product_type=product_type,
            product_names=", ".join(product_names) if product_names else "none named yet",
            max_searches=config.MAX_TIMING_SEARCHES,
            schema=json.dumps(TimingAssessment.model_json_schema()),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=["WebSearch", "WebFetch"],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            hooks={"PostToolUse": ledger_hook_matchers(ledger)},  # §4.3 population, not validation
            skills=[config.RESEARCH_PROTOCOL_SKILL, config.MARKET_TIMING_SKILL],
        )

        final_text = ""
        async for message in query(prompt=prompt, options=options):
            if not isinstance(message, AssistantMessage):
                continue
            content = message.content
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, TextBlock):
                    final_text = block.text  # keep overwriting; last wins

        parsed = _parse_timing_json(final_text)
        if parsed is None:
            raise RuntimeError(
                "scout-timer's final message did not contain a parseable "
                f"TimingAssessment JSON object: {final_text!r}"
            )
        return TimingAssessment(**parsed)
