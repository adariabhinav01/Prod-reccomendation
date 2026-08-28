"""Tests for cli.py (spec docs/handoff.md §16; build order step 3 adds
`config`, step 14 adds `rescore`/`history`, step 15 adds `eval` and
`research` — the latter filling a gap the numbered build order never
explicitly named).
"""

import tomllib

import pytest

from product_scout.cli import main
from product_scout.settings import LocationSettings, Settings, load, save
from product_scout.store.runs import RunStore
from tests.conftest import make_location, make_run_record
from tests.test_rescore import FakeScorer, FakeSynthesizer, _happy_scoring, _happy_synthesis, _matching_hashes


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / ".product-scout" / "config.toml"
    monkeypatch.setattr("product_scout.settings.DEFAULT_CONFIG_PATH", path)
    return path


# -- research ---------------------------------------------------------------
#
# `run_pipeline` itself is exhaustively covered by test_orchestrator.py's
# fakes (including the §16.1 resume tests) — these only exercise cli.py's
# own argument validation and the `--location` convenience, which don't
# need `run_pipeline` to actually execute.


def test_research_refuses_both_product_type_and_resume(isolated_config):
    exit_code = main(["research", "standing desks", "--resume", "some-run-id"])
    assert exit_code == 1


def test_research_refuses_location_combined_with_resume(isolated_config, capsys):
    """A resumed run's location comes from its own intake checkpoint, never
    from current settings — `--location` here would silently do nothing to
    the run being resumed, only to some future fresh run. Same reasoning
    as rescore's own location-mismatch refusal (§16)."""
    exit_code = main(["research", "--resume", "some-run-id", "--location", "DE"])
    assert exit_code == 1
    assert "no effect on a resumed run" in capsys.readouterr().err


def test_research_refuses_neither_product_type_nor_resume(isolated_config):
    exit_code = main(["research"])
    assert exit_code == 1


def test_research_location_flag_writes_through_to_settings(isolated_config, monkeypatch, tmp_path):
    # Stop short of actually running the pipeline (no fakes injected here,
    # and this test isn't about orchestration) by making run_pipeline a
    # no-op stand-in that raises once called, after --location has already
    # had its effect.
    from product_scout import cli as cli_module

    async def fake_run_pipeline(*args, **kwargs):
        raise SystemExit("run_pipeline should not actually be reached by this test")

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    store = RunStore(root=tmp_path / ".product-scout")

    with pytest.raises(SystemExit):
        main(["research", "standing desks", "--location", "DE"], run_store=store)

    assert load(isolated_config).location.country == "DE"


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


# -- rescore -------------------------------------------------------------


@pytest.fixture
def isolated_store(tmp_path):
    return RunStore(root=tmp_path / ".product-scout")


def _configure_location(isolated_config, country="US", currency="USD"):
    save(Settings(location=LocationSettings(country=country, currency=currency)), isolated_config)


