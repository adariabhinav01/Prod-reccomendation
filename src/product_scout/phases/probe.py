"""Phase 1 — PROBE + the sparse-category coverage gate (spec
docs/handoff.md §8.1 / §8.1a, build order step 4).

"Cheap, and it shapes every downstream phase" (build order step 4). This
module owns two things:

1. `CoverageProber` — the seam through which Phase 1's actual coverage
   check happens (see SPEC GAP-FILL below).
2. `run_probe` — the deterministic gate/interrupt/latch state machine
   described in §8.1a, built on top of that seam. This is what step 4 is
   actually testing: given a `CoverageReport`, decide whether to proceed,
   interrupt, or silently degrade — and never interrupt the user twice.

### SPEC GAP-FILL — the `CoverageProber` seam

§3 wires a `scout-prober` Haiku agent (WebSearch only) into the SDK's
`ClaudeAgentOptions`, and §8.1 describes what it must do ("cheap coverage
check on Haiku... cap at ~4 searches... no fetching") and what it returns
(`CoverageReport`, §4). But no orchestrator/SDK-client scaffolding exists
yet in this repo — that lands as agent-backed phases get built (step 5
onward). Mirroring `io/port.py`'s `QuestionPort` seam — the pattern this
codebase already uses to keep interaction logic swappable and testable
without a live backend — Phase 1's "go run the actual probe" step is
abstracted behind `CoverageProber`. The real implementation (calling the
`scout-prober` agent, capping it at ~4 searches, and parsing its answer
into a `CoverageReport`) plugs in later without touching this module's gate
logic, which is exactly the point: the interrupt/latch state machine below
needs to be unit-testable now, against a scripted fake, with no network
call or API key involved.

### SPEC GAP-FILL — interrupt option count vs. "present all of them"

§8.1a says to present *all* `suggested_broader_categories` in one
interrupt. But the interrupt is asked through `QuestionPort.ask()`, whose
2–4-option contract is a hard invariant elsewhere in this codebase (§3's
`ask_user` tool schema; enforced in `CLIQuestionPort.ask`) — and the two
fixed choices ("keep original scope" / "stop here") already spend 2 of that
budget, so more than 2 broader categories cannot literally fit alongside
them. This also matches the worked example in §8.1a itself, which shows
exactly 2 suggested categories. `_choose_categories_for_interrupt` below
caps at `MAX_BROADER_CATEGORIES_IN_INTERRUPT`, preferring the best-covered
suggestions (`rich` > `moderate` > `sparse` > `barren`, ties broken by the
prober's original order), and logs any dropped suggestions to `caveats` so
nothing goes missing silently — "degrade explicitly, never silently" (§8).

### SPEC GAP-FILL — `original_product_type` when the user keeps scope

§8.1a's prose is ambiguous here: "If the user keeps their original scope,
record `original_product_type` and continue...". But §4's field comment on
`RunRecord.original_product_type` is unambiguous — "set if the user
broadened the category." This module follows the field comment:
`original_product_type` stays `None` unless the researched category
actually changes, and is set to whatever the user *first* typed the moment
it does change. It is never set to an intermediate sparse category the user
declined to broaden into.

### SPEC GAP-FILL — `moderate` coverage does not set run-level low-evidence mode

§8.1's table gives `moderate` a per-product qualifier: "enable low-evidence
mode for under-covered products only." That has no home at this gate —
neither `Product` nor `EvidenceProfile` (§4) carries a per-product
low-evidence flag, and `RunRecord.low_evidence_mode` is a single run-wide
bool. Applying it requires knowing which *specific* products are
under-covered, which isn't known until Extraction (step 5+) runs. `run_probe`
therefore treats `moderate` identically to `rich` at the run level — no
interrupt, `low_evidence_mode=False` on the `ProbeOutcome` — and leaves the
per-product nuance for whichever later phase can actually see individual
products' evidence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from product_scout.io.port import QuestionPort
from product_scout.models import BroaderCategory, CoverageReport

# §8.1a's worked example shows 2 suggested categories; see the module
# SPEC GAP-FILL note for why this is also the hard ceiling.
MAX_BROADER_CATEGORIES_IN_INTERRUPT = 2

KEEP_SCOPE_OPTION = "Keep my original scope, proceed anyway (low-evidence mode)"
STOP_OPTION = "Stop here"

_COVERAGE_RANK = {"rich": 0, "moderate": 1, "sparse": 2, "barren": 3}
_THIN_COVERAGE_DESCRIPTION = {
    "sparse": "thin independent review coverage",  # §8.1a's own example wording
    "barren": "effectively no independent coverage",  # §8.1's table wording, verbatim
}


@runtime_checkable
class CoverageProber(Protocol):
    """Seam for Phase 1's actual coverage check — see module SPEC GAP-FILL."""

    async def probe(self, product_type: str) -> CoverageReport: ...


