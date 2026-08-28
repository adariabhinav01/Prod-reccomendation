"""Phase 1 — SURVEY (spec docs/handoff.md §1/§8.1/§8.1a/§8.2, build order
step 5).

One Haiku pass produces the whole `SurveyReport` — coverage, differentiation,
category_kind, pricing_complexity, secondhand_risk_factors, region scoping
(done *inside* this phase, before clustering — §8.1), clusters, and
dimensions. This module owns three things:

1. `Surveyor` — the seam through which the actual survey happens (mirrors
   `phases/discovery.py`'s `Discoverer` / `phases/probe.py`'s
   `CoverageProber`).
2. `_verify_exemplars` — the one piece of §8.1a's four constraints that
   needs runtime logic (see below).
3. `run_survey` — the §8.1a coverage gate / broadening interrupt / one-shot
   latch state machine, built on top of the seam.

### `_repair_comparison_specs` — a real, reproducible gap found by build
### order step 12's live verification, not a hypothetical

Step 12's own §17.2 task ("low-evidence mode tested against a genuinely
obscure category") ran `SdkSurveyor.survey()` live, four times, against two
real obscure categories. Three of the four responses had Haiku emit a
`dimensions` list containing a name it then forgot to also list in
`comparison_specs` — §4.0b's own validator ("must contain every
`Dimension.name`") correctly rejected all three as a bare `ValidationError`,
which `SdkSurveyor.survey()` had no recovery from at all: the whole phase
crashed. A 75%-in-practice failure rate on a real, unmocked call is not a
hypothetical edge case.

The fix is a Python-side repair, not a re-prompt: `comparison_specs` being
required to be a superset of `Dimension.name` is a mechanical consistency
rule, not a judgment call, so there is nothing to ask the model to
reconsider — `Dimension.name` is by definition something the model
intended as a real comparable axis (`Dimension`'s own docstring: "drives
§9.7's stopping condition"), so unioning missing names into
`comparison_specs` is the faithful reading of what the model meant, not a
guess. `_repair_comparison_specs` does exactly that, on the raw dict,
*before* `SurveyReport(**parsed)` ever runs — proactively, not inside a
try/except, since the fix is idempotent and free when nothing needs it.
Logged as a caveat when it actually fires (`RawSurvey.caveats`), never
silent — matching this module's own "degrade explicitly" precedent
(`_verify_exemplars`/`_validate_secondhand_risk_factors`).

### This module adapts `phases/probe.py`, and deliberately does NOT copy two
### of its patterns

`probe.py` (pre-v7) already implements almost exactly this gate/interrupt/
latch state machine, against the old `CoverageReport` model and the old
`QuestionPort.ask()` signature. It's the template `run_survey` below is
adapted from. Two things differ under the current (v7) `io/port.py` and §3,
and must not be carried forward from `probe.py` (or from `discovery.py`/
`extraction.py`, which share the same pre-v7 debt):

**1. `ask_choice`'s stricter contract.** The current `QuestionPort.ask_choice
(question, options, escape_hatch)` requires `len(options) >= 2` and always
renders `escape_hatch` as one extra trailing menu item. `probe.py`'s old
"cap at 2 broader categories" workaround doesn't apply here (nothing in v7's
port caps option count — §8.2's "present all of them together" is followed
literally), but a new problem does: when SURVEY suggests *zero* broader
categories, the two fixed choices (`KEEP_SCOPE_OPTION`, `STOP_OPTION`) can't
satisfy `options >= 2` without duplicating one of them in the rendered menu
or inventing a third, spec-uncited choice. `_run_interrupt` below resolves
this by using `ask_choice` normally whenever `>=1` broader category exists
(`options` = category labels + `KEEP_SCOPE_OPTION`, `escape_hatch=
STOP_OPTION` — this exactly matches §8.2's worked example, and the floor is
always satisfied since categories contribute at least one entry), and
falling back to a plain yes/no via `ask_text` — mirroring `phases/intake.py`'s
`_ask_yes_no` pattern — only in the zero-category edge case.

**2. §3's "nothing dispatches subagents" rule.** `discovery.py`/
`extraction.py` both wrap their real call in `agents={"scout-...":
AgentDefinition(...)}` — but §3's own code block is explicit: "There is no
top-level `model=`, no `agents={}` block, and `\"Agent\"` is never in
`allowed_tools`." Fixing those two files is out of scope here; `SdkSurveyor`
below just doesn't repeat the mistake — it calls `query()` directly with a
plain `ClaudeAgentOptions`, no `agents=`, and `permission_mode="default"`
(not `"acceptEdits"`, which §3.2 explicitly names as an earlier draft's
error: "granting a permission nothing in the design uses").

### §8.1a's four constraints — what's runtime logic vs. already structural

`Cluster.exemplar_products` and `price_range_native` are the system's only
unsourced values (§8.1a) — they reach question copy before a single URL has
been fetched. Four constraints:

1. Exemplar names must appear verbatim in a fetched page or a search-
   returned result, never model-generated. **This is what `_verify_exemplars`
   enforces** — the only one of the four needing runtime logic.
2. The unsourced *value* fields never enter the (rendered) report as
   report-eligible fact.
3. Question copy marks prices indicative ("around $200," never "$199").
4. These types deliberately don't use `SourcedValue`.

Constraints 2 and 4 are already true by construction — `Cluster` doesn't
inherit `SourcedValue` and never will (see `models.py`'s field comment on
`Cluster.exemplar_products`); there's no report path that could smuggle a
`source_url` onto these fields. Constraint 3 is REFINE's (§9.6, build order
step 7) concern — the question copy that quotes `price_range_native` doesn't
exist yet; this phase never renders it to a user directly. Only constraint 1
needs code, because it's the one place an invented name could slip through
undetected: nothing in the schema stops a model from putting a
plausible-but-fictional product name in `exemplar_products`.

### `Surveyor.survey()` takes `Location`, unlike `Discoverer.discover()`

§8.1: "Region scoping happens here, before clustering — no point clustering
a catalog half of which isn't purchasable." Region scoping needs to know
which region, so `Surveyor.survey(product_type, location)` takes the run's
`Location` — unlike `discover(product_type)`/`probe(product_type)`, which
didn't need it.

### Not in scope for this build step

`RunRecord.commodity_category` (§8.4) is explicitly a *separate*, later
build-order step ("12. Low-evidence and commodity modes") — `SurveyOutcome`
doesn't set it. `low_evidence_mode` *is* in scope (§8.1a's interrupt
produces it directly, exactly as `probe.py`'s `ProbeOutcome` did).

### §4.3 ledger validation on `secondhand_risk_factors` (added post-step-8)

Build order step 8 wired §4.3 ledger validation onto EXTRACTION's
`record_product` but missed the other place `SourcedValue`s enter a run:
`SurveyReport.secondhand_risk_factors`, which §4.3 itself names by name
("SURVEY produces `SourcedValue`s (notably `secondhand_risk_factors`)
largely from search rather than fetching") and which CLAUDE.md invariant 3
names explicitly too ("judgment-bearing SURVEY values may cite
`seen_not_fetched`"). A well-formed `source_url` on a risk factor was
passing straight through with no ledger check at all — the exact
presence-isn't-provenance gap §4.3 exists to close.

`_validate_secondhand_risk_factors` below closes it, mirroring
`_verify_exemplars`'s own shape exactly (drop what doesn't verify, log one
caveat per drop, never fail the whole report). Unlike exemplar names
(checked against the in-call `evidence_pool`), this checks against a
`hooks.ledger.FetchLedger` — the run-scoped provenance ledger built at
step 8 — with `require_fetched=False`, per §4.3's table: judgment-bearing
values (risk factors are exactly that — warnings, not spec facts) accept
either `fetched` or `seen_not_fetched`, never neither.

`run_survey` and `Surveyor.survey()` both take `ledger: FetchLedger` as a
required parameter for this — no default, matching
`phases/extraction.py`'s `Extractor.extract()` precedent and
`hooks/ledger.py`'s own "run-scoped, not phase-scoped" reasoning: a silent
default would just relocate the gap rather than close it once an
orchestrator exists to carry one ledger across phases. `SdkSurveyor.survey()`
wires `hooks/ledger.py`'s `PostToolUse` hook into its own `query()`, exactly
as `SdkExtractor.extract()` already does — so a live SURVEY run's own
`WebSearch`/`WebFetch` calls populate the same ledger this validation
checks against.
"""

