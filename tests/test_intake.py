"""Unit tests for phases/intake.py — Phase 0 INTAKE (spec docs/handoff.md
§7, build order step 3)."""

import asyncio

import pytest
from pydantic import ValidationError

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.phases.intake import Intake, filter_by_required_features, run_intake
from tests.conftest import make_product, make_sourced_value


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """A CLIQuestionPort fed from a fixed script instead of a real terminal.

    Mirrors test_cli_port.py's helper — both `ask()` and `ask_text()` read
    through the same injected `input_fn`, so a single script can drive a
    multi-question flow like `run_intake`.
    """
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


# -- Intake model validation ----------------------------------------------


def test_current_model_required_when_owns_current():
    with pytest.raises(ValidationError):
        Intake(
            owns_current=True,
            current_model=None,
            budget_mode="no_limit",
            budget_usd=None,
        )


def test_current_model_must_be_none_when_not_owns_current():
    with pytest.raises(ValidationError):
        Intake(
            owns_current=False,
            current_model="Old Widget",
            budget_mode="no_limit",
            budget_usd=None,
        )


def test_budget_usd_required_when_not_no_limit():
    with pytest.raises(ValidationError):
        Intake(
            owns_current=False,
            current_model=None,
            budget_mode="hard_ceiling",
            budget_usd=None,
        )


def test_budget_usd_must_be_none_when_no_limit():
    with pytest.raises(ValidationError):
        Intake(
            owns_current=False,
            current_model=None,
            budget_mode="no_limit",
            budget_usd=500.0,
        )


def test_budget_usd_must_be_positive():
    with pytest.raises(ValidationError):
        Intake(
            owns_current=False,
            current_model=None,
            budget_mode="hard_ceiling",
            budget_usd=0.0,
        )


def test_valid_intake_constructs():
    intake = Intake(
        owns_current=True,
        current_model="Old Widget",
        budget_mode="hard_ceiling",
        budget_usd=500.0,
        required_features=["bluetooth"],
        named_candidates=["Widget Pro"],
    )
    assert intake.owns_current is True
    assert intake.budget_usd == 500.0


# -- run_intake() end to end -----------------------------------------------


def test_new_purchase_no_budget_limit_no_features_no_candidates():
    port, _ = make_port(
        [
            "No, this is a new purchase",
            "No limit",
            "",  # required features
            "",  # named candidates
        ]
    )
    intake = run(run_intake("standing desk", port))
    assert intake.owns_current is False
    assert intake.current_model is None
    assert intake.budget_mode == "no_limit"
    assert intake.budget_usd is None
    assert intake.required_features == []
    assert intake.named_candidates == []


def test_upgrade_path_captures_current_model():
    port, _ = make_port(
        [
            "Yes, I'm upgrading",
            "Old Widget v2",
            "No limit",
            "",
            "",
        ]
    )
    intake = run(run_intake("standing desk", port))
    assert intake.owns_current is True
    assert intake.current_model == "Old Widget v2"


def test_hard_ceiling_budget_reprompts_on_bad_amount():
    port, printed = make_port(
        [
            "No, this is a new purchase",
            "Hard ceiling",
            "not a number",
            "500",
            "",
            "",
        ]
    )
    intake = run(run_intake("standing desk", port))
    assert intake.budget_mode == "hard_ceiling"
    assert intake.budget_usd == 500.0
    assert any("isn't a number" in line for line in printed)


def test_hard_ceiling_budget_reprompts_on_non_positive_amount():
    port, printed = make_port(
        [
            "No, this is a new purchase",
            "Hard ceiling",
            "-10",
            "300",
            "",
            "",
        ]
    )
    intake = run(run_intake("standing desk", port))
    assert intake.budget_usd == 300.0
    assert any("greater than 0" in line for line in printed)


def test_required_features_and_named_candidates_parsed_from_csv():
    port, _ = make_port(
        [
            "No, this is a new purchase",
            "No limit",
            " bluetooth ,  waterproof,,adjustable height ",
            "Widget Pro, Acme Deluxe ,",
        ]
    )
    intake = run(run_intake("standing desk", port))
    assert intake.required_features == ["bluetooth", "waterproof", "adjustable height"]
    assert intake.named_candidates == ["Widget Pro", "Acme Deluxe"]


# -- filter_by_required_features() -----------------------------------------


def test_no_required_features_returns_all_products_unchanged():
    products = [make_product(), make_product(name="Other Widget")]
    assert filter_by_required_features(products, []) is products


def test_matches_on_spec_key():
    product = make_product(
        specs={"Bluetooth": make_sourced_value(value="yes")}
    )
    assert filter_by_required_features([product], ["bluetooth"]) == [product]


def test_matches_on_spec_value():
    product = make_product(
        specs={"connectivity": make_sourced_value(value="Bluetooth 5.0")}
    )
    assert filter_by_required_features([product], ["bluetooth"]) == [product]


def test_match_is_case_insensitive():
    product = make_product(
        specs={"CONNECTIVITY": make_sourced_value(value="BLUETOOTH 5.0")}
    )
    assert filter_by_required_features([product], ["Bluetooth"]) == [product]


def test_product_missing_feature_is_excluded():
    product = make_product(specs={"weight": make_sourced_value(value="42 lb")})
    assert filter_by_required_features([product], ["bluetooth"]) == []


def test_product_must_match_all_required_features():
    product = make_product(
        specs={"connectivity": make_sourced_value(value="Bluetooth 5.0")}
    )
    assert filter_by_required_features([product], ["bluetooth", "waterproof"]) == []
    assert filter_by_required_features([product], ["bluetooth"]) == [product]
