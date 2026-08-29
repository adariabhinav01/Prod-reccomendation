"""Unit tests for `WebQuestionPort` (web build order W2). Drives the
future-based hold directly — resolve via `submit_answer`/`submit_bailout`,
never through a real HTTP layer — same discipline `test_cli_port.py` uses
for `CLIQuestionPort` (fake `input_fn`/`print_fn` instead of a real
terminal). No `pytest-asyncio`: async code is driven with a local
`run(coro)` helper wrapping `asyncio.run`, matching every other async test
file in this suite.

Priority order matches the spec's own instruction: the §2.1 skip-vs-
explicit-value distinction comes first.
"""

import asyncio

import pytest

from product_scout.io.port import (
    GATE_ANSWER_LABELS,
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
    QuestionPort,
)
from product_scout.io.web_port import (
    PendingQuestion,
    WebPortValidationError,
    WebQuestionPort,
    _classify_progress,
)
from tests.conftest import make_axis_spec, make_topic_prompt


def run(coro):
    return asyncio.run(coro)


def test_web_port_satisfies_question_port():
    assert isinstance(WebQuestionPort(), QuestionPort)


# -- §2.1: skip-vs-explicit-value, position and importance axes ------------
#
# The two paths must never alias: skipping must produce the named default
# with axis_skipped=True, and an explicit mid-scale value must produce the
# *same number* with axis_skipped=False — proving the flag, not the value,
# is what the port trusts.


def test_position_axis_skip_uses_named_default_and_sets_skipped_flag():
    topic = make_topic_prompt(axis=make_axis_spec(kind="position"))
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)  # let ask_topic park on the future
        port.submit_answer({"gate_answer": "persuadable", "axis_skipped": True, "free_text": ""})
        return await task

    answer = run(scenario())
    assert answer.axis_value == POSITION_SKIP_DEFAULT
    assert answer.axis_skipped is True


def test_importance_axis_skip_uses_named_default_and_sets_skipped_flag():
    topic = make_topic_prompt(axis=make_axis_spec(kind="importance"))
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer({"gate_answer": "persuadable", "axis_skipped": True, "free_text": ""})
        return await task

    answer = run(scenario())
    assert answer.axis_value == IMPORTANCE_SKIP_DEFAULT
    assert answer.axis_skipped is True


def test_explicit_midscale_axis_value_is_not_aliased_to_the_skip_default():
    """A raw range input defaults to its midpoint (5 out of 0-10) even when
    untouched — this is exactly why axis_skipped must be trusted over the
    numeric value. An explicit 5.0 must store 0.5 with axis_skipped=False,
    distinctly from a skip (which also happens to default to 0.5 for a
    position axis) — same number, different flag, both must be exact."""
    topic = make_topic_prompt(axis=make_axis_spec(kind="position"))
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer(
            {"gate_answer": "persuadable", "axis_skipped": False, "axis_value": 5.0, "free_text": ""}
        )
        return await task

    answer = run(scenario())
    assert answer.axis_value == 0.5
    assert answer.axis_skipped is False


def test_explicit_axis_value_converts_0_to_10_display_scale_to_0_to_1_stored():
    topic = make_topic_prompt(axis=make_axis_spec(kind="importance"))
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer(
            {"gate_answer": "persuadable", "axis_skipped": False, "axis_value": 8.0, "free_text": ""}
        )
        return await task

    answer = run(scenario())
    assert answer.axis_value == pytest.approx(0.8)
    assert answer.axis_skipped is False


def test_out_of_range_axis_value_is_rejected_and_future_stays_resolvable():
    topic = make_topic_prompt(axis=make_axis_spec(kind="position"))
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        with pytest.raises(WebPortValidationError):
            port.submit_answer(
                {"gate_answer": "persuadable", "axis_skipped": False, "axis_value": 11.0}
            )
        # The future must still be open — a bad POST leaves the question
        # "simply re-asked" (§2), not stuck or silently resolved.
        port.submit_answer({"gate_answer": "persuadable", "axis_skipped": True})
        return await task

    answer = run(scenario())
    assert answer.axis_skipped is True