class ProbeOutcome(BaseModel):
    """Phase 1's fully-resolved result — the §8.1a gate already applied.

    Maps directly onto the `RunRecord` fields it feeds (§4): `product_type`
    is the category every later phase should research (possibly broadened),
    `original_product_type` / `category_broadening_offered` / `caveats`
    copy straight across, and `low_evidence_mode` gates §8.2. `coverage` is
    the report for whichever category was ultimately settled on — not
    necessarily the first one probed.

    `proceed=False` means the user picked "Stop here" — the orchestrator
    (not yet built) must halt the run without spending anything on
    Discovery/Extraction, which is the entire point of probing first (§8.1a:
    "a full research run on a category with nothing to find is the most
    expensive way to learn the category has nothing to find").
    """

    coverage: CoverageReport
    proceed: bool
    low_evidence_mode: bool
    product_type: str
    original_product_type: str | None
    category_broadening_offered: bool
    caveats: list[str] = []


def _choose_categories_for_interrupt(
    categories: list[BroaderCategory],
) -> tuple[list[BroaderCategory], list[BroaderCategory]]:
    """Pick up to `MAX_BROADER_CATEGORIES_IN_INTERRUPT`, best-covered first.

    Dedupes by name (case-insensitive, first occurrence wins) — two
    identically-named options would be indistinguishable to
    `QuestionPort`'s text-matching (`io/cli_port.py`'s case-insensitive
    fallback). Returns `(chosen, dropped)` so the caller can log what didn't
    fit — see the module SPEC GAP-FILL note.
    """
    seen: set[str] = set()
    deduped: list[BroaderCategory] = []
    for cat in categories:
        key = cat.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cat)

    ranked = sorted(
        enumerate(deduped),
        key=lambda pair: (_COVERAGE_RANK[pair[1].estimated_coverage], pair[0]),
    )
    ordered = [cat for _, cat in ranked]
    limit = MAX_BROADER_CATEGORIES_IN_INTERRUPT
    return ordered[:limit], ordered[limit:]


def _build_interrupt_options(
    chosen: list[BroaderCategory],
) -> tuple[list[str], dict[str, BroaderCategory | None]]:
    """Build the option list and a text -> BroaderCategory|None lookup.

    `None` in the lookup marks the two fixed choices (keep scope / stop),
    which don't correspond to a category to re-probe.
    """
    options: list[str] = []
    choice_map: dict[str, BroaderCategory | None] = {}
    for cat in chosen:
        # The coverage-tier parenthetical mirrors §8.1a's own worked
        # example ("...instead (moderate coverage)") — surfaced to the
        # user, not just used internally for the ranking in
        # _choose_categories_for_interrupt.
        label = f'Research "{cat.name}" instead ({cat.estimated_coverage} coverage)'
        options.append(label)
        choice_map[label] = cat
    options.append(KEEP_SCOPE_OPTION)
    choice_map[KEEP_SCOPE_OPTION] = None
    options.append(STOP_OPTION)
    choice_map[STOP_OPTION] = None
    return options, choice_map


def _format_interrupt_question(product_type: str, coverage: CoverageReport) -> str:
    testing_note = (
        "methodology-backed testing exists"
        if coverage.has_methodology_backed_testing
        else "no methodology-backed testing"
    )
    return (
        f'Coverage check: "{product_type}" has '
        f"{_THIN_COVERAGE_DESCRIPTION[coverage.coverage]}.\n"
        f"  Found: {coverage.estimated_product_count} product(s), "
        f"{coverage.independent_review_sources_found} independent review "
        f"source(s), {testing_note}."
    )