from __future__ import annotations

import json
import re
from typing import Literal, Protocol, runtime_checkable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ServerToolResultBlock,
    TextBlock,
    ToolResultBlock,
    UserMessage,
    query,
)
from pydantic import BaseModel

from product_scout import config
from product_scout.hooks.ledger import FetchLedger, ledger_hook_matchers
from product_scout.io.port import QuestionPort
from product_scout.models import (
    BroaderCategory,
    Cluster,
    Location,
    SourcedValue,
    SurveyReport,
)
from product_scout.skills import assert_skill_loaded

KEEP_SCOPE_OPTION = "Keep my original scope, proceed anyway (low-evidence mode)"
STOP_OPTION = "Stop here"

_COVERAGE_DESCRIPTION = {
    # §8.1a's own worked example wording / §8.1's table wording, verbatim.
    "sparse": "thin independent review coverage",
    "barren": "effectively no independent coverage",
}

_YES_TOKENS = frozenset(("y", "yes"))
_NO_TOKENS = frozenset(("n", "no"))

SURVEY_PROMPT_TEMPLATE = """You are scout-surveyor. Research the category \
"{product_type}" for a buyer shopping from {country}.

Do region scoping FIRST, before anything else: work out how many of the \
products you find are actually sold or shippable to {country}, and set \
products_unavailable_in_region accordingly. There is no point clustering a \
catalog half of which isn't purchasable there.

Then assess:
- coverage, per this table:
    rich:     >=8 products, >=5 independent sources, methodology-backed testing
    moderate: 4-8 products, 2-4 independent sources
    sparse:   <4 products, or <=1 independent source
    barren:   effectively no independent coverage
  If sparse or barren, populate suggested_broader_categories with real \
  alternative categories and their estimated coverage.
- differentiation (high / moderate / low)
- category_kind (physical / software_service / hybrid)
- pricing_complexity (simple / subscription_based / financed_major_purchase / hybrid)
- secondhand_risk_factors — each a fact with a REAL url you actually saw in \
  a search result or fetched page, never an invented one
- clusters: group products into archetypes. For each cluster: key, label, \
  exemplar_products (real product names — see the hard rule below), \
  price_range_native (indicative only — this never reaches a user as an \
  exact figure), approximate_member_count.
- dimensions: the spec axes that actually separate your clusters. For each: \
  name, splits (cluster_key -> this cluster's position on the axis), and \
  axis_kind — "position" if both ends are legitimate (e.g. power vs. \
  control), "importance" if one end just means "doesn't matter" (e.g. \
  weight for a laptop is importance, since lighter is monotonically \
  better; weight for a pickleball paddle is position, since there's a \
  real sweet spot — same word, opposite structure, decide per category).
- comparison_specs: 5-10 spec keys for the comparison table; MUST include \
  every dimension's name.

HARD RULE: every name in exemplar_products must appear verbatim (the exact \
product name) in a page you fetched or a search result you were shown. \
Never invent, guess, or lightly paraphrase a product name — an unverified \
name is silently dropped before it ever reaches the user, so inventing one \
only wastes your own effort.

Budget yourself to roughly {max_searches} searches.

A fetch or search failure is routine, not exceptional — report what you \
couldn't reach and move on; don't retry the same query and don't route \
around a failure through another method.

When you are done, your FINAL message must be, and contain nothing except, \
a single JSON object matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


def _normalize_for_match(text: str) -> str:
    """Case- and whitespace-insensitive normalization shared by both sides
    of the §8.1a constraint-1 comparison."""
    return re.sub(r"\s+", " ", text).strip().lower()


class RawSurvey(BaseModel):
    """What one `Surveyor.survey()` call returns: the model's own
    `SurveyReport` plus the `evidence_pool` — search-snippet/fetched-page
    text collected during that same call. Bundled together because
    `_verify_exemplars` needs both, and the evidence is gone once the call
    that produced it returns.

    `caveats` (build order step 12) carries anything a `Surveyor`
    implementation had to correct before `SurveyReport` could even be
    constructed — currently just `_repair_comparison_specs`'s output, for
    `SdkSurveyor`. Empty for `FakeSurveyor`-style test doubles, which hand
    back an already-valid `SurveyReport` with nothing to repair."""

    report: SurveyReport
    evidence_pool: list[str] = []
    caveats: list[str] = []


@runtime_checkable
class Surveyor(Protocol):
    """Seam for Phase 1's actual research + clustering call."""

    async def survey(
        self, product_type: str, location: Location, ledger: FetchLedger
    ) -> RawSurvey: ...