def test_rescore_happy_path_through_main(isolated_config, isolated_store, capsys):
    """Argument parsing through to a saved, linked new run — using the
    same fake scorer/synthesizer injection `run_rescore` itself is tested
    with, wired through `main()`'s DI parameters so this never touches a
    real API."""
    _configure_location(isolated_config)
    record = make_run_record(skill_hashes=_matching_hashes())
    isolated_store.save(record)

    exit_code = main(
        ["rescore", record.run_id, "--set", "Widget Pro=149"],
        run_store=isolated_store,
        scorer=FakeScorer(_happy_scoring()),
        synthesizer=FakeSynthesizer(_happy_synthesis()),
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Rescored as" in out
    assert record.run_id in out
    assert "Verdict: BUY" in out


def test_rescore_with_no_set_flags_still_succeeds(isolated_config, isolated_store, capsys):
    """§16: skill-hash mismatch is 'not a refusal — re-scoring against
    improved logic is often the point.' That's exactly a rescore with zero
    price overrides, so `--set` must not be mandatory."""
    _configure_location(isolated_config)
    record = make_run_record(skill_hashes=_matching_hashes())
    isolated_store.save(record)

    exit_code = main(
        ["rescore", record.run_id],
        run_store=isolated_store,
        scorer=FakeScorer(_happy_scoring()),
        synthesizer=FakeSynthesizer(_happy_synthesis()),
    )

    assert exit_code == 0
    assert "Rescored as" in capsys.readouterr().out


def test_rescore_bad_set_syntax_returns_nonzero(isolated_config, isolated_store, capsys):
    exit_code = main(["rescore", "some-run-id", "--set", "no-equals-sign"], run_store=isolated_store)
    assert exit_code == 1
    assert "must look like" in capsys.readouterr().err


def test_rescore_nonexistent_run_returns_nonzero(isolated_config, isolated_store, capsys):
    _configure_location(isolated_config)
    exit_code = main(["rescore", "no-such-run", "--set", "Widget Pro=149"], run_store=isolated_store)
    assert exit_code == 1
    assert "no such run" in capsys.readouterr().err


def test_rescore_location_mismatch_returns_nonzero_and_refuses(isolated_config, isolated_store, capsys):
    _configure_location(isolated_config, country="DE", currency="EUR")
    record = make_run_record(location=make_location(country="US", currency="USD"))
    isolated_store.save(record)

    exit_code = main(["rescore", record.run_id, "--set", "Widget Pro=149"], run_store=isolated_store)

    assert exit_code == 1
    assert "refused" in capsys.readouterr().err


def test_missing_rescore_run_id_exits_via_argparse(isolated_config):
    with pytest.raises(SystemExit):
        main(["rescore"])


# -- history ---------------------------------------------------------------


def test_history_lists_saved_runs(isolated_store, capsys):
    isolated_store.save(make_run_record(run_id="run-a", product_type="standing desks"))
    isolated_store.save(make_run_record(run_id="run-b", product_type="espresso machines"))

    exit_code = main(["history"], run_store=isolated_store)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "run-a" in out
    assert "run-b" in out


def test_history_filters_by_category(isolated_store, capsys):
    isolated_store.save(make_run_record(run_id="run-a", product_type="standing desks"))
    isolated_store.save(make_run_record(run_id="run-b", product_type="espresso machines"))

    exit_code = main(["history", "--category", "espresso machines"], run_store=isolated_store)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "run-b" in out
    assert "run-a" not in out


def test_history_empty_store_prints_no_runs_found(isolated_store, capsys):
    exit_code = main(["history"], run_store=isolated_store)
    assert exit_code == 0
    assert "No runs found." in capsys.readouterr().out


# -- eval ------------------------------------------------------------------


def test_eval_stable_only_passes_for_a_clean_case(isolated_store, tmp_path, capsys):
    from tests.test_eval import _happy_case_fakes, _write_case_fixtures, make_eval_case

    case = make_eval_case()
    cases_dir = tmp_path / "cases"
    _write_case_fixtures(cases_dir / case.name, case)

    exit_code = main(
        ["eval", "--stable-only"],
        run_store=isolated_store,
        eval_cases_dir=cases_dir,
        eval_scratch_dir=tmp_path / "scratch",
        **_happy_case_fakes(),
    )

    assert exit_code == 0
    assert f"[PASS] {case.name}" in capsys.readouterr().out


def test_eval_stable_only_fails_for_a_broken_case(isolated_store, tmp_path, capsys):
    from tests.test_eval import ExpectedShape, _happy_case_fakes, _write_case_fixtures, make_eval_case

    case = make_eval_case(expected_shape=ExpectedShape(kind="commodity"))
    cases_dir = tmp_path / "cases"
    _write_case_fixtures(cases_dir / case.name, case)

    exit_code = main(
        ["eval", "--stable-only"],
        run_store=isolated_store,
        eval_cases_dir=cases_dir,
        eval_scratch_dir=tmp_path / "scratch",
        **_happy_case_fakes(),
    )

    assert exit_code == 1
    out = capsys.readouterr()
    assert f"[FAIL] {case.name}" in out.out
    assert "verdict_shape" in out.err


def test_eval_with_no_cases_directory_fails_loudly_not_silently(isolated_store, tmp_path, capsys):
    exit_code = main(
        ["eval", "--stable-only"],
        run_store=isolated_store,
        eval_cases_dir=tmp_path / "does-not-exist",
        eval_scratch_dir=tmp_path / "scratch",
    )

    assert exit_code == 1
    assert "no cases found" in capsys.readouterr().err.lower()