def test_no_axis_on_topic_forces_skip_regardless_of_client_payload():
    """§2: the port decides from its own TopicPrompt, never from what the
    client claims — a payload that tries to assert an axis value for a
    topic with no axis at all is ignored, not honored."""
    topic = make_topic_prompt(axis=None)
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer(
            {"gate_answer": "persuadable", "axis_skipped": False, "axis_value": 7.0, "free_text": ""}
        )
        return await task

    answer = run(scenario())
    assert answer.axis_kind is None
    assert answer.axis_value is None
    assert answer.axis_skipped is True


# -- gate validation ---------------------------------------------------------


def test_must_have_gate_short_circuits_to_a_filter_without_asking_axis_or_text():
    topic = make_topic_prompt()
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer({"gate_answer": "must_have"})
        return await task

    answer = run(scenario())
    assert answer.became_filter is True
    assert answer.gate_answer == "must_have"
    assert answer.axis_value is None
    assert answer.free_text == ""


def test_missing_gate_answer_defaults_to_persuadable_with_a_logged_note():
    topic = make_topic_prompt(axis=None)
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        port.submit_answer({})
        return await task

    answer = run(scenario())
    assert answer.gate_answer == "persuadable"
    assert answer.assumption_logged is not None


def test_unknown_gate_answer_is_rejected():
    topic = make_topic_prompt()
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_topic(topic))
        await asyncio.sleep(0)
        with pytest.raises(WebPortValidationError):
            port.submit_answer({"gate_answer": "not_a_real_answer"})
        port.submit_answer({"gate_answer": "must_avoid"})
        return await task

    answer = run(scenario())
    assert answer.gate_answer == "must_avoid"


def test_gate_answer_labels_cover_all_four_literals():
    assert set(GATE_ANSWER_LABELS) == {"must_have", "must_avoid", "persuadable", "no_preference"}


# -- ask_choice ---------------------------------------------------------------


def test_ask_choice_accepts_an_offered_option():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_choice("Q?", ["a", "b"], "hatch"))
        await asyncio.sleep(0)
        port.submit_answer({"answer": "b"})
        return await task

    assert run(scenario()) == "b"


def test_ask_choice_accepts_the_escape_hatch():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_choice("Q?", ["a", "b"], "hatch"))
        await asyncio.sleep(0)
        port.submit_answer({"answer": "hatch"})
        return await task

    assert run(scenario()) == "hatch"


def test_ask_choice_rejects_a_value_the_client_invents():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_choice("Q?", ["a", "b"], "hatch"))
        await asyncio.sleep(0)
        with pytest.raises(WebPortValidationError):
            port.submit_answer({"answer": "made-up"})
        port.submit_answer({"answer": "a"})
        return await task

    assert run(scenario()) == "a"


def test_ask_choice_requires_at_least_two_options():
    port = WebQuestionPort()
    with pytest.raises(ValueError):
        run(port.ask_choice("Q?", ["only-one"], "hatch"))


# -- offer_bailout --------------------------------------------------------------


def test_offer_bailout_true():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.offer_bailout())
        await asyncio.sleep(0)
        port.submit_bailout()
        return await task

    assert run(scenario()) is True


def test_offer_bailout_false_via_submit_answer():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.offer_bailout())
        await asyncio.sleep(0)
        port.submit_answer({"accept": False})
        return await task

    assert run(scenario()) is False


def test_submit_bailout_rejected_when_no_bailout_is_pending():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_text("What model do you own?"))
        await asyncio.sleep(0)
        with pytest.raises(WebPortValidationError):
            port.submit_bailout()
        port.submit_answer({"answer": "none"})
        return await task

    assert run(scenario()) == "none"


# -- ask_text -------------------------------------------------------------------


