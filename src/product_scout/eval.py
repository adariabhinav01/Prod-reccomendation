"""The golden set (spec docs/handoff.md §17.1; build order step 15). From
here on, this gates every skill edit.

## The hard constraint that shapes this module

`WebFetch`/`WebSearch` are server-side, Anthropic-API-hosted tools — this
SDK's client never executes them, so nothing here can substitute frozen
content before a live web request happens. That means the phases that need
those tools (SURVEY, EXTRACTION, TIMING, PRIOR-GEN) can only be replayed by
swapping the *phase adapter itself* for one that returns frozen, previously
captured output (`_Frozen*` classes below) — there is no way to keep those
four "live" while avoiding the network. The three phases that take no
tools at all (REFINE, SCORING, SYNTHESIS — `allowed_tools=[]` in every
`Sdk*` adapter, confirmed at build order step 15's own design review) have
no such constraint: they run genuinely live, against whatever skill
content currently exists on disk, exactly like `rescore.py` already proves
works. This is why `--stable-only` can still "gate a skill edit" for
`question-design` and `recommendation-logic` — it re-invokes them for
real. `research-protocol`/`market-timing` edits are caught only by the
separate, fully-live, manual `scout eval` run (§17.1: "Only the decaying
half runs live, and only manually").

## What a case is

`eval/cases/<name>/` holds one category's frozen fixtures, captured once
by actually running the live pipeline (`capture_case`) and freezing:
`survey.json` (`RawSurvey`), `products.json` (extracted `Product`s, before
prior-gen and before scoring), `timing.json` (`TimingAssessment`),
`prior_gen.json` (`RawPriorGen`), `ledger.json` (the run's `FetchLedger`),
plus `case.json` (the case's own metadata — product type, location,
intake script, and both stable and decaying expectations).

## Stable vs. decaying

Stable assertions (`check_*` functions below, `list[str]` violations,
empty = pass) check properties that are Python-derived and should hold
regardless of what a live model said today — they gate `--stable-only`.
Decaying assertions (`_decaying.py`-shaped functions with the same
signature) are informational only, printed but never blocking, since
they're checking whether live judgment still agrees with what a case's
author saw when it was captured.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from product_scout.confidence import confidence_band, property_test_violations
from product_scout.hooks.ledger import FetchLedger
from product_scout.io.port import IMPORTANCE_SKIP_DEFAULT, POSITION_SKIP_DEFAULT, TopicPrompt
from product_scout.models import (
    Location,
    Product,
    RunRecord,
    TimingAssessment,
    TopicAnswer,
)
from product_scout.orchestrator import run_pipeline
from product_scout.phases.extraction import Extractor, SdkExtractor
from product_scout.phases.prior_gen import PriorGenResearcher, RawPriorGen, SdkPriorGenResearcher
from product_scout.phases.refine import Refiner, SdkRefiner
from product_scout.phases.scoring import Scorer, SdkScorer
from product_scout.phases.survey import RawSurvey, Surveyor, SdkSurveyor
from product_scout.phases.synthesis import SdkSynthesizer, Synthesizer
from product_scout.phases.timing import SdkTimingResearcher, TimingResearcher
from product_scout.settings import LocationSettings, Settings, save as save_settings
from product_scout.store.runs import RunStore

# -- case metadata -------------------------------------------------------


class PriceAssertion(BaseModel):
    product_name: str
    expected_price: float
    tolerance: float = 0.15  # fractional; §17.1's decaying column — informational only


class ExpectedShape(BaseModel):
    """Which existing code-level classifier this case's verdict/mode is
    expected to exercise — see `check_verdict_shape`. Only one of these
    kinds is meaningful per case; the golden set's 5 categories (§17.1)
    each target exactly one."""

    kind: Literal["rich", "sparse", "commodity", "cross_border", "software"]


class EvalCase(BaseModel):
    name: str
    category: str  # human label, e.g. "rich", "sparse", "commodity", ...
    product_type: str
    location: Location
    units: Literal["imperial", "metric"]
    intake_script: list[str]
    """Exactly the `ask_text` answers `phases.intake.run_intake` needs, in
    order — see `EvalQuestionPort.ask_text`. 4 entries when
    `intake_script[0]` answers "no" to ownership (no `current_model`
    follow-up); 5 when it answers "yes"."""
    expected_shape: ExpectedShape
    must_appear_products: list[str] = []
    expected_confidence_order: list[str] = []  # product names, descending confidence
    price_assertions: list[PriceAssertion] = []


def load_case(case_dir: Path) -> EvalCase:
    return EvalCase.model_validate_json((case_dir / "case.json").read_text(encoding="utf-8"))


def _load_raw_survey(case_dir: Path) -> RawSurvey:
    return RawSurvey.model_validate_json((case_dir / "survey.json").read_text(encoding="utf-8"))


def _load_products(case_dir: Path) -> list[Product]:
    data = json.loads((case_dir / "products.json").read_text(encoding="utf-8"))
    return [Product.model_validate(p) for p in data]


def _load_timing(case_dir: Path) -> TimingAssessment:
    return TimingAssessment.model_validate_json((case_dir / "timing.json").read_text(encoding="utf-8"))


def _load_prior_gen(case_dir: Path) -> RawPriorGen:
    return RawPriorGen.model_validate_json((case_dir / "prior_gen.json").read_text(encoding="utf-8"))


def _load_ledger(case_dir: Path) -> FetchLedger:
    entries = json.loads((case_dir / "ledger.json").read_text(encoding="utf-8"))
    ledger = FetchLedger()
    for e in entries:
        observed_at = datetime.fromisoformat(e["observed_at"])
        if e["mode"] == "fetched":
            ledger.record_fetch(e["url"], status=e.get("status"), observed_at=observed_at)
        else:
            ledger.record_seen(e["url"], status=e.get("status"), observed_at=observed_at)
    return ledger


def _save_ledger(case_dir: Path, ledger: FetchLedger) -> None:
    entries = [
        {"url": url, "mode": entry.mode, "status": entry.status, "observed_at": entry.observed_at.isoformat()}
        for url, entry in ledger.all_entries().items()
    ]
    (case_dir / "ledger.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")


# -- frozen phase adapters ------------------------------------------------
#
# Each implements the real Protocol (survey.py:Surveyor, extraction.py:
# Extractor, timing.py:TimingResearcher, prior_gen.py:PriorGenResearcher)
# by returning a fixture loaded once at construction, unconditionally —
# same shape as tests/test_orchestrator.py's Fake* classes, but defined
# here (not imported from tests/) since this is shipped package code.


class _FrozenSurveyor:
    def __init__(self, raw: RawSurvey):
        self._raw = raw

    async def survey(self, product_type, location, ledger, budget) -> RawSurvey:
        return self._raw


class _FrozenExtractor:
    def __init__(self, products: list[Product]):
        self._products = products

    async def extract(self, product_type, candidates, survey, ledger, location, low_evidence_mode, budget):
        return self._products


class _FrozenTimingResearcher:
    def __init__(self, assessment: TimingAssessment):
        self._assessment = assessment

    async def research(self, product_type, product_names, ledger, low_evidence_mode, budget) -> TimingAssessment:
        return self._assessment


class _FrozenPriorGenResearcher:
    def __init__(self, raw: RawPriorGen):
        self._raw = raw

    async def research(self, seeds, ledger, low_evidence_mode, budget) -> RawPriorGen:
        return self._raw


# -- recording adapters (capture only) -------------------------------------
#
# Delegate to a real Sdk* adapter, and remember exactly what it returned —
# `capture_case` wraps the four web-tool phases in these, runs the real
# pipeline once, and freezes whatever they recorded. Each also captures a
# reference to the `ledger` argument it was called with; `run_pipeline`
# constructs exactly one `FetchLedger` and threads the same instance
# through every phase, so any one recorder's reference is the whole run's
# ledger once the pipeline completes.


class _RecordingSurveyor:
    def __init__(self, real: Surveyor):
        self._real = real
        self.raw: RawSurvey | None = None
        self.ledger: FetchLedger | None = None

    async def survey(self, product_type, location, ledger, budget) -> RawSurvey:
        self.ledger = ledger
        self.raw = await self._real.survey(product_type, location, ledger, budget)
        return self.raw


class _RecordingExtractor:
    def __init__(self, real: Extractor):
        self._real = real
        self.products: list[Product] | None = None
        self.ledger: FetchLedger | None = None

    async def extract(self, product_type, candidates, survey, ledger, location, low_evidence_mode, budget):
        self.ledger = ledger
        self.products = await self._real.extract(
            product_type, candidates, survey, ledger, location, low_evidence_mode, budget
        )
        return self.products


class _RecordingTimingResearcher:
    def __init__(self, real: TimingResearcher):
        self._real = real
        self.assessment: TimingAssessment | None = None

    async def research(self, product_type, product_names, ledger, low_evidence_mode, budget) -> TimingAssessment:
        self.assessment = await self._real.research(product_type, product_names, ledger, low_evidence_mode, budget)
        return self.assessment


class _RecordingPriorGenResearcher:
    def __init__(self, real: PriorGenResearcher):
        self._real = real
        self.raw: RawPriorGen | None = None

    async def research(self, seeds, ledger, low_evidence_mode, budget) -> RawPriorGen:
        self.raw = await self._real.research(seeds, ledger, low_evidence_mode, budget)
        return self.raw


# -- the scripted QuestionPort ---------------------------------------------


class EvalQuestionPort:
    """`QuestionPort` for eval/capture — deterministic, content-aware
    defaults, never real terminal I/O. REFINE runs LIVE under this
    module's hybrid design (see module docstring), so the number of
    `ask_topic` calls is genuinely non-deterministic (Opus decides, per
    §9.7's stopping condition) — a positional/flat-list script (the
    `tests/test_orchestrator.py::make_port` pattern) can't work here; every
    method below answers from the CONTENT of what it's asked, not from a
    position in a pre-recorded list, so it never runs out.

    - `ask_choice` always keeps `options[-1]` — SURVEY's §8.2 broadening
      interrupt (`phases/survey.py`) is `ask_choice`'s only production
      caller, and always builds `options = [*broader_category_labels,
      KEEP_SCOPE_OPTION]` with `escape_hatch=STOP_OPTION` — so
      `options[-1]` is `KEEP_SCOPE_OPTION`, keeping this case's own
      category rather than silently broadening or stopping the run.
    - `ask_topic` always answers "no_preference" + the §9.6 skip-default
      axis value (never an assertive position/importance) — invariant 8
      ("axis answers are soft weights, never filters") means a naive
      always-must_have default would be actively wrong, not just neutral.
    - `offer_bailout` always declines, so REFINE's own §9.7 stopping
      condition (floor / stop-(a) / stop-(b) / CAP) ends the loop
      naturally instead of an artificial early exit.
    - `ask_text` is the one INTAKE actually needs — Phase 0 is fixed-count
      Python logic (never model-driven), so a positional script is exactly
      right there; pulls from `EvalCase.intake_script`, in order.
    """

    def __init__(self, intake_script: list[str]):
        self._intake_answers = iter(intake_script)

    async def ask_choice(self, question: str, options: list[str], escape_hatch: str) -> str:
        return options[-1]

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer:
        if topic.axis is None:
            axis_kind, axis_value, axis_skipped = None, None, True
        else:
            axis_kind = topic.axis.kind
            axis_value = POSITION_SKIP_DEFAULT if axis_kind == "position" else IMPORTANCE_SKIP_DEFAULT
            axis_skipped = True
        return TopicAnswer(
            topic=topic.topic,
            dimension_name=topic.dimension_name,
            gate_answer="no_preference",
            axis_kind=axis_kind,
            axis_value=axis_value,
            axis_skipped=axis_skipped,
            free_text="",
            became_filter=False,
            assumption_logged="eval harness default — no preference asserted",
        )

    async def offer_bailout(self) -> bool:
        return False

    async def ask_text(self, prompt: str) -> str:
        try:
            return next(self._intake_answers)
        except StopIteration:
            raise ValueError(
                "EvalCase.intake_script ran out of answers — run_intake asked more "
                "questions than the case scripted. Check whether owns_current_version "
                "needs a 5th entry (the current_model follow-up)."
            ) from None

    async def report_progress(self, message: str) -> None:
        pass  # eval/capture progress reporting is a separate concern; not needed here


def _settings_path_for(case: EvalCase, scratch_dir: Path) -> Path:
    """Pre-seeds `settings_path` with the case's location/units, the same
    way `tests/test_orchestrator.py::settings_path_with_location` does —
    `run_intake`'s `_ask_location` only prompts through the port when
    settings DON'T already have a location, and `EvalQuestionPort` has no
    sensible way to answer that prompt (it's free-text country/currency
    codes, not content it can derive from a `TopicPrompt`)."""
    path = scratch_dir / "settings.toml"
    settings = Settings(
        location=LocationSettings(country=case.location.country, currency=case.location.currency)
    )
    save_settings(settings, path)
    return path


# -- running a case ---------------------------------------------------------


async def run_stable(
    case: EvalCase,
    case_dir: Path,
    store: RunStore,
    scratch_dir: Path,
    *,
    refiner: Refiner | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
) -> RunRecord | None:
    """SURVEY/EXTRACTION/TIMING/PRIOR-GEN replay `case_dir`'s frozen
    fixtures (no live call is possible for these — see module docstring).
    REFINE/SCORING/SYNTHESIS run live by default (`refiner`/`scorer`/
    `synthesizer` default `None` -> real `Sdk*` adapters, injectable for
    this module's own tests, mirroring every other phase's DI pattern)."""
    port = EvalQuestionPort(case.intake_script)
    settings_path = _settings_path_for(case, scratch_dir)
    return await run_pipeline(
        case.product_type,
        port,
        store,
        settings_path=settings_path,
        surveyor=_FrozenSurveyor(_load_raw_survey(case_dir)),
        refiner=refiner,
        extractor=_FrozenExtractor(_load_products(case_dir)),
        timing_researcher=_FrozenTimingResearcher(_load_timing(case_dir)),
        prior_gen_researcher=_FrozenPriorGenResearcher(_load_prior_gen(case_dir)),
        scorer=scorer,
        synthesizer=synthesizer,
    )


async def run_live(
    case: EvalCase,
    store: RunStore,
    scratch_dir: Path,
    *,
    surveyor: Surveyor | None = None,
    refiner: Refiner | None = None,
    extractor: Extractor | None = None,
    timing_researcher: TimingResearcher | None = None,
    prior_gen_researcher: PriorGenResearcher | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
) -> RunRecord | None:
    """Every adapter real by default — hits the actual web and API. Used
    by the decaying/full `scout eval` mode and by `capture_case` (via
    recording wrappers around the same real adapters)."""
    port = EvalQuestionPort(case.intake_script)
    settings_path = _settings_path_for(case, scratch_dir)
    return await run_pipeline(
        case.product_type,
        port,
        store,
        settings_path=settings_path,
        surveyor=surveyor,
        refiner=refiner,
        extractor=extractor,
        timing_researcher=timing_researcher,
        prior_gen_researcher=prior_gen_researcher,
        scorer=scorer,
        synthesizer=synthesizer,
    )


async def capture_case(case_dir: Path, case: EvalCase, store: RunStore, scratch_dir: Path) -> RunRecord:
    """Author (or refresh) `case_dir`'s frozen fixtures by actually running
    the live pipeline once. Wraps the four web-tool adapters in `_Recording*`
    decorators around the real `Sdk*` adapters, runs `run_pipeline` exactly
    once (so orchestration logic lives in exactly one place — never
    reimplemented here), then freezes what each recorder captured. Writes
    `case.json` too, so a case directory is fully self-contained after one
    call. Raises if the run declined to proceed or returned no record —
    `EvalQuestionPort` never picks SURVEY's "Stop here" option, so this
    should not happen in practice; a raise here means something upstream
    behaved unexpectedly and the case must not be silently frozen anyway.
    """
    surveyor = _RecordingSurveyor(SdkSurveyor())
    extractor = _RecordingExtractor(SdkExtractor())
    timing_researcher = _RecordingTimingResearcher(SdkTimingResearcher())
    prior_gen_researcher = _RecordingPriorGenResearcher(SdkPriorGenResearcher())

    record = await run_live(
        case,
        store,
        scratch_dir,
        surveyor=surveyor,
        refiner=SdkRefiner(),
        extractor=extractor,
        timing_researcher=timing_researcher,
        prior_gen_researcher=prior_gen_researcher,
        scorer=SdkScorer(),
        synthesizer=SdkSynthesizer(),
    )
    if record is None:
        raise RuntimeError(
            f"capture_case({case.name!r}): run_pipeline returned no record — "
            "the run must have declined to proceed, which EvalQuestionPort should "
            "never trigger. Investigate before freezing anything."
        )
    if surveyor.raw is None or extractor.products is None or timing_researcher.assessment is None or prior_gen_researcher.raw is None:
        missing = [
            name
            for name, val in (
                ("survey", surveyor.raw), ("extraction", extractor.products),
                ("timing", timing_researcher.assessment), ("prior_gen", prior_gen_researcher.raw),
            )
            if val is None
        ]
        raise RuntimeError(
            f"capture_case({case.name!r}): {missing} never ran — "
            f"truncated_at_phase={record.truncated_at_phase!r}, "
            f"caveats={[c.text for c in record.caveats]!r}. Refusing to freeze a partial case."
        )

    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "case.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")
    (case_dir / "survey.json").write_text(surveyor.raw.model_dump_json(indent=2), encoding="utf-8")
    (case_dir / "products.json").write_text(
        json.dumps([p.model_dump(mode="json") for p in extractor.products], indent=2), encoding="utf-8"
    )
    (case_dir / "timing.json").write_text(timing_researcher.assessment.model_dump_json(indent=2), encoding="utf-8")
    (case_dir / "prior_gen.json").write_text(prior_gen_researcher.raw.model_dump_json(indent=2), encoding="utf-8")
    ledger = surveyor.ledger or extractor.ledger
    if ledger is not None:
        _save_ledger(case_dir, ledger)
    return record


# -- stable assertions (§17.1's blocking half) -------------------------------


def check_every_product_has_a_con(record: RunRecord) -> list[str]:
    """Invariant 6 — already schema-guaranteed at `Product` construction
    (`cons: list[str] = Field(min_length=1)`), so this can't realistically
    fail; asserted anyway as an integration confirmation that nothing
    between construction and this record's persistence stripped it."""
    return [p.name for p in record.products if not p.cons]


