"""Unit tests for phases/refine.py — Phase 2 REFINE's §9.7 stopping
condition, §9.7a bail-out, floor, CAP, and the "only Dimension-mapped
topics can shrink surviving_clusters" rule (build order step 7).
`SdkRefiner` (the real SDK-calling adapter) is intentionally not exercised
here — see the module docstring; only `run_refine`'s state machine (against
a fake `Refiner` and a fake `QuestionPort`) and the pure helper functions
(`_apply_gate`, `_established_lean`) are.

Unlike `test_survey.py`, this file uses a small hand-rolled
`FakeQuestionPort` rather than a scripted `CLIQuestionPort`. `ask_topic`
returns a whole structured `TopicAnswer`, not a bare string/bool the way
`ask_choice`/`ask_text` do — supplying canned `TopicAnswer`s directly tests
`run_refine`'s own state machine in isolation from `cli_port.py`'s text
parsing, which `test_cli_port.py` already covers on its own.
"""

import asyncio

from product_scout.io.port import (
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
    QuestionPort,
)
from product_scout.phases.refine import (
    REFINE_TOPIC_CAP,
    RawTopicPrompt,
    Refiner,
    _apply_gate,
    _established_lean,
    run_refine,
)
from tests.conftest import (
    make_axis_spec,
    make_cluster,
    make_dimension,
    make_intake_answers,
    make_raw_topic_prompt,
    make_survey_report,
    make_topic_answer,
    make_topic_prompt,
)


def run(coro):
    return asyncio.run(coro)


class FakeRefiner:
    """Refiner test double: one canned `list[RawTopicPrompt]`, returned on
    every call. Records every `(survey, intake)` pair it was called with."""

    def __init__(self, topics: list[RawTopicPrompt]):
        self._topics = topics
        self.calls: list[tuple[object, object]] = []

    async def propose_topics(self, survey, intake) -> list[RawTopicPrompt]:
        self.calls.append((survey, intake))
        return list(self._topics)


class FakeQuestionPort:
    """QuestionPort test double. `ask_topic` returns scripted `TopicAnswer`s
    in call order; `offer_bailout` either follows its own script or — when
    none is given — raises if called at all, mirroring `test_survey.py`'s
    "would raise if called" idiom for asserting something was never
    offered. `ask_choice`/`ask_text` are unused by `run_refine` in this
    build step (see module docstring) and raise if reached."""

    def __init__(self, topic_answers, bailout_script=None):
        self._topic_answers = iter(topic_answers)
        self._bailout_script = iter(bailout_script) if bailout_script is not None else None
        self.ask_topic_calls: list = []
        self.bailout_calls = 0

    async def ask_choice(self, question, options, escape_hatch):
        raise NotImplementedError("run_refine doesn't call ask_choice in this build step")

    async def ask_topic(self, topic):
        self.ask_topic_calls.append(topic)
        return next(self._topic_answers)

    async def offer_bailout(self) -> bool:
        self.bailout_calls += 1
        if self._bailout_script is None:
            raise AssertionError("offer_bailout should not have been called")
        return next(self._bailout_script)

    async def ask_text(self, prompt):
        raise NotImplementedError("run_refine doesn't call ask_text in this build step")


def _raw(name: str, satisfies: list[str] | None = None) -> RawTopicPrompt:
    """A Dimension-mapped RawTopicPrompt for dimension `name`."""
    return make_raw_topic_prompt(
        topic=make_topic_prompt(
            topic=name,
            dimension_name=name,
            axis=make_axis_spec(kind="position"),
        ),
        satisfies_must_have=satisfies or [],
    )


def _freetext_raw(topic: str = "anything else") -> RawTopicPrompt:
    """A free-text-only RawTopicPrompt — no backing Dimension."""
    return make_raw_topic_prompt(
        topic=make_topic_prompt(topic=topic, dimension_name=None, axis=None),
        satisfies_must_have=[],
    )


def _answer(**overrides) -> object:
    return make_topic_answer(**overrides)


# -- protocol conformance -----------------------------------------------------


def test_fake_refiner_satisfies_refiner():
    assert isinstance(FakeRefiner([]), Refiner)


def test_fake_port_satisfies_question_port():
    assert isinstance(FakeQuestionPort([]), QuestionPort)


# -- floor: min(2, len(separating_dimensions)) --------------------------------