def _verify_exemplars(
    clusters: list[Cluster], evidence_pool: list[str]
) -> tuple[list[Cluster], list[str]]:
    """§8.1a constraint 1. A cluster's `exemplar_products` entries survive
    only if they appear verbatim (case/whitespace-insensitive substring
    match) somewhere in `evidence_pool`. Unverified names are dropped —
    never silently kept — and a caveat is logged per cluster naming what
    was dropped. A cluster losing every exemplar this way ends up with an
    empty list, not an error: `Cluster.exemplar_products` carries no
    `min_length` constraint, and "we have no verified examples for this
    archetype" is itself real information worth keeping the cluster (and
    its dimensions) around for.

    Pure and side-effect-free: returns new `Cluster` instances rather than
    mutating the input list, and never touches `Dimension.splits` (keyed on
    `cluster_key`, unaffected by which exemplar names survive).
    """
    haystack = "\n".join(_normalize_for_match(text) for text in evidence_pool)

    verified: list[Cluster] = []
    caveats: list[str] = []
    for cluster in clusters:
        kept: list[str] = []
        dropped: list[str] = []
        for name in cluster.exemplar_products:
            if _normalize_for_match(name) in haystack:
                kept.append(name)
            else:
                dropped.append(name)

        if dropped:
            caveats.append(
                f'Cluster "{cluster.label}": dropped unverified exemplar '
                f"name(s) {dropped!r} — not found verbatim in any fetched "
                "page or search result (§8.1a); never shown to the user."
            )

        if dropped:
            cluster = cluster.model_copy(update={"exemplar_products": kept})
        verified.append(cluster)

    return verified, caveats


