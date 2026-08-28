"""Unit tests for CLIQuestionPort (spec docs/handoff.md §3.4, §9)."""

import asyncio

import pytest

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.io.port import (
    ESCAPE_HATCH_NOT_SURE,
    ESCAPE_HATCH_NO_PREFERENCE,
    QuestionPort,
)
from tests.conftest import make_axis_spec, make_topic_prompt


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """A CLIQuestionPort fed from a fixed script instead of a real terminal."""
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


# -- protocol conformance ----------------------------------------------------


def test_cli_port_satisfies_question_port():
    port, _ = make_port([])
    assert isinstance(port, QuestionPort)


# -- ask_choice: basic selection ----------------------------------------


def test_numbered_selection_returns_option_text():
    port, _ = make_port(["2"])
    answer = run(port.ask_choice("Speed or battery?", ["Speed", "Battery"], "N/A"))
    assert answer == "Battery"


def test_text_selection_is_case_insensitive():
    port, _ = make_port(["speed"])
    answer = run(port.ask_choice("Speed or battery?", ["Speed", "Battery"], "N/A"))
    assert answer == "Speed"


def test_invalid_input_reprompts_until_valid():
    port, printed = make_port(["0", "abc", "1"])
    answer = run(port.ask_choice("Speed or battery?", ["Speed", "Battery"], "N/A"))
    assert answer == "Speed"
    assert sum("Please enter a number" in line for line in printed) == 2


def test_rejects_fewer_than_two_options():
    port, _ = make_port([])
    with pytest.raises(ValueError):
        run(port.ask_choice("Q?", ["only one"], "hatch"))


# -- ask_choice: escape hatch is a bare string, appended as the last option --


def test_escape_hatch_is_appended_as_last_option():
    port, printed = make_port(["3"])
    run(port.ask_choice("Which matters more?", ["Speed", "Battery"], ESCAPE_HATCH_NOT_SURE))
    menu_lines = [line for line in printed if line.strip().startswith(("1.", "2.", "3."))]
    assert menu_lines[-1] == f"  3. {ESCAPE_HATCH_NOT_SURE}"


def test_picking_escape_hatch_by_text_returns_it_verbatim():
    port, _ = make_port([ESCAPE_HATCH_NOT_SURE])
    answer = run(port.ask_choice("Which matters more?", ["Speed", "Battery"], ESCAPE_HATCH_NOT_SURE))
    assert answer == ESCAPE_HATCH_NOT_SURE


def test_picking_escape_hatch_by_number_returns_it_verbatim():
    port, _ = make_port(["3"])
    answer = run(port.ask_choice("Which matters more?", ["Speed", "Battery"], ESCAPE_HATCH_NOT_SURE))
    assert answer == ESCAPE_HATCH_NOT_SURE


# -- §9.6: the port is stateless — two calls, two different hatches ----------


def test_port_renders_two_independent_hatches_across_two_calls():
    """§9.6: the two-attempt swap lives in the CALLER (REFINE), not the
    port. Proven here by calling ask_choice twice with different hatch
    strings on the SAME port instance and confirming neither call leaks
    state into the other."""
    port, printed = make_port([ESCAPE_HATCH_NOT_SURE, "1"])

    first = run(
        port.ask_choice("Dual motor or single?", ["Dual motor", "Single motor"], ESCAPE_HATCH_NOT_SURE)
    )
    assert first == ESCAPE_HATCH_NOT_SURE

    # Phase code would call Opus for a grounded explanation here, then
    # re-ask the SAME question with the hatch swapped. The port doesn't
    # care this is "attempt 2" — no counter, no memory of the first call.
    second = run(
        port.ask_choice(
            "Dual motor or single?", ["Dual motor", "Single motor"], ESCAPE_HATCH_NO_PREFERENCE
        )
    )
    assert second == "Dual motor"

    menu_lines = [line for line in printed if line.strip().startswith(("1.", "2.", "3."))]
    assert menu_lines[-1] == f"  3. {ESCAPE_HATCH_NO_PREFERENCE}"
    assert not any(ESCAPE_HATCH_NOT_SURE in line for line in menu_lines[3:])


# -- ask_topic: §9.1 gate stage — must_have/must_avoid short-circuits --------


def test_gate_must_have_becomes_filter_and_skips_stage_two():
    port, printed = make_port(["1"])  # 1 = must_have
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.gate_answer == "must_have"
    assert answer.became_filter is True
    assert answer.axis_kind is None
    assert answer.axis_value is None
    assert answer.axis_skipped is True
    assert answer.free_text == ""
    assert answer.assumption_logged is None
    # Only the gate menu should have printed — no axis or free-text prompt.
    assert not any("scale of 0-10" in line for line in printed)


def test_gate_must_avoid_becomes_filter():
    port, _ = make_port(["2"])  # 2 = must_avoid
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.gate_answer == "must_avoid"
    assert answer.became_filter is True


def test_gate_persuadable_proceeds_to_stage_two():
    port, _ = make_port(["3", "7", "no thanks"])  # gate=persuadable, axis=7, free text
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.gate_answer == "persuadable"
    assert answer.became_filter is False