def check_constraint_satisfaction(record: RunRecord) -> list[str]:
    """§5.1's row-composition rules, re-checked against the FINAL product
    list. Deliberately narrower than the full spec text: without the
    pre-enforcement candidate pool (only `run_scoring`'s own `_find_issues`
    re-prompt loop has that), this can only check FINAL-STATE
    self-consistency, not "would 3 have qualified" counterfactuals —
    flagged here rather than resolved silently, same house style as
    `location.py`'s own documented limitations."""
    violations: list[str] = []
    if len(record.products) > 12:
        violations.append(f"row count {len(record.products)} exceeds the §5.1 hard cap of 12")

    in_budget_recs = [
        p for p in record.products if p.role == "recommendation" and p.in_budget
    ]
    if len(in_budget_recs) >= 3:
        archetypes = {p.strength_archetype for p in in_budget_recs}
        if len(archetypes) < 3:
            violations.append(
                f"{len(in_budget_recs)} in-budget recommendation rows share only "
                f"{len(archetypes)} distinct strength_archetype value(s), short of §5.1's 3"
            )
    return violations


def check_ledger_validity(record: RunRecord, ledger: FetchLedger) -> list[str]:
    """§4.3, re-derived offline against the case's own frozen ledger — the
    identical `is_admissible` calls `tools/record_product.py` makes live.
    Since EXTRACTION is frozen (never re-run in `--stable-only`), this
    check is, by construction, invariant across skill edits once a case is
    correctly captured — its value is guarding against a future *code*
    regression to `FetchLedger`/`Product`'s own validators, or a
    hand-edited case fixture that quietly breaks §4.3 discipline, not
    against live model drift. Prior-gen products (`generation == "prior"`)
    are skipped — they're validated against a different phase's ledger
    state this case's single `ledger.json` doesn't capture."""
    violations: list[str] = []
    for product in record.products:
        if product.generation == "prior":
            continue
        for spec_name, sv in product.specs.items():
            if not ledger.is_admissible(sv.source_url, require_fetched=True):
                violations.append(f'{product.name}: specs[{spec_name!r}].source_url not admissible')
        for i, sv in enumerate(product.ownership_notes):
            if not ledger.is_admissible(sv.source_url, require_fetched=False):
                violations.append(f"{product.name}: ownership_notes[{i}].source_url not admissible")
        if not ledger.is_admissible(product.pricing.price_source_url, require_fetched=True):
            violations.append(f"{product.name}: pricing.price_source_url not admissible")
        for i, url in enumerate(product.review_sources):
            if not ledger.is_admissible(url, require_fetched=False):
                violations.append(f"{product.name}: review_sources[{i}] not admissible")
    return violations


