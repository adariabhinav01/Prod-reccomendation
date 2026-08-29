"""Unit tests for orchestrator.py (build order step 13). Every phase is
faked, mirroring each phase module's own test-double pattern (`FakeSurveyor`
etc.) — no live SDK call, no `ANTHROPIC_API_KEY` needed. `run_pipeline`
itself is exercised end to end against these fakes; the cost-cap counter's
own cross-phase-survival behavior (the "7N vs N" regression) is covered
directly in `tests/test_budget.py`, not re-tested here — this file's job is
proving `orchestrator.py` actually shares ONE `RunBudget` across every
phase call and reacts correctly to it tripping.
"""

import asyncio

import pytest

from product_scout.hooks.budget import RunBudget
from product_scout.models import Product
from product_scout.orchestrator import (
    _extraction_candidates,
    _mark_truncated,
    _REQUIRED_SKILLS,
    merge_caveats,
    run_pipeline,
)
from product_scout.io.cli_port import CLIQuestionPort
from product_scout.phases.prior_gen import RawPriorGen
from product_scout.phases.refine import RefineOutcome
from product_scout.phases.scoring import ROW_CAP, RawProductScore, RawScoring
from product_scout.phases.survey import RawSurvey, SurveyOutcome
from product_scout.phases.synthesis import RawSynthesis
from product_scout.render.report import render_html
from product_scout.skills import hash_required_skills
from product_scout.settings import Settings, LocationSettings, save as save_settings
from product_scout.store.checkpoint import PHASE_NAMES
from product_scout.store.runs import RunStore
from tests.conftest import (
    make_cluster,
    make_intake_answers,
    make_location,
    make_product,
    make_run_budget,
    make_settings,
    make_survey_report,
    make_timing_assessment,
)


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """Same helper as test_survey.py/test_intake.py."""
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


def settings_path_with_location(tmp_path):
    path = tmp_path / "config.toml"
    settings = make_settings(location=LocationSettings(country="US", currency="USD"))
    save_settings(settings, path)
    return path


# Intake's script when location is already in settings (settings_path_with_
# location above): owns=no, budget=no limit, features=none, candidates=none.
INTAKE_SCRIPT = ["no", "no limit", "", ""]


# -- fakes: one per Protocol, mirroring each phase module's own test double -


class FakeSurveyor:
    def __init__(self, raw: RawSurvey, *, trip_budget: bool = False):
        self._raw = raw
        self._trip_budget = trip_budget
        self.calls: list[tuple] = []

    async def survey(self, product_type, location, ledger, budget, progress=None):
        self.calls.append((product_type, location))
        if self._trip_budget:
            budget.fetches_used = budget.max_fetches
        return self._raw


class FakeRefiner:
    def __init__(self, topics: list | None = None):
        self._topics = topics or []
        self.calls: list[tuple] = []

    async def propose_topics(self, survey, intake):
        self.calls.append((survey, intake))
        return self._topics


class FakeExtractor:
    def __init__(self, products: list[Product], *, trip_budget: bool = False):
        self._products = products
        self._trip_budget = trip_budget
        self.calls: list[tuple] = []

    async def extract(
        self, product_type, candidates, survey, ledger, location, low_evidence_mode, budget,
        progress=None,
    ):
        self.calls.append((product_type, candidates, low_evidence_mode))
        if self._trip_budget:
            budget.fetches_used = budget.max_fetches
        return self._products


class FakeTimingResearcher:
    def __init__(self, assessment):
        self._assessment = assessment
        self.calls: list[tuple] = []

    async def research(self, product_type, product_names, ledger, low_evidence_mode, budget, progress=None):
        self.calls.append((product_type, product_names, low_evidence_mode))
        return self._assessment


class FakePriorGenResearcher:
    def __init__(self, raw: RawPriorGen):
        self._raw = raw
        self.calls: list[tuple] = []

    async def research(self, seeds, ledger, low_evidence_mode, budget, progress=None):
        self.calls.append((seeds, low_evidence_mode))
        return self._raw


class FakeScorer:
    def __init__(self, responses: list[RawScoring]):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    async def propose_scores(self, products, survey, intake, topics, low_evidence_mode, feedback):
        self.calls.append((products, low_evidence_mode, feedback))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class FakeSynthesizer:
    def __init__(self, raw: RawSynthesis):
        self._raw = raw
        self.calls: list[tuple] = []

    async def synthesize(self, products, scores, survey, intake, timing, low_evidence_mode, top_picks):
        self.calls.append((products, scores, low_evidence_mode))
        return self._raw


