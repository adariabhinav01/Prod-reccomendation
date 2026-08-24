"""Unit tests for CLIQuestionPort — the §9 two-attempt escape-hatch swap."""

import asyncio

import pytest

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.io.port import QuestionPort


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


# -- basic selection -----------------------------------------------------


def test_numbered_selection_returns_option_text():
    port, _ = make_port(["2"])
    answer = run(port.ask("Speed or battery?", ["Speed", "Battery"]))
    assert answer == "Battery"


def test_text_selection_is_case_insensitive():
    port, _ = make_port(["speed"])
    answer = run(port.ask("Speed or battery?", ["Speed", "Battery"]))
    assert answer == "Speed"


def test_invalid_input_reprompts_until_valid():
    port, printed = make_port(["0", "abc", "1"])
    answer = run(port.ask("Speed or battery?", ["Speed", "Battery"]))
    assert answer == "Speed"
    assert sum("Please enter a number" in line for line in printed) == 2


# -- §3: options must be 2-4 --------------------------------------------


def test_rejects_fewer_than_two_options():
    port, _ = make_port([])
    with pytest.raises(ValueError):
        run(port.ask("Q?", ["only one"]))


def test_rejects_more_than_four_options():
    port, _ = make_port([])
    with pytest.raises(ValueError):
        run(port.ask("Q?", ["a", "b", "c", "d", "e"]))


def test_rejects_unknown_escape_hatch():
    port, _ = make_port([])
    with pytest.raises(ValueError):
        run(port.ask("Q?", ["a", "b"], escape_hatch="maybe"))  # type: ignore[arg-type]


# -- §9: attempt 1 — "not sure" hatch ------------------------------------


def test_not_sure_hatch_is_appended_as_last_option():
    port, printed = make_port(["3"])
    run(port.ask("Which matters more?", ["Speed", "Battery"], escape_hatch="not_sure"))
    menu_lines = [line for line in printed if line.strip().startswith(("1.", "2.", "3."))]
    assert menu_lines[-1] == "  3. Not sure — explain what this changes"


def test_picking_not_sure_returns_sentinel_not_label():
    port, _ = make_port(["Not sure — explain what this changes"])
    answer = run(port.ask("Which matters more?", ["Speed", "Battery"], escape_hatch="not_sure"))
    assert answer == "not_sure"


def test_picking_not_sure_by_number_returns_sentinel():
    port, _ = make_port(["3"])
    answer = run(port.ask("Which matters more?", ["Speed", "Battery"], escape_hatch="not_sure"))
    assert answer == "not_sure"


# -- §9: attempt 2 — "no preference" hatch, "not sure" withdrawn ---------


def test_no_preference_hatch_replaces_not_sure_on_reask():
    port, printed = make_port(["3"])
    run(
        port.ask(
            "Which matters more?",
            ["Speed", "Battery"],
            escape_hatch="no_preference",
        )
    )
    menu_lines = [line for line in printed if line.strip().startswith(("1.", "2.", "3."))]
    assert menu_lines[-1] == "  3. No preference — pick a sensible default for me"
    assert not any("Not sure" in line for line in menu_lines)


def test_picking_no_preference_returns_sentinel_not_label():
    port, _ = make_port(["No preference — pick a sensible default for me"])
    answer = run(
        port.ask("Which matters more?", ["Speed", "Battery"], escape_hatch="no_preference")
    )
    assert answer == "no_preference"


# -- escape_hatch="none" — used outside the §9 loop (e.g. Phase 0 intake) --


def test_none_hatch_appends_nothing():
    port, printed = make_port(["1"])
    run(port.ask("Do you already own one?", ["Yes", "No"], escape_hatch="none"))
    menu_lines = [line for line in printed if line.strip().startswith(("1.", "2.", "3."))]
    assert menu_lines == ["  1. Yes", "  2. No"]


def test_none_hatch_leaves_only_real_options_selectable():
    # Only two real options exist; "3" is out of range and must reprompt.
    port, _ = make_port(["3", "2"])
    answer = run(port.ask("Do you already own one?", ["Yes", "No"], escape_hatch="none"))
    assert answer == "No"


# -- full §9 round trip: attempt 1 escape -> attempt 2 answer -------------


def test_full_not_sure_then_no_preference_round_trip():
    port, _ = make_port(["Not sure — explain what this changes"])
    first = run(
        port.ask("Dual motor or single?", ["Dual motor", "Single motor"], escape_hatch="not_sure")
    )
    assert first == "not_sure"

    # Phase code would call Opus for a grounded explanation here, then
    # re-ask the SAME question with the hatch swapped — the port doesn't
    # care that this is "attempt 2", it just renders what it's told to.
    port2, _ = make_port(["No preference — pick a sensible default for me"])
    second = run(
        port2.ask(
            "Dual motor or single?",
            ["Dual motor", "Single motor"],
            escape_hatch="no_preference",
        )
    )
    assert second == "no_preference"


# -- ask_text — free-form input, no options (§7, Phase 0 intake) --------


def test_ask_text_returns_stripped_input():
    port, _ = make_port(["  500  "])
    answer = run(port.ask_text("What's the dollar amount?"))
    assert answer == "500"


def test_ask_text_prints_the_prompt():
    port, printed = make_port(["some answer"])
    run(port.ask_text("What's the current model you own?"))
    assert "What's the current model you own?" in printed


def test_ask_text_allows_empty_string():
    port, _ = make_port([""])
    answer = run(port.ask_text("Any required features?"))
    assert answer == ""