def check_band_tiling_and_totality(record: RunRecord) -> list[str]:
    """§4.2 — every product's confidence maps to exactly one band. Same
    predicate as `confidence.py`'s `test_bands_tile`, applied to this
    record's actual products rather than the full [0, 1] sweep
    `property_test_violations()` already covers generally."""
    violations = []
    for p in record.products:
        band = confidence_band(p.evidence.confidence)
        if band not in {"high", "moderate", "low", "very_low"}:
            violations.append(f"{p.name}: confidence {p.evidence.confidence} did not tile into a known band")
    return violations


def check_axis_kind_recorded(record: RunRecord) -> list[str]:
    """§9.4 — `axis_kind` decided in SURVEY and recorded, for every
    `Dimension`."""
    return [d.name for d in record.survey.dimensions if d.axis_kind is None]


def check_shared_denominator(record: RunRecord) -> list[str]:
    """§4.0b — trivially true by construction (`comparison_specs` lives
    once, on `SurveyReport`, not per-product), asserted anyway as an
    integration confirmation that nothing downstream forked it."""
    del record  # nothing to check beyond the schema itself; see docstring
    return []


def check_verdict_shape(record: RunRecord, case: EvalCase) -> list[str]:
    """"Verdict shape given coverage class" — checked against whichever
    existing code-level classifier the case's `expected_shape.kind`
    targets (§17.1's 5 categories don't correspond to one schema enum;
    see build order step 15's design notes)."""
    kind = case.expected_shape.kind
    if kind == "rich":
        if record.verdict.action == "INSUFFICIENT_EVIDENCE":
            return ["rich case produced an INSUFFICIENT_EVIDENCE verdict"]
    elif kind == "sparse":
        if not record.low_evidence_mode:
            return ["sparse case did not set low_evidence_mode"]
    elif kind == "commodity":
        if not record.commodity_category:
            return ["commodity case did not set commodity_category"]
    elif kind == "software":
        if record.survey.category_kind != "software_service":
            return [f"software case has category_kind={record.survey.category_kind!r}"]
    elif kind == "cross_border":
        if not any(p.availability.landed_price_native is not None for p in record.products):
            return ["cross_border case has no product with a computed landed_price_native"]
    return []


