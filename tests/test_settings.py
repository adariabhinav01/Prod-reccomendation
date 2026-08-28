"""Tests for settings.py (spec docs/handoff.md §10.1; build order step 3)."""

import tomllib

import pytest

from product_scout.settings import (
    DisplaySettings,
    LocationSettings,
    Settings,
    SettingsError,
    SourcesSettings,
    _to_toml,
    load,
    save,
    set_value,
)
from tests.conftest import make_settings


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / ".product-scout" / "config.toml"


# -- load(): missing / empty / partial files ---------------------------------


def test_load_missing_file_returns_defaults(config_path):
    settings = load(config_path)
    assert settings.location.has_location is False
    assert settings.location.country is None
    assert settings.location.currency is None
    assert settings.display.output_language == "en"
    assert settings.display.units is None
    assert settings.sources.trusted == []


def test_load_empty_file_returns_defaults(config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("", encoding="utf-8")
    settings = load(config_path)
    assert settings == Settings()


def test_load_partial_file_tolerates_missing_keys(config_path):
    # §7.1: intake must be able to tell location is still "missing" even if
    # a previous partial write only recorded country, not currency.
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[location]\ncountry = "US"\n', encoding="utf-8")
    settings = load(config_path)
    assert settings.location.country == "US"
    assert settings.location.currency is None
    assert settings.location.has_location is False
    assert settings.display == DisplaySettings()
    assert settings.sources.trusted == []


# -- load(): error paths ------------------------------------------------------


def test_load_malformed_toml_raises_settings_error(config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("not = [valid toml", encoding="utf-8")
    with pytest.raises(SettingsError):
        load(config_path)


def test_load_wrong_type_raises_settings_error(config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[location]\ncountry = 42\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load(config_path)


def test_load_unknown_table_raises_settings_error(config_path):
    # extra="forbid": house style is "validate hard, fail loudly" — a
    # typo'd table (e.g. [locaton]) must not silently vanish.
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[foo]\nbar = "baz"\n', encoding="utf-8")
    with pytest.raises(SettingsError):
        load(config_path)


def test_load_unknown_key_in_known_table_raises_settings_error(config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[location]\nzipcode = "12345"\n', encoding="utf-8")
    with pytest.raises(SettingsError):
        load(config_path)


# -- save() / round-trip -------------------------------------------------------


def test_save_then_load_round_trips(config_path):
    settings = make_settings()
    save(settings, config_path)
    loaded = load(config_path)
    assert loaded == settings


def test_save_creates_parent_directory(config_path):
    assert not config_path.parent.exists()
    save(make_settings(), config_path)
    assert config_path.exists()


def test_save_is_atomic_no_tmp_file_left_behind(config_path):
    save(make_settings(), config_path)
    tmp_path = config_path.with_suffix(".toml.tmp")
    assert not tmp_path.exists()


# -- _to_toml() serialization --------------------------------------------------


def test_to_toml_only_emits_present_optional_fields():
    settings = make_settings(location=LocationSettings(country="US", currency=None))
    text = _to_toml(settings)
    assert "currency" not in text


def test_to_toml_always_emits_sources_trusted_even_when_empty():
    text = _to_toml(make_settings())
    assert "trusted = []" in text


def test_to_toml_orders_tables_location_display_sources():
    text = _to_toml(make_settings())
    assert text.index("[location]") < text.index("[display]") < text.index("[sources]")


def test_to_toml_quotes_and_escapes_string_values():
    settings = make_settings(
        sources=SourcesSettings(trusted=['https://example.com/"quoted"\\path'])
    )
    text = _to_toml(settings)
    parsed = tomllib.loads(text)
    assert parsed["sources"]["trusted"] == ['https://example.com/"quoted"\\path']


# -- set_value() ---------------------------------------------------------------


def test_set_value_updates_location_country():
    original = make_settings()
    updated = set_value(original, "location.country", "DE")
    assert updated.location.country == "DE"
    assert original.location.country == "US"  # immutability


def test_set_value_updates_display_units_valid_literal():
    updated = set_value(make_settings(), "display.units", "metric")
    assert updated.display.units == "metric"


def test_set_value_rejects_invalid_display_units_literal():
    with pytest.raises(SettingsError):
        set_value(make_settings(), "display.units", "furlongs")


def test_set_value_rejects_unknown_table():
    with pytest.raises(SettingsError):
        set_value(make_settings(), "bogus.field", "x")


def test_set_value_rejects_unknown_field_in_known_table():
    with pytest.raises(SettingsError):
        set_value(make_settings(), "location.zipcode", "x")


def test_set_value_rejects_sources_trusted():
    with pytest.raises(SettingsError, match="not settable"):
        set_value(make_settings(), "sources.trusted", "https://example.com")


@pytest.mark.parametrize(
    "bad_key", ["location", "location.country.extra", "", "location."]
)
def test_set_value_rejects_malformed_dotted_key(bad_key):
    with pytest.raises(SettingsError):
        set_value(make_settings(), bad_key, "x")


# -- LocationSettings.has_location ---------------------------------------------


def test_has_location_false_when_only_country_set():
    settings = make_settings(location=LocationSettings(country="US", currency=None))
    assert settings.location.has_location is False


def test_has_location_true_when_both_set():
    assert make_settings().location.has_location is True