# -- happy-path fixtures ------------------------------------------------------


def _raw_survey(**overrides) -> RawSurvey:
    # Default evidence pool covers make_survey_report()'s default cluster
    # exemplar ("Widget Pro") so §8.1a's _verify_exemplars constraint
    # doesn't strip it before EXTRACTION ever sees it — same guard
    # test_survey.py's own make_raw() helper needs, for the same reason.
    evidence_pool = overrides.pop("evidence_pool", ["The Widget Pro is a popular choice."])
    return RawSurvey(report=make_survey_report(**overrides), evidence_pool=evidence_pool, caveats=[])


def _happy_scoring() -> RawScoring:
    return RawScoring(
        scores=[
            RawProductScore(
                product_name="Widget Pro",
                score=8.2,
                rationale="Strong fit given stated constraints.",
                role="recommendation",
                strength_archetype="value",
                score_at_minus_10pct=8.4,
                score_at_minus_20pct=8.6,
                score_at_minus_30pct=8.8,
            )
        ]
    )


def _happy_synthesis() -> RawSynthesis:
    return RawSynthesis(verdict_action="BUY", verdict_reasoning="Best fit.", verdict_timing_note=None)


def _happy_fakes(**overrides):
    """A full set of fakes for a clean, non-tripped run — individual
    fields overridden per test."""
    defaults = dict(
        surveyor=FakeSurveyor(_raw_survey(coverage="rich")),
        refiner=FakeRefiner([]),
        extractor=FakeExtractor([make_product(name="Widget Pro")]),
        timing_researcher=FakeTimingResearcher(make_timing_assessment()),
        prior_gen_researcher=FakePriorGenResearcher(RawPriorGen(findings=[])),
        scorer=FakeScorer([_happy_scoring()]),
        synthesizer=FakeSynthesizer(_happy_synthesis()),
    )
    defaults.update(overrides)
    return defaults


# -- run_pipeline: §3.3 startup skill assertion -------------------------------


def test_run_pipeline_checks_every_required_skill_before_intake_asks_anything(tmp_path, monkeypatch):
    """§3.3: 'assert at startup that skills actually loaded' — checked once,
    up front, before INTAKE's first question. Proven by ORDER here: the
    fake port raises if asked anything before the full skill sweep has
    already recorded all four names."""
    import product_scout.orchestrator as orchestrator_module

    checked: list[str] = []
    monkeypatch.setattr(orchestrator_module, "assert_skill_loaded", checked.append)

    def fail(_prompt):
        raise AssertionError("INTAKE must not ask anything before the skill sweep completes")

    port = CLIQuestionPort(input_fn=fail, print_fn=lambda line="": None)
    store = RunStore(root=tmp_path / ".product-scout")

    with pytest.raises(AssertionError):
        run(
            run_pipeline(
                "standing desks", port, store,
                settings_path=settings_path_with_location(tmp_path), **_happy_fakes(),
            )
        )

    assert checked == list(_REQUIRED_SKILLS)


def test_run_pipeline_raises_before_any_phase_runs_if_a_skill_is_actually_missing(
    tmp_path, monkeypatch
):
    import product_scout.skills as skills_module

    monkeypatch.setattr(skills_module, "_REPO_ROOT", tmp_path)  # no .claude/skills/ here at all

    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes()

    with pytest.raises(RuntimeError, match="not found"):
        run(
            run_pipeline(
                "standing desks", port, store,
                settings_path=settings_path_with_location(tmp_path), **fakes,
            )
        )

    assert fakes["surveyor"].calls == []  # SURVEY never got a chance to spend anything


# -- run_pipeline: full run, no trip -----------------------------------------


def test_full_run_calls_every_phase_once_in_order_and_saves(tmp_path):
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes()

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record is not None
    assert record.truncated_at_phase is None
    assert len(fakes["surveyor"].calls) == 1
    assert len(fakes["refiner"].calls) == 1
    assert len(fakes["extractor"].calls) == 1
    assert len(fakes["timing_researcher"].calls) == 1
    assert len(fakes["prior_gen_researcher"].calls) == 1
    assert len(fakes["scorer"].calls) == 1
    assert len(fakes["synthesizer"].calls) == 1
    assert record.verdict.action == "BUY"
    assert store.exists(record.run_id)
    assert store.report_path(record.run_id).exists()
    assert record.skill_hashes == hash_required_skills(_REQUIRED_SKILLS)