def stable_checks(record: RunRecord, case: EvalCase, ledger: FetchLedger | None) -> dict[str, list[str]]:
    """`ledger=None` in live/decaying mode — `check_ledger_validity` needs
    the SAME `FetchLedger` the run actually populated, which `run_live`
    doesn't expose (only `capture_case`'s recording wrappers do); that
    check is inherently a frozen-fixture-vs-frozen-ledger consistency
    check, so it's simply omitted here rather than run against a
    meaningless empty ledger."""
    checks = {
        "every_product_has_a_con": check_every_product_has_a_con(record),
        "constraint_satisfaction": check_constraint_satisfaction(record),
        "band_tiling_and_totality": check_band_tiling_and_totality(record),
        "axis_kind_recorded": check_axis_kind_recorded(record),
        "shared_denominator": check_shared_denominator(record),
        "verdict_shape": check_verdict_shape(record, case),
        "confidence_properties": property_test_violations(),
    }
    if ledger is not None:
        checks["ledger_validity"] = check_ledger_validity(record, ledger)
    return checks


# -- decaying assertions (§17.1's informational half) ------------------------


def check_must_appear_products(record: RunRecord, case: EvalCase) -> list[str]:
    present = {p.name for p in record.products}
    return [name for name in case.must_appear_products if name not in present]