def _apply_exemplar_constraint(
    report: SurveyReport, evidence_pool: list[str]
) -> tuple[SurveyReport, list[str]]:
    verified_clusters, caveats = _verify_exemplars(report.clusters, evidence_pool)
    if verified_clusters != report.clusters:
        report = report.model_copy(update={"clusters": verified_clusters})
    return report, caveats


def _validate_secondhand_risk_factors(
    factors: list[SourcedValue], ledger: FetchLedger
) -> tuple[list[SourcedValue], list[str]]:
    """§4.3, applied to SURVEY's own output — see module docstring. A
    factor survives only if the ledger has SOME record of its
    `source_url`, fetched or merely seen (`require_fetched=False`: these
    are judgment-bearing risk warnings, not spec facts). Unverified
    entries are dropped — never silently kept — and a caveat is logged
    per drop, mirroring `_verify_exemplars` exactly."""
    kept: list[SourcedValue] = []
    caveats: list[str] = []
    for factor in factors:
        if ledger.is_admissible(factor.source_url, require_fetched=False):
            kept.append(factor)
        else:
            caveats.append(
                f"Dropped a secondhand-risk-factor citing {factor.source_url!r} "
                "— not found in this run's fetch/search ledger (§4.3); never "
                "shown to the user."
            )
    return kept, caveats


def _apply_ledger_constraint(
    report: SurveyReport, ledger: FetchLedger
) -> tuple[SurveyReport, list[str]]:
    verified_factors, caveats = _validate_secondhand_risk_factors(
        report.secondhand_risk_factors, ledger
    )
    if verified_factors != report.secondhand_risk_factors:
        report = report.model_copy(update={"secondhand_risk_factors": verified_factors})
    return report, caveats