def test_full_run_reports_progress_for_every_phase_in_order(tmp_path):
    """§16.2: 'the long research phases emit per-phase progress' — checked
    here for all 9 phases, not just the research-heavy ones, since
    `report_progress` is called uniformly at every phase boundary."""
    port, printed = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes()

    run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    progress_lines = [line for line in printed if line]
    expected = ["INTAKE", "SURVEY", "REFINE", "EXTRACTION", "TIMING", "PRIOR-GEN", "SCORING", "SYNTHESIS", "RENDER"]
    # Each expected phase name must appear, in order (other lines — the
    # actual intake questions — are interleaved and ignored here).
    positions = [progress_lines.index(name) for name in expected]
    assert positions == sorted(positions)


# -- run_pipeline: §16.1 resume ------------------------------------------


def test_resume_with_no_checkpoints_at_all_raises_file_not_found(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")

    def fail(_prompt):
        raise AssertionError("resuming a run with nothing checkpointed must not ask anything")

    port = CLIQuestionPort(input_fn=fail, print_fn=lambda line="": None)

    with pytest.raises(FileNotFoundError):
        run(run_pipeline("", port, store, resume_run_id="no-such-run", **_happy_fakes()))


def test_resume_of_an_already_completed_run_returns_existing_record_without_recomputing(tmp_path):
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes()

    first = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    def fail(_prompt):
        raise AssertionError("resuming a fully completed run must not ask anything")

    resumed = run(
        run_pipeline(
            "", CLIQuestionPort(input_fn=fail, print_fn=lambda line="": None), store,
            resume_run_id=first.run_id, **fakes,
        )
    )

    assert resumed.run_id == first.run_id
    assert resumed.verdict.action == first.verdict.action
    # Nothing re-ran — every fake's call count is exactly what the first,
    # real run already left it at.
    assert len(fakes["surveyor"].calls) == 1
    assert len(fakes["refiner"].calls) == 1
    assert len(fakes["scorer"].calls) == 1
    assert len(fakes["synthesizer"].calls) == 1


def test_resume_skips_only_the_phases_already_checkpointed(tmp_path):
    """Seeds `intake` and `survey` checkpoints by hand (simulating a crash
    right after SURVEY) and resumes — SURVEY (and the port, for
    INTAKE/SURVEY's own interaction) must never be touched again, while
    REFINE onward runs normally."""
    store = RunStore(root=tmp_path / ".product-scout")
    run_id = "resume-test-0001"

    intake = make_intake_answers()
    location = make_location()
    store.save_checkpoint(
        run_id, "intake",
        {"intake": intake.model_dump(mode="json"), "location": location.model_dump(mode="json"), "units": "imperial"},
    )
    survey_outcome = SurveyOutcome(
        survey=make_survey_report(coverage="rich"),
        proceed=True,
        low_evidence_mode=False,
        product_type="standing desks",
        original_product_type=None,
        category_broadening_offered=False,
        caveats=[],
    )
    store.save_checkpoint(run_id, "survey", survey_outcome.model_dump(mode="json"))

    fakes = _happy_fakes()
    port, _ = make_port([])  # would raise StopIteration if intake/survey were re-asked anything

    record = run(run_pipeline("", port, store, resume_run_id=run_id, **fakes))

    assert record is not None
    assert record.run_id == run_id
    assert fakes["surveyor"].calls == []  # skipped — reused the seeded checkpoint
    assert len(fakes["refiner"].calls) == 1  # ran fresh, past the resume point
    assert len(fakes["scorer"].calls) == 1
    assert len(fakes["synthesizer"].calls) == 1
    assert record.verdict.action == "BUY"
    assert store.exists(record.run_id)


def test_extraction_candidates_are_the_surviving_clusters_exemplars(tmp_path):
    """§1: REFINE runs before EXTRACTION so its answers can narrow the
    candidate list — this is the end-to-end proof, not just
    `_extraction_candidates`'s own unit test below."""
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes()

    run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    product_type, candidates, _ = fakes["extractor"].calls[0]
    assert candidates == ["Widget Pro"]  # make_survey_report()'s default cluster exemplar


# -- run_pipeline: §8.2 user-declined stop is NOT a §13.1 truncation --------


def test_user_declines_to_proceed_returns_none_and_saves_nothing(tmp_path):
    port, _ = make_port([*INTAKE_SCRIPT, "no"])  # "no" answers the barren-coverage fallback
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes(
        surveyor=FakeSurveyor(_raw_survey(coverage="barren", suggested_broader_categories=[]))
    )

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record is None
    assert store.list_run_ids() == []
    assert fakes["refiner"].calls == []  # never reached
    assert fakes["extractor"].calls == []


# -- run_pipeline: §13.1 truncation -------------------------------------------


def test_budget_tripped_during_survey_skips_every_later_research_phase(tmp_path):
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes(
        surveyor=FakeSurveyor(_raw_survey(coverage="rich"), trip_budget=True),
        scorer=FakeScorer([RawScoring(scores=[])]),
    )

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record is not None
    assert record.truncated_at_phase == PHASE_NAMES.index("survey")
    assert fakes["extractor"].calls == []
    assert fakes["timing_researcher"].calls == []
    assert fakes["prior_gen_researcher"].calls == []
    assert record.products == []
    # run_scoring/run_synthesis short-circuit internally on an empty
    # product list — never call the fakes at all.
    assert fakes["scorer"].calls == []
    assert fakes["synthesizer"].calls == []
    assert record.verdict.action == "INSUFFICIENT_EVIDENCE"


def test_budget_tripped_mid_extraction_skips_timing_and_prior_gen_only(tmp_path):
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes(
        extractor=FakeExtractor([make_product(name="Widget Pro")], trip_budget=True),
    )

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record is not None
    assert len(fakes["extractor"].calls) == 1  # extraction itself DID run
    assert fakes["timing_researcher"].calls == []
    assert fakes["prior_gen_researcher"].calls == []
    assert record.truncated_at_phase == PHASE_NAMES.index("extraction")
    assert len(record.products) == 1
    # SCORING/SYNTHESIS still run — "completes what it can from what it has".
    assert len(fakes["scorer"].calls) == 1
    assert len(fakes["synthesizer"].calls) == 1


def test_truncated_run_produces_a_schema_valid_record_that_renders_with_banner(tmp_path):
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes(
        extractor=FakeExtractor([make_product(name="Widget Pro")], trip_budget=True),
    )

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record is not None  # pydantic validation already happened in RunRecord(**...)
    html = render_html(record)
    assert "truncation-banner" in html
    assert "extraction phase" in html
    decision_affecting = [c for c in record.caveats if c.tier == "decision_affecting"]
    assert any("truncated" in c.text.lower() for c in decision_affecting)


def test_second_trip_never_overwrites_the_first_recorded_phase(tmp_path):
    """A budget that's ALREADY tripped from SURVEY must not get
    re-attributed to EXTRACTION just because extraction's own fake also
    tries to trip it — `truncated_at_phase` names the FIRST phase."""
    port, _ = make_port(INTAKE_SCRIPT)
    store = RunStore(root=tmp_path / ".product-scout")
    fakes = _happy_fakes(
        surveyor=FakeSurveyor(_raw_survey(coverage="rich"), trip_budget=True),
        extractor=FakeExtractor([], trip_budget=True),  # never actually called
        scorer=FakeScorer([RawScoring(scores=[])]),
    )

    record = run(
        run_pipeline(
            "standing desks", port, store,
            settings_path=settings_path_with_location(tmp_path), **fakes,
        )
    )

    assert record.truncated_at_phase == PHASE_NAMES.index("survey")


# -- pure-function unit tests -------------------------------------------------


def test_extraction_candidates_unions_user_candidates_and_surviving_exemplars():
    survey = make_survey_report(
        clusters=[
            make_cluster(key="mid-tier", exemplar_products=["Widget Pro"]),
            make_cluster(key="budget", exemplar_products=["Widget Lite"]),
        ]
    )
    refine_outcome = RefineOutcome(topics=[], surviving_clusters={"mid-tier"}, caveats=[])
    intake = make_intake_answers(candidates_under_consideration=["User's Pick"])

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert candidates == ["User's Pick", "Widget Pro"]  # "budget" cluster didn't survive
    assert caveat is None


def test_extraction_candidates_dedupes_first_occurrence_wins():
    survey = make_survey_report(clusters=[make_cluster(key="mid-tier", exemplar_products=["Widget Pro"])])
    refine_outcome = RefineOutcome(topics=[], surviving_clusters={"mid-tier"}, caveats=[])
    intake = make_intake_answers(candidates_under_consideration=["Widget Pro"])

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert candidates == ["Widget Pro"]  # not duplicated
    assert caveat is None


def test_extraction_candidates_caps_at_row_cap_with_no_caveat_when_under():
    """Fewer surviving candidates than ROW_CAP — no cap, no caveat, matches
    the standing-desks live failure's absence in the small case."""
    survey = make_survey_report(
        clusters=[
            make_cluster(key="a", exemplar_products=["A1"]),
            make_cluster(key="b", exemplar_products=["B1"]),
        ]
    )
    refine_outcome = RefineOutcome(topics=[], surviving_clusters={"a", "b"}, caveats=[])
    intake = make_intake_answers()

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert candidates == ["A1", "B1"]
    assert caveat is None


def test_extraction_candidates_caps_at_row_cap_and_emits_caveat():
    """The standing-desks failure mode: many clusters, each with several
    exemplars, should never send more than ROW_CAP names to EXTRACTION —
    and dropping anything must show up as a caveat, never silently."""
    clusters = [
        make_cluster(key=f"cluster-{i}", exemplar_products=[f"P{i}a", f"P{i}b", f"P{i}c"])
        for i in range(7)  # 7 clusters x 3 exemplars = 21 candidates, well over ROW_CAP=12
    ]
    survey = make_survey_report(clusters=clusters)
    refine_outcome = RefineOutcome(
        topics=[], surviving_clusters={c.key for c in clusters}, caveats=[]
    )
    intake = make_intake_answers()

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert len(candidates) == ROW_CAP
    assert caveat is not None
    assert "12" in caveat  # names the cap
    assert "9" in caveat  # 21 total - 12 kept = 9 dropped


def test_extraction_candidates_round_robins_across_surviving_clusters():
    """Every surviving cluster contributes at least one candidate before
    any cluster contributes a second — a single exemplar-heavy cluster
    must not exhaust the cap and starve the rest."""
    clusters = [
        make_cluster(key="heavy", exemplar_products=[f"Heavy{i}" for i in range(20)]),
        make_cluster(key="light-a", exemplar_products=["LightA1"]),
        make_cluster(key="light-b", exemplar_products=["LightB1"]),
    ]
    survey = make_survey_report(clusters=clusters)
    refine_outcome = RefineOutcome(
        topics=[], surviving_clusters={"heavy", "light-a", "light-b"}, caveats=[]
    )
    intake = make_intake_answers()

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert "LightA1" in candidates  # not starved by "heavy"
    assert "LightB1" in candidates
    assert len(candidates) == ROW_CAP
    assert caveat is not None


def test_extraction_candidates_never_drops_user_named_picks():
    """User-named candidates are kept unconditionally, even if they alone
    reach or exceed ROW_CAP — explicit user picks are never dropped."""
    user_picks = [f"UserPick{i}" for i in range(ROW_CAP + 3)]
    survey = make_survey_report(clusters=[make_cluster(key="a", exemplar_products=["A1"])])
    refine_outcome = RefineOutcome(topics=[], surviving_clusters={"a"}, caveats=[])
    intake = make_intake_answers(candidates_under_consideration=user_picks)

    candidates, caveat = _extraction_candidates(survey, refine_outcome, intake)

    assert set(user_picks) <= set(candidates)
    assert caveat is not None  # "A1" never got a slot


def test_mark_truncated_sets_once_and_never_moves():
    budget = make_run_budget()
    assert _mark_truncated(None, budget, "survey") is None  # not tripped yet

    budget.fetches_used = budget.max_fetches
    first = _mark_truncated(None, budget, "extraction")
    assert first == PHASE_NAMES.index("extraction")

    # Already set — a later phase seeing the same tripped budget must not
    # move it, even though budget.tripped is still True.
    second = _mark_truncated(first, budget, "timing")
    assert second == PHASE_NAMES.index("extraction")


def test_merge_caveats_anchors_to_a_matching_product_name():
    products = [make_product(name="Widget Pro")]
    caveats = merge_caveats(['A note specifically about "Widget Pro".'], products)

    assert len(caveats) == 1
    assert caveats[0].tier == "decision_affecting"
    assert caveats[0].anchor == "Widget Pro"


def test_merge_caveats_never_collapses_decision_affecting_entries():
    products = [make_product(name="Widget Pro")]
    caveats = merge_caveats(
        ['Note A about "Widget Pro".', 'Note B about "Widget Pro".'], products
    )
    decision = [c for c in caveats if c.tier == "decision_affecting"]
    assert len(decision) == 2


def test_merge_caveats_collapses_structurally_identical_provenance_entries():
    caveats = merge_caveats(
        [
            "Skipped an axis question — assumed no preference on weight.",
            "Skipped an axis question — assumed no preference on weight.",
        ],
        [],
    )
    assert len(caveats) == 1
    assert caveats[0].tier == "provenance"
    assert caveats[0].instance_count == 2


def test_merge_caveats_keeps_distinct_provenance_entries_separate():
    caveats = merge_caveats(
        ["A provenance note.", "A different provenance note."], []
    )
    assert len(caveats) == 2
    assert all(c.instance_count == 1 for c in caveats)