def test_floor_forces_second_topic_even_though_first_answer_alone_would_stop():
    """3 clusters, 3 dimensions all partitioned the same way (a vs {b, c}).
    Answering the first dimension's must_have eliminates "a", which
    collapses every OTHER dimension's separating power to 1 at once — so
    without the floor, condition (a) would be satisfiable after just one
    answer. floor = min(2, 3) = 2 must force a second topic anyway."""
    clusters = [
        make_cluster(key="a", price_range_native=(100.0, 150.0)),
        make_cluster(key="b", price_range_native=(150.0, 200.0)),
        make_cluster(key="c", price_range_native=(200.0, 250.0)),
    ]
    dims = [
        make_dimension(name="motor", splits={"a": "single", "b": "dual", "c": "dual"}),
        make_dimension(name="material", splits={"a": "plastic", "b": "metal", "c": "metal"}),
        make_dimension(name="warranty", splits={"a": "1yr", "b": "3yr", "c": "3yr"}),
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner([_raw("motor", ["dual"]), _raw("material", ["metal"])])
    answers = [
        _answer(
            dimension_name="motor",
            gate_answer="must_have",
            axis_kind=None,
            axis_value=None,
            axis_skipped=True,
            became_filter=True,
        ),
        _answer(
            dimension_name="material",
            gate_answer="persuadable",
            axis_kind="position",
            axis_value=0.5,
            axis_skipped=False,
            became_filter=False,
        ),
    ]
    port = FakeQuestionPort(answers)  # no bailout_script -> raises if called

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 2
    assert outcome.surviving_clusters == {"b", "c"}
    assert port.bailout_calls == 0


def test_floor_one_permits_stopping_after_a_single_topic():
    """Only one real separating dimension -> floor = min(2, 1) = 1. A
    second (free-text-only) topic exists but must never be asked once
    condition (a) is satisfiable right after the first answer."""
    clusters = [make_cluster(key="a"), make_cluster(key="b", price_range_native=(200.0, 300.0))]
    dim = make_dimension(name="motor", splits={"a": "single", "b": "dual"})
    survey = make_survey_report(
        clusters=clusters, dimensions=[dim], comparison_specs=["motor"]
    )
    refiner = FakeRefiner([_raw("motor"), _freetext_raw()])
    port = FakeQuestionPort(
        [_answer(dimension_name="motor", gate_answer="persuadable", axis_skipped=True, axis_value=None)]
    )

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 1
    assert port.bailout_calls == 0


# -- stop condition (a): PRIMARY ----------------------------------------------


def test_stop_condition_a_fires_once_no_remaining_dimension_separates():
    """Same 3-cluster / 3-dimension-in-lockstep shape as the floor test
    above, but here we let the floor's second topic actually run out the
    clock: after motor + material are both answered (meeting floor=2), the
    third dimension (warranty) has also collapsed to power 1 and must never
    be asked."""
    clusters = [
        make_cluster(key="a", price_range_native=(100.0, 150.0)),
        make_cluster(key="b", price_range_native=(150.0, 200.0)),
        make_cluster(key="c", price_range_native=(200.0, 250.0)),
    ]
    dims = [
        make_dimension(name="motor", splits={"a": "single", "b": "dual", "c": "dual"}),
        make_dimension(name="material", splits={"a": "plastic", "b": "metal", "c": "metal"}),
        make_dimension(name="warranty", splits={"a": "1yr", "b": "3yr", "c": "3yr"}),
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner(
        [_raw("motor", ["dual"]), _raw("material", ["metal"]), _raw("warranty", ["3yr"])]
    )
    answers = [
        _answer(
            topic="motor",
            dimension_name="motor",
            gate_answer="must_have",
            axis_kind=None,
            axis_value=None,
            axis_skipped=True,
            became_filter=True,
        ),
        _answer(
            topic="material",
            dimension_name="material",
            gate_answer="persuadable",
            axis_kind="position",
            axis_value=0.5,
            axis_skipped=False,
            became_filter=False,
        ),
    ]
    port = FakeQuestionPort(answers)  # only 2 answers scripted; "warranty" must not be asked

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 2
    assert {t.topic for t in outcome.topics} == {"motor", "material"}
    assert port.bailout_calls == 0


# -- stop condition (b): BACKSTOP, pathological case only ---------------------


def test_backstop_b_fires_on_two_consecutive_no_progress_answers():
    """4 independent separating dimensions (no correlation between them).
    floor = min(2, 4) = 2. The first two answers eliminate nothing,
    establish no lean, and don't down-weight (gate_answer="persuadable",
    axis skipped) -> both count as "no progress", so the backstop should
    fire even though two real separating dimensions are still left."""
    clusters = [make_cluster(key="x"), make_cluster(key="y", price_range_native=(200.0, 300.0))]
    dims = [
        make_dimension(name=f"dim{i}", splits={"x": "px", "y": "py"}) for i in range(4)
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner([_raw(d.name) for d in dims])
    answers = [
        _answer(
            dimension_name=f"dim{i}",
            gate_answer="persuadable",
            axis_kind="position",
            axis_value=None,
            axis_skipped=True,
            became_filter=False,
        )
        for i in range(2)
    ]
    port = FakeQuestionPort(answers)  # only 2 scripted -> dim2/dim3 must not be asked

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 2
    assert port.bailout_calls == 0


def test_backstop_b_does_not_fire_when_answers_show_progress():
    """Same 4-dimension shape, but every answer is no_preference on a
    Dimension-mapped topic (a down-weight, i.e. real progress per §6.4) ->
    the backstop must never fire, and the interview runs all 4 dimensions
    to natural completion (declining the bail-out each time it's offered,
    post-floor, since nothing here asks it to stop)."""
    clusters = [make_cluster(key="x"), make_cluster(key="y", price_range_native=(200.0, 300.0))]
    dims = [
        make_dimension(name=f"dim{i}", splits={"x": "px", "y": "py"}) for i in range(4)
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner([_raw(d.name) for d in dims])
    answers = [
        _answer(
            dimension_name=f"dim{i}",
            gate_answer="no_preference",
            axis_kind="position",
            axis_value=None,
            axis_skipped=True,
            became_filter=False,
        )
        for i in range(4)
    ]
    # floor=2 -> topics 1-2 ask unconditionally; topics 3-4 each first pass
    # a bail-out check (declined) since nothing here ever satisfies (a) or
    # (b) — every answer is "progress".
    port = FakeQuestionPort(answers, bailout_script=[False, False])

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 4
    assert port.bailout_calls == 2


# -- §9.7a bail-out -------------------------------------------------------


def test_bailout_defaults_every_remaining_topic_and_collapses_one_caveat():
    clusters = [make_cluster(key="x"), make_cluster(key="y", price_range_native=(200.0, 300.0))]
    dims = [
        make_dimension(name=f"dim{i}", splits={"x": "px", "y": "py"}) for i in range(5)
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner([_raw(d.name) for d in dims])
    progress_answers = [
        _answer(
            dimension_name=f"dim{i}",
            gate_answer="no_preference",
            axis_kind="position",
            axis_value=None,
            axis_skipped=True,
            became_filter=False,
        )
        for i in range(2)
    ]
    port = FakeQuestionPort(progress_answers, bailout_script=[True])

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 5
    assert len(port.ask_topic_calls) == 2  # only the first two were real interactions
    assert port.bailout_calls == 1

    defaulted = outcome.topics[2:]
    assert len(defaulted) == 3
    for topic_answer in defaulted:
        assert topic_answer.gate_answer == "no_preference"
        assert topic_answer.axis_skipped is True
        assert topic_answer.axis_value == POSITION_SKIP_DEFAULT
        assert topic_answer.assumption_logged is not None

    assert len(outcome.caveats) == 1
    for i in range(2, 5):
        assert f"dim{i}" in outcome.caveats[0]


# -- only Dimension-mapped topics can shrink surviving_clusters ---------------


def test_free_text_only_topic_never_shrinks_surviving_clusters():
    clusters = [make_cluster(key="a"), make_cluster(key="b", price_range_native=(200.0, 300.0))]
    dim1 = make_dimension(name="dim1", splits={"a": "single", "b": "dual"})
    dim2 = make_dimension(name="dim2", splits={"a": "x", "b": "y"})  # never turned into a topic
    survey = make_survey_report(
        clusters=clusters, dimensions=[dim1, dim2], comparison_specs=["dim1", "dim2"]
    )
    refiner = FakeRefiner([_raw("dim1"), _freetext_raw()])
    answers = [
        _answer(
            dimension_name="dim1",
            gate_answer="persuadable",
            axis_kind="position",
            axis_value=0.5,
            axis_skipped=False,
            became_filter=False,
        ),
        _answer(
            dimension_name=None,
            gate_answer="must_have",
            axis_kind=None,
            axis_value=None,
            axis_skipped=True,
            became_filter=True,
        ),
    ]
    port = FakeQuestionPort(answers)

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == 2
    assert outcome.topics[1].dimension_name is None
    assert outcome.topics[1].became_filter is True
    assert outcome.surviving_clusters == {"a", "b"}  # unchanged by the free-text filter


# -- CAP: runaway backstop -----------------------------------------------


def test_cap_stops_at_eight_topics_with_no_defaults():
    clusters = [make_cluster(key="x"), make_cluster(key="y", price_range_native=(200.0, 300.0))]
    dims = [
        make_dimension(name=f"dim{i}", splits={"x": "px", "y": "py"}) for i in range(10)
    ]
    survey = make_survey_report(
        clusters=clusters, dimensions=dims, comparison_specs=[d.name for d in dims]
    )
    refiner = FakeRefiner([_raw(d.name) for d in dims])
    answers = [
        _answer(
            dimension_name=f"dim{i}",
            gate_answer="no_preference",
            axis_kind="position",
            axis_value=None,
            axis_skipped=True,
            became_filter=False,
        )
        for i in range(REFINE_TOPIC_CAP)
    ]
    # floor=2 -> topics 1-2 ask unconditionally; a bail-out check precedes
    # each of topics 3-8 (6 offers total, always declined here) since
    # nothing ever satisfies (a) or (b) in this all-progress run. The CAP
    # check runs before any of that on the would-be 9th iteration, so a 9th
    # ask_topic call (or a 7th bail-out offer) would raise.
    port = FakeQuestionPort(answers, bailout_script=[False] * 6)

    outcome = run(run_refine(survey, make_intake_answers(), refiner, port))

    assert len(outcome.topics) == REFINE_TOPIC_CAP == 8
    assert len(port.ask_topic_calls) == 8
    assert port.bailout_calls == 6
    assert outcome.caveats == []  # CAP assumes nothing on the user's behalf


# -- _apply_gate: pure function, no port needed --------------------------


def test_apply_gate_must_have_keeps_only_satisfying_clusters():
    dim = make_dimension(name="motor", splits={"a": "single", "b": "dual", "c": "dual"})
    raw = _raw("motor", satisfies=["dual"])
    answer = _answer(dimension_name="motor", gate_answer="must_have", became_filter=True)

    surviving, eliminated = _apply_gate(raw, {"motor": dim}, answer, {"a", "b", "c"})

    assert surviving == {"b", "c"}
    assert eliminated is True


def test_apply_gate_must_avoid_excludes_satisfying_clusters():
    dim = make_dimension(name="motor", splits={"a": "single", "b": "dual", "c": "dual"})
    raw = _raw("motor", satisfies=["dual"])
    answer = _answer(dimension_name="motor", gate_answer="must_avoid", became_filter=True)

    surviving, eliminated = _apply_gate(raw, {"motor": dim}, answer, {"a", "b", "c"})

    assert surviving == {"a"}
    assert eliminated is True


def test_apply_gate_never_eliminates_a_cluster_missing_from_splits():
    dim = make_dimension(name="motor", splits={"a": "single", "b": "dual"})  # "d" absent
    raw = _raw("motor", satisfies=["dual"])
    answer = _answer(dimension_name="motor", gate_answer="must_have", became_filter=True)

    surviving, _ = _apply_gate(raw, {"motor": dim}, answer, {"a", "b", "d"})

    assert "d" in surviving  # never eliminated for lack of data


def test_apply_gate_refuses_a_filter_that_would_zero_out_surviving():
    dim = make_dimension(name="motor", splits={"a": "single", "b": "single"})
    raw = _raw("motor", satisfies=["dual"])  # nothing survives this
    answer = _answer(dimension_name="motor", gate_answer="must_have", became_filter=True)

    surviving, eliminated = _apply_gate(raw, {"motor": dim}, answer, {"a", "b"})

    assert surviving == {"a", "b"}
    assert eliminated is False


def test_apply_gate_free_text_only_topic_is_a_no_op():
    raw = _freetext_raw()
    answer = _answer(dimension_name=None, gate_answer="must_have", became_filter=True)

    surviving, eliminated = _apply_gate(raw, {}, answer, {"a", "b"})

    assert surviving == {"a", "b"}
    assert eliminated is False


# -- _established_lean regression ------------------------------------------


def test_skipped_importance_default_is_never_a_lean():
    """§9.7's exact named confusion: 0.2 <= 0.35 numerically, but a skipped
    axis default must never count as an explicitly-given lean."""
    answer = _answer(
        axis_kind="importance", axis_value=IMPORTANCE_SKIP_DEFAULT, axis_skipped=True
    )
    assert _established_lean(answer) is False


def test_same_value_explicitly_given_is_a_lean():
    answer = _answer(axis_kind="importance", axis_value=0.2, axis_skipped=False)
    assert _established_lean(answer) is True


# -- survey/intake threading sanity ---------------------------------------


def test_survey_and_intake_passed_through_to_refiner_unchanged():
    survey = make_survey_report()
    intake = make_intake_answers()
    refiner = FakeRefiner([])
    port = FakeQuestionPort([])

    run(run_refine(survey, intake, refiner, port))

    assert refiner.calls == [(survey, intake)]