def _repair_comparison_specs(parsed: dict) -> tuple[dict, list[str]]:
    """§4.0b, enforced proactively rather than reactively (build order step
    12 — see module docstring's "found by live verification" section).
    Operates on the RAW dict, before `SurveyReport(**parsed)` runs, so a
    Haiku response that forgot to list a `Dimension.name` in
    `comparison_specs` gets fixed rather than crashing the whole phase.

    Pure and defensive: only touches `parsed["comparison_specs"]` when
    `parsed["dimensions"]` is a well-formed list of dicts with a string
    `"name"` — anything else (a missing/malformed `dimensions`, a
    malformed `comparison_specs`) is left untouched, on the theory that
    this function's job is fixing ONE specific, well-understood mismatch,
    not sanitizing arbitrary malformed model output; a genuinely broken
    response should still fail loudly at `SurveyReport(**parsed)` (house
    style: validate hard, fail loudly), not be silently coerced into
    something that happens to validate.

    Returns `(possibly-updated parsed dict, caveats)` — a new dict, never
    mutates the input; `caveats` is empty when nothing needed repairing.
    """
    dimensions = parsed.get("dimensions")
    if not isinstance(dimensions, list):
        return parsed, []

    dimension_names = [
        d["name"]
        for d in dimensions
        if isinstance(d, dict) and isinstance(d.get("name"), str) and d["name"]
    ]
    if not dimension_names:
        return parsed, []

    comparison_specs = parsed.get("comparison_specs")
    if not isinstance(comparison_specs, list):
        return parsed, []

    missing = [name for name in dimension_names if name not in comparison_specs]
    if not missing:
        return parsed, []

    repaired = dict(parsed)
    repaired["comparison_specs"] = [*comparison_specs, *missing]
    caveat = (
        f"comparison_specs was missing {missing!r} even though each is a "
        "Dimension.name (§4.0b requires comparison_specs to be a superset) "
        "— added automatically rather than failing the phase; the model's "
        "own dimension naming is trusted here since there is nothing "
        "genuinely ambiguous to re-ask about."
    )
    return repaired, [caveat]


class SurveyOutcome(BaseModel):
    """Phase 1's fully-resolved result — the §8.1a gate already applied.

    Mirrors `phases/probe.py`'s `ProbeOutcome` shape/semantics exactly:
    `proceed=False` means the user picked "Stop here" and the (not yet
    built) orchestrator must halt before spending anything on REFINE/
    EXTRACTION. `survey` is the report for whichever category was
    ultimately settled on, not necessarily the first one surveyed.
    """

    survey: SurveyReport
    proceed: bool
    low_evidence_mode: bool
    product_type: str
    original_product_type: str | None
    category_broadening_offered: bool
    caveats: list[str] = []


def _dedupe_categories(categories: list[BroaderCategory]) -> list[BroaderCategory]:
    """Case-insensitive dedup by name, first occurrence wins — two
    identically-named suggestions would be indistinguishable to
    `ask_choice`'s text-matching."""
    seen: set[str] = set()
    deduped: list[BroaderCategory] = []
    for cat in categories:
        key = cat.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cat)
    return deduped


def _category_label(cat: BroaderCategory) -> str:
    # Mirrors §8.1a's own worked example: `...instead (moderate coverage)`.
    return f'Research "{cat.name}" instead ({cat.estimated_coverage} coverage)'


def _format_interrupt_question(product_type: str, report: SurveyReport) -> str:
    testing_note = (
        "methodology-backed testing exists"
        if report.has_methodology_backed_testing
        else "no methodology-backed testing"
    )
    return (
        f'Coverage check: "{product_type}" has '
        f"{_COVERAGE_DESCRIPTION[report.coverage]}.\n"
        f"  Found: {report.estimated_product_count} product(s), "
        f"{report.independent_review_sources_found} independent review "
        f"source(s), {testing_note}."
    )