def test_gate_no_preference_proceeds_to_stage_two():
    port, _ = make_port(["4", "", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.gate_answer == "no_preference"
    assert answer.became_filter is False


# -- ask_topic: gate skip defaults to persuadable, logged (§9.6) ------------


def test_gate_skip_defaults_to_persuadable_and_logs_assumption():
    port, _ = make_port(["", "", ""])  # skip gate, skip axis, skip free text
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.gate_answer == "persuadable"
    assert answer.assumption_logged is not None
    assert "persuadable" in answer.assumption_logged


# -- ask_topic: axis skip defaults are asymmetric (§9.6) ---------------------


def test_position_axis_skip_defaults_to_0_5_and_logs_balanced():
    port, _ = make_port(["3", "", ""])  # persuadable, skip axis, skip free text
    topic = make_topic_prompt(axis=make_axis_spec(kind="position"))
    answer = run(port.ask_topic(topic))
    assert answer.axis_kind == "position"
    assert answer.axis_value == 0.5
    assert answer.axis_skipped is True
    assert "balanced" in answer.assumption_logged


def test_importance_axis_skip_defaults_to_0_2_and_logs_light_weight():
    port, _ = make_port(["3", "", ""])
    topic = make_topic_prompt(axis=make_axis_spec(kind="importance", low_label="doesn't matter"))
    answer = run(port.ask_topic(topic))
    assert answer.axis_kind == "importance"
    assert answer.axis_value == 0.2
    assert answer.axis_skipped is True
    assert "weighted it lightly" in answer.assumption_logged


def test_axis_defaults_are_asymmetric_not_both_0_5():
    """The load-bearing assertion: position and importance skips must NOT
    produce the same default — that's the entire point of §9.6's table."""
    port_a, _ = make_port(["3", "", ""])
    position_answer = run(
        port_a.ask_topic(make_topic_prompt(axis=make_axis_spec(kind="position")))
    )
    port_b, _ = make_port(["3", "", ""])
    importance_answer = run(
        port_b.ask_topic(make_topic_prompt(axis=make_axis_spec(kind="importance")))
    )
    assert position_answer.axis_value != importance_answer.axis_value
    assert position_answer.axis_value == 0.5
    assert importance_answer.axis_value == 0.2


# -- ask_topic: explicit axis answer, 0-10 -> 0.0-1.0 conversion -------------


def test_explicit_axis_value_converts_scale_correctly():
    port, _ = make_port(["3", "7", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.axis_value == pytest.approx(0.7)
    assert answer.axis_skipped is False


def test_axis_endpoints_convert_correctly():
    port, _ = make_port(["3", "0", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.axis_value == 0.0

    port2, _ = make_port(["3", "10", ""])
    answer2 = run(port2.ask_topic(make_topic_prompt()))
    assert answer2.axis_value == 1.0


def test_axis_out_of_range_reprompts():
    port, printed = make_port(["3", "15", "-1", "5", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.axis_value == 0.5
    assert sum("Please enter a number from 0 to 10" in line for line in printed) == 2


def test_axis_non_numeric_reprompts():
    port, printed = make_port(["3", "banana", "5", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.axis_value == 0.5
    assert any("Please enter a number from 0 to 10" in line for line in printed)


def test_no_axis_on_topic_skips_axis_stage_entirely():
    port, printed = make_port(["3", "some free text"])
    answer = run(port.ask_topic(make_topic_prompt(axis=None)))
    assert answer.axis_kind is None
    assert answer.axis_value is None
    assert answer.axis_skipped is True
    assert answer.assumption_logged is None
    assert not any("scale of 0-10" in line for line in printed)


# -- ask_topic: free text is always present, never logged when skipped ------


def test_free_text_skip_is_empty_and_not_logged():
    port, _ = make_port(["3", "5", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.free_text == ""
    # §9.6: free-text skip is NOT logged (unlike gate/axis skips).
    assert answer.assumption_logged is None


def test_free_text_is_captured_and_stripped():
    port, _ = make_port(["3", "5", "  quiet operation matters most  "])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert answer.free_text == "quiet operation matters most"


# -- ask_topic: both gate and axis skipped -> both notes combined -----------


def test_gate_and_axis_skip_notes_are_both_present():
    port, _ = make_port(["", "", ""])
    answer = run(port.ask_topic(make_topic_prompt()))
    assert "persuadable" in answer.assumption_logged
    assert "balanced" in answer.assumption_logged


# -- ask_topic: dimension_name/topic pass through unchanged -----------------


def test_topic_answer_carries_dimension_name_and_topic_through():
    port, _ = make_port(["3", "5", ""])
    topic = make_topic_prompt(topic="noise level", dimension_name="noise")
    answer = run(port.ask_topic(topic))
    assert answer.topic == "noise level"
    assert answer.dimension_name == "noise"


# -- offer_bailout: §9.7a -----------------------------------------------


@pytest.mark.parametrize("raw,expected", [("y", True), ("Y", True), ("yes", True), ("YES", True)])
def test_offer_bailout_accepts_yes_variants(raw, expected):
    port, _ = make_port([raw])
    assert run(port.offer_bailout()) is expected


@pytest.mark.parametrize("raw,expected", [("n", False), ("no", False), ("", False), ("maybe", False)])
def test_offer_bailout_defaults_to_no(raw, expected):
    port, _ = make_port([raw])
    assert run(port.offer_bailout()) is expected
