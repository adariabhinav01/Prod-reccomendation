"""The orchestrator (spec docs/handoff.md §1/§3/§13/§13.1, build order step
13).

CLAUDE.md's own phase-sequence table: "Fixed. Decided at design time,
encoded in `orchestrator.py` as one `query()` per phase. Nothing dispatches
subagents." This module is that encoding. It didn't exist before this build
step because nothing before it needed cross-phase, run-scoped state — every
phase module (`intake.py` … `synthesis.py`) was deliberately built as an
independently-callable `run_<phase>()` + `Protocol` seam + `Sdk<Phase>`
adapter, unit-tested against fakes, with no code sequencing them together
(see e.g. `hooks/ledger.py`'s, `store/checkpoint.py`'s, and
`phases/extraction.py`'s own docstrings, all of which point here by name).
§13 is the first thing that genuinely needs one: a `RunBudget` shared across
every phase's `query()`, or the cost cap resets per phase (§13's own
warning, quoted in `hooks/budget.py`).

### What this module does

`run_pipeline()` sequences all 9 phases in the fixed §1 order (intake,
survey, refine, extraction, timing, prior_gen, scoring, synthesis, render),
constructing exactly ONE `FetchLedger` and ONE `RunBudget` for the run's
lifetime and threading both into every research phase — never a fresh
instance per phase, which is the concrete fix for the "7N not N" failure
`hooks/budget.py`'s module docstring quotes §13 on. Every phase's own
`Sdk<Phase>` adapter is used by default; each seam accepts an injected fake
instead, mirroring every phase module's own test-double pattern, so this
module is unit-testable the same way they are (`tests/test_orchestrator.py`)
without any live SDK call.

### §13.1 termination — skip ahead of time, not fight the hook mid-call

"The orchestrator stops issuing new research calls, completes what it can
from what it has, and ships a partial run." Concretely: before issuing a
RESEARCH phase's `query()` (survey, extraction, timing, prior_gen — the
only phases that ever call `WebFetch`/`WebSearch`), `run_pipeline` checks
`budget.tripped`; if already tripped, it skips calling that phase entirely
rather than issuing a `query()` every one of whose tool calls the hook would
just deny — that would burn a whole extra round-trip for nothing. Every
OTHER phase (intake, refine, scoring, synthesis, render) always runs on
whatever inputs resulted; `run_scoring`/`run_synthesis` already handle an
empty product list correctly (`SynthesisOutcome` short-circuits to a
code-computed `INSUFFICIENT_EVIDENCE` verdict), so this module invents no
placeholder `verdict`/`scores`/`products`. The one field genuinely needing
an orchestrator-built placeholder when a phase is skipped mid-run is a
skipped TIMING's `TimingAssessment` — see `_SKIPPED_TIMING` below.

`truncated_at_phase` is set exactly once, to the index (into
`store.checkpoint.PHASE_NAMES`) of the first research phase during or
before which `budget.tripped` was observed — never overwritten after that.
See `_mark_truncated`.

### A structurally distinct early exit: the user declining to proceed (§8.2)

`SurveyOutcome.proceed=False` ("the user picked 'Stop here'") is NOT a §13.1
cost-cap event — it's a user choice, made before REFINE/EXTRACTION spend
anything, unrelated to the budget. `run_pipeline` returns `None` in this
case (no `RunRecord` to save — there is nothing to recommend), and never
sets `truncated_at_phase`, which is specifically cost-cap-termination
semantics.

### §3.3's startup assertion — genuinely new now, not a duplicate

`_REQUIRED_SKILLS` and the loop at the top of `run_pipeline` are what §3.3
means by "assert at startup" — checked once, before INTAKE's first
question, across every skill any phase in the run could need. This is
DIFFERENT from (and not made redundant by) each `Sdk<Phase>` adapter's own
`assert_skill_loaded` call immediately before it spends anything: those
remain the correct defensive check for any caller that reaches a phase
without going through `run_pipeline` at all (every phase's own test suite
does exactly that). Only this module can assert the whole run's precondition
before the whole run spends anything.

### Two pieces of "connect the phases" logic that had nowhere to live before

Both are explicitly flagged, in the phase modules that need them, as work
with "no real caller (`orchestrator.py`) exists yet":

1. **Extraction candidates.** Nothing built the shortlist EXTRACTION
   researches. `_extraction_candidates` below unions
   `IntakeAnswers.candidates_under_consideration` (the user's own named
   picks) with the `exemplar_products` of every `Cluster` REFINE's
   interview left in `RefineOutcome.surviving_clusters` — i.e. only
   clusters the interview didn't rule out contribute candidates, which is
   the entire point of running REFINE before EXTRACTION (§1). Capped at
   `ROW_CAP` (§5.1's own row ceiling — EXTRACTION never needs to research
   more candidates than SCORING could ever keep) via round-robin across
   surviving clusters rather than truncation in cluster order: a category
   that survives REFINE with many clusters (standing desks, live-verified,
   surveyed into 6-7) would otherwise let EXTRACTION's per-candidate fetch
   allowance burn the whole run's shared budget before TIMING/PRIOR-GEN/
   SCORING/SYNTHESIS ever ran. Round-robin means every surviving cluster
   contributes at least one candidate before any cluster contributes a
   second, so the cap can't silently starve clusters REFINE's interview
   chose to keep. Emits a caveat, never silent, when the cap actually
   drops an exemplar.
2. **`intake.filter_by_required_features`.** `phases/extraction.py`'s own
   docstring names this as unwired cross-cutting logic "with a home later."
   Applied immediately after EXTRACTION returns, before anything else
   (TIMING's grounding names, PRIOR-GEN's seeds) sees the product list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from product_scout import config
from product_scout.degraded_modes import is_commodity_category, unresearched_cluster_labels
from product_scout.hooks.budget import RunBudget
from product_scout.hooks.ledger import FetchLedger
from product_scout.io.port import QuestionPort
from product_scout.models import Caveat, IntakeAnswers, Location, Product, RunRecord, TimingAssessment
from product_scout.phases.extraction import Extractor, SdkExtractor, run_extraction
from product_scout.phases.intake import filter_by_required_features, run_intake
from product_scout.phases.prior_gen import (
    PriorGenOutcome,
    PriorGenResearcher,
    SdkPriorGenResearcher,
    run_prior_gen,
)
from product_scout.phases.refine import RefineOutcome, Refiner, SdkRefiner, run_refine
from product_scout.phases.scoring import ROW_CAP, ScoringOutcome, Scorer, SdkScorer, run_scoring
from product_scout.phases.survey import SurveyOutcome, Surveyor, SdkSurveyor, run_survey
from product_scout.phases.synthesis import SdkSynthesizer, SynthesisOutcome, Synthesizer, run_synthesis
from product_scout.phases.timing import (
    SdkTimingResearcher,
    TimingOutcome,
    TimingResearcher,
    run_timing,
)
from product_scout.skills import assert_skill_loaded, hash_required_skills
from product_scout.store import checkpoint as checkpoint_module
from product_scout.store.checkpoint import PHASE_NAMES
from product_scout.store.runs import RunStore, generate_run_id

# §3.3: "Assert at startup that .claude/skills/ actually loaded." Every
# skill any phase in a full run could read, gathered from config.py's own
# names — checked ONCE, here, before INTAKE's first question, rather than
# only at each phase's own point of use (every Sdk<Phase> adapter already
# asserts its own skill defensively too — that stays, as a safety net for
# any caller that reaches a phase without going through this orchestrator
# at all, e.g. a unit test). Before this module existed there was no
# "startup" of a whole RUN to assert anything at; every phase's own
# assertion WAS the earliest possible check, for that phase in isolation.
# Now that run_pipeline is the actual entry point, deferring to each
# phase's own check would mean a run could spend real money on SURVEY,
# EXTRACTION, TIMING, and PRIOR-GEN before discovering at SCORING that
# recommendation-logic never loaded — exactly the "plausible-looking
# garbage rather than an error" failure CLAUDE.md's notes section warns
# against, just delayed rather than prevented.
_REQUIRED_SKILLS = (
    config.RESEARCH_PROTOCOL_SKILL,
    config.QUESTION_DESIGN_SKILL,
    config.RECOMMENDATION_LOGIC_SKILL,
    config.MARKET_TIMING_SKILL,
)

# §6.5's schema-legitimate "no signal" value — used when TIMING itself is
# skipped by the cost cap (§13.1), never when TIMING ran and genuinely
# found nothing (that's `_enforce_basis_discipline`'s own downgrade path,
# inside phases/timing.py, unrelated to this).
_SKIPPED_TIMING = TimingAssessment(signal_found=False, recommends_wait=False)


def _mark_truncated(current: int | None, budget: RunBudget, phase_name: str) -> int | None:
    """§13.1: set once, on first observation of `budget.tripped`, never
    overwritten after. A no-op once `current` is already set — by the time
    a LATER phase is skipped because `budget.tripped` was already true
    going in, the phase during which it actually tripped already claimed
    the index."""
    if current is not None:
        return current
    if budget.tripped:
        return PHASE_NAMES.index(phase_name)
    return None


def _load_if_resuming(run_store: RunStore, run_id: str, resume: bool, phase: str) -> dict | None:
    """§16.1: `None` when not resuming, or when this phase hasn't
    checkpointed yet — both mean "compute it fresh," the exact behavior
    every phase already had before resume existed. Never confuse "not
    resuming" with "phase 1 of 9" — a fresh (non-resumed) run always takes
    this branch for every phase, so `resume=False` reproduces today's
    behavior byte-for-byte."""
    if not resume:
        return None
    return run_store.load_checkpoint(run_id, phase)


def _extraction_candidates(survey, refine_outcome, intake) -> tuple[list[str], str | None]:
    """See module docstring point 1. Order-preserving, deduped (first
    occurrence wins) — a user-named candidate that also happens to be a
    surviving cluster's exemplar is researched once, not twice. Capped at
    `ROW_CAP`: user-named candidates are kept unconditionally, then one
    exemplar at a time is round-robined off each surviving cluster's queue
    until either every queue is empty or the cap is reached — so a single
    exemplar-heavy cluster can't exhaust the cap and starve the rest.
    Returns `(candidates, caveat)`; `caveat` is `None` unless the cap
    actually dropped an exemplar that would otherwise have been
    researched."""
    kept = list(dict.fromkeys(intake.candidates_under_consideration))
    queues = [
        list(cluster.exemplar_products)
        for cluster in survey.clusters
        if cluster.key in refine_outcome.surviving_clusters
    ]
    dropped = 0
    while any(queues):
        progressed = False
        for queue in queues:
            if not queue:
                continue
            progressed = True
            name = queue.pop(0)
            if name in kept:
                continue  # dedup — not a drop, just already present
            if len(kept) < ROW_CAP:
                kept.append(name)
            else:
                dropped += 1
        if not progressed:
            break
    caveat = None
    if dropped:
        caveat = (
            f"EXTRACTION candidate list capped at {ROW_CAP} (§5.1's row ceiling) — "
            f"{dropped} additional exemplar(s) from surviving clusters were not researched."
        )
    return kept, caveat


def _looks_like_a_product_name(text: str, product_names: set[str]) -> str | None:
    for name in product_names:
        if name and name in text:
            return name
    return None


def _normalize_caveat_template(text: str, product_names: set[str]) -> str:
    """Collapsing key for §5.5 rule 3 ('one line per class, not per
    instance where instances share a cause'): replace any product-name
    substring with a placeholder so structurally-identical caveats about
    different products collapse into one class-level line."""
    normalized = text
    for name in product_names:
        if name:
            normalized = normalized.replace(name, "{product}")
    return normalized


def merge_caveats(raw_caveats: list[str], products: list[Product]) -> list[Caveat]:
    """Wraps every phase's raw `list[str]` caveats into typed, tiered
    `Caveat` objects — explicitly documented in multiple phase modules
    (e.g. `phases/refine.py`'s `RefineOutcome` docstring) as "the
    (not-yet-built) orchestrator's job." A pragmatic heuristic over free
    text, not a rederivation of anything settled elsewhere: a caveat
    mentioning a known product name verbatim is decision-affecting and
    anchored to it (§5.5 rule 1 — render inline, never collapsed); every
    other caveat is provenance-tier and collapses with structurally
    identical ones, keeping the first occurrence's exact wording and
    counting instances (§5.5 rule 3). If this proves lossy against the
    §17.1 golden set, the real fix is having each phase emit
    `(text, anchor)` tuples instead of bare strings — a larger, separate
    change, not attempted here.
    """
    product_names = {p.name for p in products if p.name}
    decision_affecting: list[Caveat] = []
    provenance_groups: dict[str, list[str]] = {}

    for text in raw_caveats:
        anchor = _looks_like_a_product_name(text, product_names)
        if anchor is not None:
            decision_affecting.append(Caveat(tier="decision_affecting", text=text, anchor=anchor))
            continue
        template = _normalize_caveat_template(text, product_names)
        provenance_groups.setdefault(template, []).append(text)

    provenance = [
        Caveat(tier="provenance", text=group[0], anchor=None, instance_count=len(group))
        for group in provenance_groups.values()
    ]
    return decision_affecting + provenance


def _truncation_caveat(phase_index: int, survey, products: list[Product]) -> Caveat:
    """§13.1: 'a banner stating research was truncated and where, every
    unresearched cluster named.' The banner itself lives in
    `render/report.py`'s `_render_truncation_banner` (reading
    `RunRecord.truncated_at_phase` directly); this is the matching
    decision-affecting `Caveat`, feeding the standard notes/caveat pipeline
    (§5.5 lists 'truncation' as one of its own caveat generator classes)
    so the truncation is discoverable there too, not only in the banner.
    Always `tier='decision_affecting'` — unlike `merge_caveats`'s
    heuristic, this one is never in doubt."""
    phase_name = PHASE_NAMES[phase_index]
    unresearched = unresearched_cluster_labels(survey, products)
    detail = (
        f"Unresearched clusters: {', '.join(unresearched)}."
        if unresearched
        else "Every surveyed cluster has at least one researched product."
    )
    text = (
        f"Research was truncated during the {phase_name} phase — this run's "
        f"§13 cost cap was reached, so later research calls were skipped. {detail}"
    )
    return Caveat(tier="decision_affecting", text=text, anchor=None)


async def run_pipeline(
    product_type: str,
    port: QuestionPort,
    run_store: RunStore,
    *,
    settings_path: Path | str | None = None,
    resume_run_id: str | None = None,
    surveyor: Surveyor | None = None,
    refiner: Refiner | None = None,
    extractor: Extractor | None = None,
    timing_researcher: TimingResearcher | None = None,
    prior_gen_researcher: PriorGenResearcher | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
) -> RunRecord | None:
    """Run every phase, in the fixed §1 order, and return the assembled
    `RunRecord` — or `None` if the user declined to proceed at SURVEY's
    §8.2 interrupt (see module docstring; not a §13.1 truncation).

    Every `*_researcher`/`surveyor`/`refiner`/`extractor`/`scorer`/
    `synthesizer` argument defaults to the real `Sdk<Phase>` adapter;
    `tests/test_orchestrator.py` injects fakes, mirroring every phase
    module's own test-double pattern.

    `resume_run_id` (§16.1, build order step 15) picks up a partially
    completed run: each phase below checks its own checkpoint first
    (`_load_if_resuming`) and reconstructs that phase's outcome instead of
    computing it fresh — skipping the live call, the checkpoint write, AND
    the port interaction for INTAKE/SURVEY/REFINE. `resume_run_id=None`
    (the default) makes every one of those checks a no-op, so a normal run
    behaves exactly as before this parameter existed. `product_type` is
    unused when resuming past INTAKE's own checkpoint — it's only needed
    if INTAKE itself never checkpointed, which `run_id`'s own up-front
    validation below refuses rather than silently re-asking INTAKE with
    whatever (possibly empty) string the caller passed.
    """
    for skill in _REQUIRED_SKILLS:
        assert_skill_loaded(skill)

    surveyor = surveyor or SdkSurveyor()
    refiner = refiner or SdkRefiner()
    extractor = extractor or SdkExtractor()
    timing_researcher = timing_researcher or SdkTimingResearcher()
    prior_gen_researcher = prior_gen_researcher or SdkPriorGenResearcher()
    scorer = scorer or SdkScorer()
    synthesizer = synthesizer or SdkSynthesizer()

    resume = resume_run_id is not None
    if resume:
        run_id = resume_run_id
        done = checkpoint_module.completed_phases(run_store.run_dir(run_id))
        if not done:
            raise FileNotFoundError(
                f"No checkpoints found for run_id={run_id!r} — nothing to resume. "
                "Start a fresh run instead."
            )
        if "render" in done:
            # Already fully completed and saved — resuming it is a no-op.
            return run_store.load(run_id)
    else:
        run_id = generate_run_id()

    ledger = FetchLedger()
    budget = RunBudget(max_fetches=config.MAX_RUN_FETCHES, max_searches=config.MAX_RUN_SEARCHES)
    truncated_at_phase: int | None = None
    raw_caveats: list[str] = []

    # -- 0. INTAKE (Python, no model call) -----------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "intake")
    if cached is not None:
        intake = IntakeAnswers.model_validate(cached["intake"])
        location = Location.model_validate(cached["location"])
        units = cached["units"]
    else:
        await port.report_progress("INTAKE")
        intake, location, units = await run_intake(product_type, port, settings_path=settings_path)
        run_store.save_checkpoint(
            run_id,
            "intake",
            {
                "intake": intake.model_dump(mode="json"),
                "location": location.model_dump(mode="json"),
                "units": units,
            },
        )

    # -- 1. SURVEY (Haiku) ----------------------------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "survey")
    if cached is not None:
        survey_outcome = SurveyOutcome.model_validate(cached)
    else:
        await port.report_progress("SURVEY")
        survey_outcome = await run_survey(product_type, location, surveyor, port, ledger, budget)
        run_store.save_checkpoint(run_id, "survey", survey_outcome.model_dump(mode="json"))
        truncated_at_phase = _mark_truncated(truncated_at_phase, budget, "survey")
    raw_caveats.extend(survey_outcome.caveats)

    if not survey_outcome.proceed:
        # §8.2 user-declined stop — not a cost-cap event. Nothing to
        # recommend; no RunRecord to save.
        return None

    survey = survey_outcome.survey

    # -- 2. REFINE (Opus) — always runs; no tool calls, doesn't touch budget --
    cached = _load_if_resuming(run_store, run_id, resume, "refine")
    if cached is not None:
        refine_outcome = RefineOutcome.model_validate(cached)
    else:
        await port.report_progress("REFINE")
        refine_outcome = await run_refine(survey, intake, refiner, port)
        run_store.save_checkpoint(run_id, "refine", refine_outcome.model_dump(mode="json"))
    raw_caveats.extend(refine_outcome.caveats)

    # -- 3. EXTRACTION (Haiku) -------------------------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "extraction")
    if cached is not None:
        products = [Product.model_validate(p) for p in cached["products"]]
    else:
        await port.report_progress("EXTRACTION")
        if budget.tripped:
            products = []
            raw_caveats.append(
                "EXTRACTION skipped — this run's §13 cost cap was already reached "
                "before this phase could run; no products were researched."
            )
        else:
            candidates, cap_caveat = _extraction_candidates(survey, refine_outcome, intake)
            if cap_caveat is not None:
                raw_caveats.append(cap_caveat)
            products = await run_extraction(
                survey_outcome.product_type, candidates, survey, ledger, location,
                survey_outcome.low_evidence_mode, budget, extractor,
            )
            products = filter_by_required_features(products, intake.required_features)
        extraction_checkpoint = {"products": [p.model_dump(mode="json") for p in products]}
        run_store.save_checkpoint(run_id, "extraction", extraction_checkpoint)
        truncated_at_phase = _mark_truncated(truncated_at_phase, budget, "extraction")

    # -- 4. TIMING (Haiku) ------------------------------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "timing")
    if cached is not None:
        timing_outcome = TimingOutcome.model_validate(cached)
    else:
        await port.report_progress("TIMING")
        if budget.tripped:
            timing_outcome = TimingOutcome(
                timing=_SKIPPED_TIMING,
                caveats=[
                    "TIMING skipped — this run's §13 cost cap was already reached "
                    "before this phase could run."
                ],
            )
        else:
            timing_outcome = await run_timing(
                survey_outcome.product_type, [p.name for p in products], ledger,
                survey_outcome.low_evidence_mode, budget, timing_researcher,
            )
        run_store.save_checkpoint(run_id, "timing", timing_outcome.model_dump(mode="json"))
        truncated_at_phase = _mark_truncated(truncated_at_phase, budget, "timing")
    raw_caveats.extend(timing_outcome.caveats)

    # -- 5. PRIOR-GEN (Haiku) -----------------------------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "prior_gen")
    if cached is not None:
        prior_gen_outcome = PriorGenOutcome.model_validate(cached)
    else:
        await port.report_progress("PRIOR-GEN")
        if budget.tripped:
            prior_gen_outcome = PriorGenOutcome(
                products=[],
                caveats=[
                    "PRIOR-GEN skipped — this run's §13 cost cap was already "
                    "reached before this phase could run."
                ],
            )
        else:
            prior_gen_outcome = await run_prior_gen(
                products, survey, ledger, location, survey_outcome.low_evidence_mode,
                budget, prior_gen_researcher,
            )
        run_store.save_checkpoint(run_id, "prior_gen", prior_gen_outcome.model_dump(mode="json"))
        truncated_at_phase = _mark_truncated(truncated_at_phase, budget, "prior_gen")
    raw_caveats.extend(prior_gen_outcome.caveats)

    products = [*products, *prior_gen_outcome.products]

    # -- 6a. SCORING (Opus) — always runs, on whatever `products` resulted ------
    cached = _load_if_resuming(run_store, run_id, resume, "scoring")
    if cached is not None:
        scoring_outcome = ScoringOutcome.model_validate(cached)
    else:
        await port.report_progress("SCORING")
        scoring_outcome = await run_scoring(
            products, survey, intake, refine_outcome.topics, survey_outcome.low_evidence_mode, scorer
        )
        run_store.save_checkpoint(run_id, "scoring", scoring_outcome.model_dump(mode="json"))
    raw_caveats.extend(scoring_outcome.caveats)

    # -- 6b. SYNTHESIS (Opus) — always runs ------------------------------------
    cached = _load_if_resuming(run_store, run_id, resume, "synthesis")
    if cached is not None:
        synthesis_outcome = SynthesisOutcome.model_validate(cached)
    else:
        await port.report_progress("SYNTHESIS")
        synthesis_outcome = await run_synthesis(
            scoring_outcome.products, scoring_outcome.scores, survey, intake,
            timing_outcome.timing, survey_outcome.low_evidence_mode, synthesizer,
        )
        run_store.save_checkpoint(run_id, "synthesis", synthesis_outcome.model_dump(mode="json"))
    raw_caveats.extend(synthesis_outcome.caveats)

    # -- assemble ---------------------------------------------------------------
    caveats = merge_caveats(raw_caveats, scoring_outcome.products)
    if truncated_at_phase is not None:
        caveats.append(_truncation_caveat(truncated_at_phase, survey, scoring_outcome.products))

    record = RunRecord(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        product_type=survey_outcome.product_type,
        original_product_type=survey_outcome.original_product_type,
        location=location,
        units=units,
        intake=intake,
        survey=survey,
        topics=refine_outcome.topics,
        low_evidence_mode=survey_outcome.low_evidence_mode,
        commodity_category=is_commodity_category(survey),
        category_broadening_offered=survey_outcome.category_broadening_offered,
        truncated_at_phase=truncated_at_phase,
        products=scoring_outcome.products,
        timing=timing_outcome.timing,
        verdict=synthesis_outcome.verdict,
        scores=synthesis_outcome.scores,
        caveats=caveats,
        model_ids={"haiku": config.MODEL_HAIKU, "opus": config.MODEL_OPUS},
        skill_hashes=hash_required_skills(_REQUIRED_SKILLS),
    )

    # -- 7. RENDER (Python, no model call) ---------------------------------------
    await port.report_progress("RENDER")
    run_store.save(record)
    run_store.save_report(record)
    run_store.save_checkpoint(run_id, "render", {"run_id": run_id})

    return record