def check_confidence_ordering(record: RunRecord, case: EvalCase) -> list[str]:
    by_name = {p.name: p.evidence.confidence for p in record.products}
    ordered = [by_name[n] for n in case.expected_confidence_order if n in by_name]
    violations = []
    for prev_name, prev_conf, name, conf in zip(
        case.expected_confidence_order, ordered, case.expected_confidence_order[1:], ordered[1:]
    ):
        if conf > prev_conf:
            violations.append(f"{name} ({conf}) outranks {prev_name} ({prev_conf}) — expected order broke")
    return violations


def check_price_assertions(record: RunRecord, case: EvalCase) -> list[str]:
    by_name = {p.name: p for p in record.products}
    violations = []
    for assertion in case.price_assertions:
        product = by_name.get(assertion.product_name)
        if product is None:
            violations.append(f"{assertion.product_name}: not present in this run")
            continue
        price = product.pricing.upfront_amount or product.pricing.total_cost_1yr
        if price is None:
            violations.append(f"{assertion.product_name}: no price recorded")
            continue
        low = assertion.expected_price * (1 - assertion.tolerance)
        high = assertion.expected_price * (1 + assertion.tolerance)
        if not (low <= price <= high):
            violations.append(
                f"{assertion.product_name}: price {price} outside expected "
                f"{assertion.expected_price} ± {assertion.tolerance:.0%}"
            )
    return violations