async def _ask_yes_no(port: QuestionPort, prompt: str) -> bool:
    """Same loop as `phases/intake.py`'s private `_ask_yes_no` — kept as a
    small local copy rather than a cross-phase import, matching this
    codebase's existing convention of each phase module being self-
    contained (`discovery.py`/`extraction.py`/`probe.py` don't share
    helpers with each other either)."""
    while True:
        raw = (await port.ask_text(prompt)).strip().lower()
        if raw in _YES_TOKENS:
            return True
        if raw in _NO_TOKENS:
            return False
        prompt = f"{raw!r} — please answer yes or no."


async def _run_interrupt(
    product_type: str, report: SurveyReport, port: QuestionPort
) -> tuple[Literal["stop", "keep", "broaden"], BroaderCategory | None]:
    """The one §8.2 interrupt. See module docstring point 1 for why the
    zero-category case can't just be a degenerate `ask_choice` call."""
    categories = _dedupe_categories(report.suggested_broader_categories)
    question = _format_interrupt_question(product_type, report)

    if categories:
        options = [_category_label(c) for c in categories] + [KEEP_SCOPE_OPTION]
        choice_map = dict(zip(options, categories))
        answer = await port.ask_choice(question, options, escape_hatch=STOP_OPTION)
        if answer == STOP_OPTION:
            return "stop", None
        if answer == KEEP_SCOPE_OPTION:
            return "keep", None
        return "broaden", choice_map[answer]

    proceed = await _ask_yes_no(
        port,
        f"{question}\nNo broader category to suggest. Proceed anyway in "
        "low-evidence mode? (yes/no — no stops here)",
    )
    return ("keep", None) if proceed else ("stop", None)