async def run_probe(
    product_type: str,
    prober: CoverageProber,
    port: QuestionPort,
) -> ProbeOutcome:
    """Run Phase 1 end to end: probe, gate, interrupt at most once (§8.1a).

    A `rich`/`moderate` result proceeds immediately, no interrupt, no
    low-evidence mode (§8.1's table). A `sparse`/`barren` result halts
    *before* Discovery/Extraction spend anything and interrupts the user —
    but only the first time this fires in the run. If the user broadens
    into another category, that category is re-probed (cheap) but the
    interrupt is never shown a second time (§8.1a): a further sparse/barren
    result just proceeds in low-evidence mode with a caveat logged, exactly
    like the "keep original scope" answer would have.

    This loop runs at most twice: the initial probe, and — only if the user
    chose to broaden — one re-probe of the broadened category, which always
    returns (either it clears the bar, or the latch is now set and it
    degrades automatically). No unbounded recursion is possible.
    """
    original_product_type: str | None = None
    category_broadening_offered = False
    current_type = product_type
    caveats: list[str] = []

    while True:
        coverage = await prober.probe(current_type)

        if coverage.coverage in ("rich", "moderate"):
            return ProbeOutcome(
                coverage=coverage,
                proceed=True,
                low_evidence_mode=False,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=category_broadening_offered,
                caveats=caveats,
            )

        # coverage.coverage is "sparse" or "barren" from here on.

        if category_broadening_offered:
            # The edge case (§8.1a): the user already answered the
            # broadening question once this run — broadening into ANOTHER
            # sparse/barren category must not re-interrupt. Proceed in
            # low-evidence mode instead, same as if they'd chosen to keep
            # their original scope.
            caveats.append(
                f'Broadened category "{current_type}" also probed as '
                f"{coverage.coverage} ({coverage.notes}); proceeding in "
                "low-evidence mode without asking again — the broadening "
                "question was already answered once this run (§8.1a)."
            )
            return ProbeOutcome(
                coverage=coverage,
                proceed=True,
                low_evidence_mode=True,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        # First sparse/barren hit this run — the one interrupt (§8.1a).
        chosen, dropped = _choose_categories_for_interrupt(
            coverage.suggested_broader_categories
        )
        if dropped:
            names = ", ".join(
                f'"{c.name}" ({c.estimated_coverage})' for c in dropped
            )
            caveats.append(
                "Coverage probe suggested more broader categories than "
                f"the interrupt can show at once ({MAX_BROADER_CATEGORIES_IN_INTERRUPT} "
                f"max); not offered: {names}."
            )

        options, choice_map = _build_interrupt_options(chosen)
        question = _format_interrupt_question(current_type, coverage)
        answer = await port.ask(question, options, escape_hatch="none")
        # The latch trips the moment the interrupt is shown, regardless of
        # what the user picks — "a user who has already declined to
        # broaden once has answered the question" (§8.1a).
        category_broadening_offered = True

        if answer == STOP_OPTION:
            return ProbeOutcome(
                coverage=coverage,
                proceed=False,
                low_evidence_mode=False,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        if answer == KEEP_SCOPE_OPTION:
            caveats.append(
                f'Coverage for "{current_type}" is {coverage.coverage} '
                f"({coverage.notes}); user kept the original scope and "
                "proceeded in low-evidence mode."
            )
            return ProbeOutcome(
                coverage=coverage,
                proceed=True,
                low_evidence_mode=True,
                product_type=current_type,
                original_product_type=original_product_type,
                category_broadening_offered=True,
                caveats=caveats,
            )

        # A broader category was chosen — re-probe it (cheap), but the
        # interrupt latch above ensures the loop's next pass can never ask
        # again, however that re-probe comes back.
        chosen_category = choice_map[answer]
        assert chosen_category is not None  # only remaining branch
        if original_product_type is None:
            original_product_type = current_type
        current_type = chosen_category.name