def test_ask_text_round_trip():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_text("What model do you own?"))
        await asyncio.sleep(0)
        port.submit_answer({"answer": "Widget Pro"})
        return await task

    assert run(scenario()) == "Widget Pro"


def test_ask_text_rejects_oversized_answer():
    port = WebQuestionPort()

    async def scenario():
        task = asyncio.ensure_future(port.ask_text("Anything else?"))
        await asyncio.sleep(0)
        with pytest.raises(WebPortValidationError):
            port.submit_answer({"answer": "x" * 3000})
        port.submit_answer({"answer": "short"})
        return await task

    assert run(scenario()) == "short"


# -- submit_answer with nothing pending ------------------------------------------


def test_submit_answer_with_nothing_pending_is_rejected():
    port = WebQuestionPort()
    with pytest.raises(WebPortValidationError):
        port.submit_answer({"answer": "anything"})


def test_pending_reflects_current_question_kind():
    port = WebQuestionPort()
    assert port.pending is None

    async def scenario():
        task = asyncio.ensure_future(port.ask_text("Q?"))
        await asyncio.sleep(0)
        assert isinstance(port.pending, PendingQuestion)
        assert port.pending.kind == "text"
        port.submit_answer({"answer": "a"})
        await task
        assert port.pending is None

    run(scenario())


# -- on_pending_changed hook (used by web/registry.py, W3) -----------------------


def test_on_pending_changed_fires_on_park_and_clear():
    events: list[bool] = []
    port = WebQuestionPort(on_pending_changed=lambda pq: events.append(pq is not None))

    async def scenario():
        task = asyncio.ensure_future(port.ask_text("Q?"))
        await asyncio.sleep(0)
        port.submit_answer({"answer": "a"})
        await task

    run(scenario())
    assert events == [True, False]


# -- report_progress classification ----------------------------------------------


@pytest.mark.parametrize(
    "phase",
    ["INTAKE", "SURVEY", "REFINE", "EXTRACTION", "TIMING", "PRIOR-GEN", "SCORING", "SYNTHESIS", "RENDER"],
)
def test_classify_progress_recognizes_every_phase_label(phase):
    assert _classify_progress(phase) == {"type": "phase_entered", "phase": phase}


def test_classify_progress_recognizes_fetch_tick():
    event = _classify_progress("  fetched https://example.com/product")
    assert event == {"type": "tick", "text": "fetched https://example.com/product"}


def test_classify_progress_recognizes_search_tick():
    event = _classify_progress("  searched: best standing desks 2026")
    assert event == {"type": "tick", "text": "searched: best standing desks 2026"}


def test_classify_progress_recognizes_phase_result_line():
    event = _classify_progress("  [ok] 6 turns, terminal_reason=completed")
    assert event == {"type": "phase_result", "text": "[ok] 6 turns, terminal_reason=completed"}


def test_classify_progress_falls_through_unclassified_text_to_generic_tick():
    event = _classify_progress("some other status text")
    assert event == {"type": "tick", "text": "some other status text"}


def test_report_progress_appends_to_the_in_memory_log():
    port = WebQuestionPort()
    run(port.report_progress("SURVEY"))
    run(port.report_progress("  fetched https://example.com"))
    assert port.progress_log == ["SURVEY", "  fetched https://example.com"]


# -- on_progress callback (used by web/registry.py's SSE fan-out, W4) ------------


def test_on_progress_fires_with_classified_events():
    events: list[dict] = []
    port = WebQuestionPort(on_progress=events.append)
    run(port.report_progress("SURVEY"))
    run(port.report_progress("  fetched https://example.com"))
    assert events == [
        {"type": "phase_entered", "phase": "SURVEY"},
        {"type": "tick", "text": "fetched https://example.com"},
    ]


def test_on_progress_is_optional_and_progress_log_still_fills():
    port = WebQuestionPort()  # no on_progress
    run(port.report_progress("SURVEY"))
    assert port.progress_log == ["SURVEY"]