async def run_survey(
    product_type: str,
    location: Location,
    surveyor: Surveyor,
    port: QuestionPort,
    ledger: FetchLedger,
) -> SurveyOutcome:
    """Run Phase 1 end to end: survey, verify exemplars (§8.1a constraint
    1), §4.3-validate secondhand_risk_factors against `ledger`, gate on
    coverage, interrupt at most once (§8.2).

    A `rich`/`moderate` result proceeds immediately, no interrupt, no
    low-evidence mode. A `sparse`/`barren` result halts *before* REFINE/
    EXTRACTION spend anything and interrupts the user — but only the first
    time this fires in the run. If the user broadens into another
    category, that category is re-surveyed but the interrupt is never
    shown a second time (§8.1a): a further sparse/barren result just
    proceeds in low-evidence mode with a caveat logged, exactly like
    "keep original scope" would have.

    This loop runs at most twice: the initial survey, and — only if the
    user chose to broaden — one re-survey of the broadened category, which
    always returns (either it clears the bar, or the latch is now set and
    it degrades automatically). No unbounded recursion is possible.
    """
    original_product_type: str | None = None
    category_broadening_offered = False
    current_type = product_type
    caveats: list[str] = []

    while True:
        raw = await surveyor.survey(current_type, location, ledger)
        caveats.extend(raw.caveats)  # e.g. _repair_comparison_specs (step 12)
        report, verify_caveats = _apply_exemplar_constraint(
            raw.report, raw.evidence_pool
        )
        caveats.extend(verify_caveats)
        report, ledger_caveats = _apply_ledger_constraint(report, ledger)
        caveats.extend(ledger_caveats)

        if report.coverage in ("rich", "moderate"):
            return SurveyOutcome(
                survey=report,
                proceed=True,
                low_evidence_mode=False,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=category_broadening_offered,
                caveats=caveats,
            )

        # report.coverage is "sparse" or "barren" from here on.

        if category_broadening_offered:
            # The edge case (§8.1a): already answered the broadening
            # question once this run — broadening into ANOTHER sparse/
            # barren category must not re-interrupt. Proceed degraded.
            caveats.append(
                f'Broadened category "{current_type}" also surveyed as '
                f"{report.coverage} ({report.notes}); proceeding in "
                "low-evidence mode without asking again — the broadening "
                "question was already answered once this run (§8.1a)."
            )
            return SurveyOutcome(
                survey=report,
                proceed=True,
                low_evidence_mode=True,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        # First sparse/barren hit this run — the one interrupt (§8.2).
        decision, chosen_category = await _run_interrupt(current_type, report, port)
        # The latch trips the moment the interrupt is shown, regardless of
        # what the user picks — "a user who has already declined to
        # broaden once has answered the question" (§8.1a).
        category_broadening_offered = True

        if decision == "stop":
            return SurveyOutcome(
                survey=report,
                proceed=False,
                low_evidence_mode=False,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        if decision == "keep":
            caveats.append(
                f'Coverage for "{current_type}" is {report.coverage} '
                f"({report.notes}); user kept the original scope and "
                "proceeded in low-evidence mode."
            )
            return SurveyOutcome(
                survey=report,
                proceed=True,
                low_evidence_mode=True,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        # decision == "broaden" — re-survey it (the interrupt latch above
        # ensures the loop's next pass can never ask again, however that
        # re-survey comes back).
        assert chosen_category is not None  # only remaining branch
        if original_product_type is None:
            original_product_type = current_type
        current_type = chosen_category.name


# ---------------------------------------------------------------------------
# SdkSurveyor — real Surveyor. Not unit tested here (no ANTHROPIC_API_KEY in
# this suite, consistent with SdkDiscoverer/SdkExtractor).
# ---------------------------------------------------------------------------

_UNPARSED = object()


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_survey_json(text: str) -> dict | None:
    """Extract a JSON object from the model's final message. Mirrors
    `discovery.py`'s `_parse_candidate_list`: try the whole message as JSON
    first, then fall back to the outermost `{`...`}` span (tolerates a
    markdown fence or surrounding prose). Unlike that function, this one
    returns `None` on failure rather than degrading to an empty value —
    `SdkSurveyor.survey` turns that into a `RuntimeError`, since there's no
    sensible empty-`SurveyReport` the rest of a run could continue with."""
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


def _flatten_strings(value: object) -> list[str]:
    """Pull every string leaf out of an arbitrarily-nested dict/list —
    used to turn a `ToolResultBlock`/`ServerToolResultBlock.content` (an
    opaque, SDK-version-dependent shape for WebSearch/WebFetch results)
    into evidence text for `_verify_exemplars`, without depending on that
    shape being stable. Deliberately tolerant, matching how every other
    real (untested) adapter in this codebase treats content it can't pin
    down precisely."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(_flatten_strings(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flatten_strings(v))
        return out
    return []


class SdkSurveyor:
    """Real `Surveyor` — calls Haiku directly via the SDK, no dispatched
    subagent (see module docstring point 2). Not unit tested (see above)."""

    def __init__(self, model: str = config.MODEL_HAIKU) -> None:
        self._model = model

    async def survey(
        self, product_type: str, location: Location, ledger: FetchLedger
    ) -> RawSurvey:
        # §3: fail loudly before spending anything if the research
        # protocol skill isn't there to be loaded.
        assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)

        prompt = SURVEY_PROMPT_TEMPLATE.format(
            product_type=product_type,
            country=location.country,
            max_searches=config.MAX_SURVEY_SEARCHES,
            schema=json.dumps(SurveyReport.model_json_schema()),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=["WebSearch", "WebFetch"],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            hooks={"PostToolUse": ledger_hook_matchers(ledger)},  # §4.3
            skills=[config.RESEARCH_PROTOCOL_SKILL],
        )

        final_text = ""
        evidence_pool: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if not isinstance(message, (AssistantMessage, UserMessage)):
                continue
            content = message.content
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, TextBlock):
                    final_text = block.text  # keep overwriting; last wins
                elif isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
                    evidence_pool.extend(_flatten_strings(block.content))

        parsed = _parse_survey_json(final_text)
        if parsed is None:
            raise RuntimeError(
                "scout-surveyor's final message did not contain a "
                f"parseable SurveyReport JSON object: {final_text!r}"
            )

        parsed, repair_caveats = _repair_comparison_specs(parsed)
        report = SurveyReport(**parsed)
        return RawSurvey(report=report, evidence_pool=evidence_pool, caveats=repair_caveats)