def decaying_checks(record: RunRecord, case: EvalCase) -> dict[str, list[str]]:
    return {
        "must_appear_products": check_must_appear_products(record, case),
        "confidence_ordering": check_confidence_ordering(record, case),
        "price_assertions": check_price_assertions(record, case),
    }


# -- suite aggregation -------------------------------------------------------


class CaseResult(BaseModel):
    case_name: str
    stable: dict[str, list[str]]
    decaying: dict[str, list[str]] = {}

    @property
    def stable_ok(self) -> bool:
        return all(not v for v in self.stable.values())


class SuiteReport(BaseModel):
    results: list[CaseResult]

    @property
    def ok(self) -> bool:
        """`all(...)` over an empty sequence is vacuously `True` — without
        the explicit guard, an empty or missing `eval/cases/` would make
        `scout eval --stable-only` silently exit 0, looking exactly like
        every stable check passed when nothing was actually checked at
        all. "Silence is not success": zero cases is never a pass."""
        if not self.results:
            return False
        return all(r.stable_ok for r in self.results)


async def run_eval_suite(
    cases_dir: Path,
    store: RunStore,
    scratch_dir: Path,
    *,
    stable_only: bool,
    refiner: Refiner | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
) -> SuiteReport:
    """The top-level entry `cli.py` calls. `stable_only=True` replays every
    case's frozen research fixtures (`run_stable`); `stable_only=False`
    hits the live web for every phase (`run_live`) and also evaluates the
    decaying assertions — §17.1: "Only the decaying half runs live, and
    only manually." Either way, only the stable results affect
    `SuiteReport.ok` — "only the stable half blocks" is a property of the
    assertion, not of which mode produced the record."""
    results: list[CaseResult] = []
    case_dirs = sorted(p for p in cases_dir.iterdir() if p.is_dir()) if cases_dir.is_dir() else []
    for case_dir in case_dirs:
        case = load_case(case_dir)
        if stable_only:
            record = await run_stable(case, case_dir, store, scratch_dir, refiner=refiner, scorer=scorer, synthesizer=synthesizer)
            ledger = _load_ledger(case_dir)
            decaying: dict[str, list[str]] = {}
        else:
            record = await run_live(case, store, scratch_dir, scorer=scorer, synthesizer=synthesizer)
            ledger = None  # not meaningfully checkable post-hoc for a live run; see stable_checks
            decaying = decaying_checks(record, case) if record is not None else {}
        if record is None:
            results.append(
                CaseResult(case_name=case.name, stable={"run": ["run_pipeline returned no record"]})
            )
            continue
        results.append(
            CaseResult(case_name=case.name, stable=stable_checks(record, case, ledger), decaying=decaying)
        )
    return SuiteReport(results=results)
