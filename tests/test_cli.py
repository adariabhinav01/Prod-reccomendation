"""Tests for cli.py (spec docs/handoff.md §16; build order step 3).

Only the `config` subcommand is wired at this step — research/rescore/
history/eval land in their own future build steps.
"""

import tomllib

import pytest

from product_scout.cli import main
from product_scout.settings import LocationSettings, Settings, load, save


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / ".product-scout" / "config.toml"
    monkeypatch.setattr("product_scout.settings.DEFAULT_CONFIG_PATH", path)
    return path


def test_config_set_writes_new_value(isolated_config):
    exit_code = main(["config", "set", "location.country", "DE"])
    assert exit_code == 0
    assert load(isolated_config).location.country == "DE"


def test_config_set_preserves_other_fields(isolated_config):
    save(Settings(location=LocationSettings(country="US", currency="USD")), isolated_config)
    main(["config", "set", "location.country", "DE"])
    settings = load(isolated_config)
    assert settings.location.country == "DE"
    assert settings.location.currency == "USD"


def test_config_set_unknown_key_returns_nonzero_and_prints_error(isolated_config, capsys):
    exit_code = main(["config", "set", "bogus.key", "x"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "bogus.key" in err


def test_config_set_invalid_units_returns_nonzero(isolated_config):
    exit_code = main(["config", "set", "display.units", "furlongs"])
    assert exit_code == 1


def test_missing_subcommand_exits_via_argparse(isolated_config):
    with pytest.raises(SystemExit):
        main([])


def test_config_missing_set_exits_via_argparse(isolated_config):
    with pytest.raises(SystemExit):
        main(["config"])


def test_config_set_round_trips_through_real_file_format(isolated_config):
    main(["config", "set", "location.country", "DE"])
    main(["config", "set", "location.currency", "EUR"])
    raw = tomllib.loads(isolated_config.read_text(encoding="utf-8"))
    assert raw["location"]["country"] == "DE"
    assert raw["location"]["currency"] == "EUR"
    assert "display" in raw
    assert "sources" in raw
