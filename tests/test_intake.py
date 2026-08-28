"""Unit tests for phases/intake.py — Phase 0 INTAKE (spec docs/handoff.md
§7, build order step 4)."""

import asyncio

import pytest

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.models import Location
from product_scout.phases.intake import filter_by_required_features, run_intake
from product_scout.settings import (
    LocationSettings,
    Settings,
    load as load_settings,
    save as save_settings,
)
from tests.conftest import make_product, make_sourced_value


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """A CLIQuestionPort fed from a fixed script instead of a real terminal."""
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


@pytest.fixture
def isolated_settings_path(tmp_path):
    return tmp_path / ".product-scout" / "config.toml"


# -- run_intake(): full flow, location missing ------------------------------


def test_new_purchase_no_budget_limit_no_features_no_candidates_location_missing(
    isolated_settings_path,
):
    port, _ = make_port(
        [
            "no",
            "no limit",
            "",  # required features
            "",  # named candidates
            "US",
            "USD",
        ]
    )
    intake, location = run(
        run_intake("standing desk", port, settings_path=isolated_settings_path)
    )
    assert intake.owns_current_version is False
    assert intake.current_model is None
    assert intake.budget_ceiling is None
    assert intake.required_features == []
    assert intake.candidates_under_consideration == []
    assert location == Location(country="US", currency="USD")
    assert isolated_settings_path.exists()


def test_location_already_in_settings_is_not_asked(isolated_settings_path):
    save_settings(
        Settings(location=LocationSettings(country="DE", currency="EUR")),
        isolated_settings_path,
    )
    # Only 4 answers scripted — if location were asked, run_intake would
    # raise StopIteration trying to pull a 5th/6th input.
    port, _ = make_port(["no", "no limit", "", ""])
    intake, location = run(
        run_intake("standing desk", port, settings_path=isolated_settings_path)
    )
    assert location == Location(country="DE", currency="EUR")
    assert intake.owns_current_version is False


def test_location_capture_writes_back_to_settings(isolated_settings_path):
    port, _ = make_port(["no", "no limit", "", "", "gb", "gbp"])
    run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    settings = load_settings(isolated_settings_path)
    assert settings.location.country == "GB"
    assert settings.location.currency == "GBP"


def test_location_capture_reprompts_on_blank_country(isolated_settings_path):
    port, printed = make_port(["no", "no limit", "", "", "", "US", "USD"])
    intake, location = run(
        run_intake("standing desk", port, settings_path=isolated_settings_path)
    )
    assert location == Location(country="US", currency="USD")
    assert any("can't be blank" in line for line in printed)


# -- run_intake(): upgrade path ----------------------------------------------


def test_upgrade_path_captures_current_model(isolated_settings_path):
    port, _ = make_port(
        ["yes", "Old Widget v2", "no limit", "", "", "US", "USD"]
    )
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.owns_current_version is True
    assert intake.current_model == "Old Widget v2"


def test_ownership_invalid_input_reprompts(isolated_settings_path):
    port, printed = make_port(
        ["maybe", "no", "no limit", "", "", "US", "USD"]
    )
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.owns_current_version is False
    assert any("please answer yes or no" in line for line in printed)


# -- run_intake(): budget parsing (§7 item 2) --------------------------------


def test_budget_single_figure_parses_ceiling(isolated_settings_path):
    port, _ = make_port(["no", "under $220", "", "", "US", "USD"])
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.budget_ceiling == 220.0
    assert intake.budget_note == "under $220"


def test_budget_tiered_statement_uses_highest_figure(isolated_settings_path):
    raw = "under $220 for something good, $150 for adequate"
    port, _ = make_port(["no", raw, "", "", "US", "USD"])
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.budget_ceiling == 220.0
    assert intake.budget_note == raw


def test_budget_no_limit_statement_parses_to_none_ceiling(isolated_settings_path):
    port, _ = make_port(["no", "no limit", "", "", "US", "USD"])
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.budget_ceiling is None
    assert intake.budget_note == "no limit"


def test_budget_unparseable_input_reprompts(isolated_settings_path):
    port, printed = make_port(
        ["no", "what even is money", "500", "", "", "US", "USD"]
    )
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.budget_ceiling == 500.0
    assert any("didn't give me a number" in line for line in printed)


# -- run_intake(): required features / candidates (§7 items 3-4) ------------


def test_required_features_and_candidates_parsed_from_csv(isolated_settings_path):
    port, _ = make_port(
        [
            "no",
            "no limit",
            " bluetooth ,  waterproof,,adjustable height ",
            "Widget Pro, Acme Deluxe ,",
            "US",
            "USD",
        ]
    )
    intake, _ = run(run_intake("standing desk", port, settings_path=isolated_settings_path))
    assert intake.required_features == ["bluetooth", "waterproof", "adjustable height"]
    assert intake.candidates_under_consideration == ["Widget Pro", "Acme Deluxe"]


# -- filter_by_required_features() -------------------------------------------


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
