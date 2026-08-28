"""Unit tests for phases/survey.py — Phase 1 SURVEY + the §8.1/§8.1a/§8.2
coverage gate, broadening interrupt, one-shot latch, and (added post-step-8)
§4.3 ledger validation of `secondhand_risk_factors` (build order step 5,
extended after step 8's audit). `SdkSurveyor` (the real SDK-calling
adapter) is intentionally not exercised here — see the module docstring;
only `run_survey`'s gate/interrupt/latch orchestration (against a fake),
`_verify_exemplars`, and `_validate_secondhand_risk_factors` (both pure,
plain fixtures) are.
"""

import asyncio

from product_scout.hooks.budget import RunBudget
from product_scout.hooks.ledger import FetchLedger
from product_scout.io.cli_port import CLIQuestionPort
from product_scout.models import Location
from product_scout.phases.survey import (
    KEEP_SCOPE_OPTION,
    STOP_OPTION,
    RawSurvey,
    Surveyor,
    _repair_comparison_specs,
    _validate_secondhand_risk_factors,
    _verify_exemplars,
    run_survey,
)
from tests.conftest import (
    make_broader_category,
    make_cluster,
    make_run_budget,
    make_sourced_value,
    make_survey_report,
)


def run(coro):
    return asyncio.run(coro)


def make_port(inputs: list[str]):
    """A CLIQuestionPort fed from a fixed script instead of a real terminal.
    Mirrors test_probe.py's / test_intake.py's helper."""
    it = iter(inputs)
    printed: list[str] = []

    def print_fn(line: str = "") -> None:
        printed.append(line)

    return CLIQuestionPort(input_fn=lambda _prompt: next(it), print_fn=print_fn), printed


def make_location(**overrides) -> Location:
    defaults = dict(country="US", currency="USD")
    defaults.update(overrides)
    return Location(**defaults)


class FakeSurveyor:
    """Surveyor test double: one canned RawSurvey per product_type.

    `calls` records every (product_type, location) pair surveyed, in
    order, so tests can pin exactly how many times (and on what, and with
    what location) the survey actually ran — the key thing for the "never
    interrupt twice" edge case and for confirming location is threaded
    through.
    """

    def __init__(self, responses: dict[str, RawSurvey]):
        self._responses = responses
        self.calls: list[tuple[str, Location]] = []

    async def survey(
        self, product_type: str, location: Location, ledger: FetchLedger, budget: RunBudget
    ) -> RawSurvey:
        self.calls.append((product_type, location))
        return self._responses[product_type]


def _menu_options(printed: list[str]) -> list[str]:
    options = []
    for line in printed:
        stripped = line.strip()
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] == ". ":
            options.append(stripped[3:])
    return options


def make_raw(**overrides) -> RawSurvey:
    # Default evidence pool covers make_survey_report()'s default cluster
    # exemplar ("Widget Pro") so tests unrelated to §8.1a's exemplar
    # constraint aren't tripped by it — see test_verify_exemplars below for
    # the constraint's own dedicated tests.
    evidence_pool = overrides.pop("evidence_pool", ["The Widget Pro is a popular choice."])
    caveats = overrides.pop("caveats", [])
    report = make_survey_report(**overrides)
    return RawSurvey(report=report, evidence_pool=evidence_pool, caveats=caveats)


# -- protocol conformance -----------------------------------------------------


def test_fake_surveyor_satisfies_surveyor():
    surveyor = FakeSurveyor({"widgets": make_raw()})
    assert isinstance(surveyor, Surveyor)


# -- rich / moderate: proceed without interrupting ----------------------------


def test_rich_coverage_proceeds_without_interrupt():
    surveyor = FakeSurveyor({"widgets": make_raw(coverage="rich")})
    port, _ = make_port([])  # would raise StopIteration if the port were called

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.product_type == "widgets"
    assert outcome.original_product_type is None
    assert outcome.category_broadening_offered is False
    assert outcome.caveats == []
    assert surveyor.calls == [("widgets", make_location())]


def test_moderate_coverage_proceeds_without_interrupt():
    surveyor = FakeSurveyor(
        {
            "widgets": make_raw(
                coverage="moderate",
                estimated_product_count=6,
                independent_review_sources_found=3,
            )
        }
    )
    port, _ = make_port([])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.category_broadening_offered is False


# -- location is threaded through ---------------------------------------------


def test_location_passed_through_to_surveyor():
    surveyor = FakeSurveyor({"widgets": make_raw(coverage="rich")})
    port, _ = make_port([])
    location = make_location(country="DE", currency="EUR")

    run(run_survey("widgets", location, surveyor, port, FetchLedger(), make_run_budget()))

    assert surveyor.calls == [("widgets", location)]


# -- sparse / barren: the one interrupt ---------------------------------------


def test_sparse_coverage_interrupts_and_user_can_stop():
    surveyor = FakeSurveyor(
        {
            "widgets": make_raw(
                coverage="sparse",
                estimated_product_count=3,
                independent_review_sources_found=1,
                has_methodology_backed_testing=False,
                suggested_broader_categories=[make_broader_category(name="broader widgets")],
            )
        }
    )
    port, printed = make_port([STOP_OPTION])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is False
    assert outcome.low_evidence_mode is False
    assert outcome.category_broadening_offered is True
    assert outcome.original_product_type is None
    assert surveyor.calls == [("widgets", make_location())]  # no re-survey after stopping
    assert any("Coverage check:" in line for line in printed)


def test_sparse_coverage_user_keeps_original_scope():
    surveyor = FakeSurveyor(
        {
            "widgets": make_raw(
                coverage="sparse",
                estimated_product_count=3,
                independent_review_sources_found=1,
                suggested_broader_categories=[make_broader_category(name="broader widgets")],
            )
        }
    )
    port, _ = make_port([KEEP_SCOPE_OPTION])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert outcome.product_type == "widgets"
    assert outcome.original_product_type is None  # never broadened
    assert outcome.category_broadening_offered is True
    assert any("kept the original scope" in c for c in outcome.caveats)
    assert surveyor.calls == [("widgets", make_location())]


def test_barren_coverage_user_broadens_to_a_rich_category():
    barren = make_raw(
        coverage="barren",
        estimated_product_count=1,
        independent_review_sources_found=0,
        suggested_broader_categories=[
            make_broader_category(name="broader widgets", estimated_coverage="rich")
        ],
    )
    rich = make_raw(coverage="rich")
    surveyor = FakeSurveyor({"vintage widgets": barren, "broader widgets": rich})
    port, _ = make_port(['Research "broader widgets" instead (rich coverage)'])

    outcome = run(run_survey("vintage widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.product_type == "broader widgets"
    assert outcome.original_product_type == "vintage widgets"
    assert outcome.category_broadening_offered is True
    assert [c for c, _ in surveyor.calls] == ["vintage widgets", "broader widgets"]


# -- zero suggested categories: ask_text yes/no fallback ----------------------


def test_zero_suggested_categories_falls_back_to_yes_no_proceed():
    surveyor = FakeSurveyor(
        {"widgets": make_raw(coverage="barren", suggested_broader_categories=[])}
    )
    port, printed = make_port(["yes"])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert outcome.category_broadening_offered is True
    # ask_choice was never invoked — no numbered menu was ever printed.
    assert _menu_options(printed) == []


def test_zero_suggested_categories_falls_back_to_yes_no_stop():
    surveyor = FakeSurveyor(
        {"widgets": make_raw(coverage="barren", suggested_broader_categories=[])}
    )
    port, printed = make_port(["no"])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is False
    assert outcome.category_broadening_offered is True
    assert _menu_options(printed) == []


def test_zero_suggested_categories_reprompts_on_garbage_input():
    surveyor = FakeSurveyor(
        {"widgets": make_raw(coverage="barren", suggested_broader_categories=[])}
    )
    port, _ = make_port(["maybe", "yes"])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True


# -- key edge case (§8.1a): broadening into ANOTHER sparse category ----------


def test_broadening_into_another_sparse_category_does_not_reinterrupt():
    original = make_raw(
        coverage="sparse",
        estimated_product_count=3,
        independent_review_sources_found=1,
        suggested_broader_categories=[
            make_broader_category(name="broader widgets", estimated_coverage="moderate")
        ],
    )
    also_sparse = make_raw(
        coverage="sparse",
        estimated_product_count=4,
        independent_review_sources_found=1,
        notes="Still thin even after broadening.",
    )
    surveyor = FakeSurveyor({"widgets": original, "broader widgets": also_sparse})
    # Only ONE scripted answer: if run_survey asked a second time, make_port's
    # StopIteration would fail the test outright.
    port, printed = make_port(['Research "broader widgets" instead (moderate coverage)'])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert [c for c, _ in surveyor.calls] == ["widgets", "broader widgets"]
    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True  # degraded, not asked again
    assert outcome.product_type == "broader widgets"
    assert outcome.original_product_type == "widgets"
    assert outcome.category_broadening_offered is True
    assert any(
        "also surveyed as sparse" in c and "without asking again" in c
        for c in outcome.caveats
    )
    assert sum("Coverage check:" in line for line in printed) == 1


def test_broadening_into_a_barren_category_also_does_not_reinterrupt():
    sparse = make_raw(
        coverage="sparse",
        suggested_broader_categories=[make_broader_category(name="broader widgets")],
    )
    barren = make_raw(coverage="barren", independent_review_sources_found=0)
    surveyor = FakeSurveyor({"widgets": sparse, "broader widgets": barren})
    port, _ = make_port(['Research "broader widgets" instead (rich coverage)'])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert [c for c, _ in surveyor.calls] == ["widgets", "broader widgets"]


def test_broadening_into_a_barren_category_with_zero_suggestions_does_not_reinterrupt():
    """The two edge cases compose: latch already set, AND the re-surveyed
    category has nothing further to suggest. Must still degrade silently,
    not fall into the ask_text fallback a second time."""
    sparse = make_raw(
        coverage="sparse",
        suggested_broader_categories=[make_broader_category(name="broader widgets")],
    )
    barren_dead_end = make_raw(coverage="barren", suggested_broader_categories=[])
    surveyor = FakeSurveyor({"widgets": sparse, "broader widgets": barren_dead_end})
    port, _ = make_port(['Research "broader widgets" instead (rich coverage)'])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is True
    assert [c for c, _ in surveyor.calls] == ["widgets", "broader widgets"]


# -- duplicate category names deduped -----------------------------------------


def test_duplicate_broader_category_names_are_deduped():
    surveyor = FakeSurveyor(
        {
            "widgets": make_raw(
                coverage="sparse",
                suggested_broader_categories=[
                    make_broader_category(name="broader widgets", estimated_coverage="moderate"),
                    make_broader_category(name="Broader Widgets", estimated_coverage="rich"),
                ],
            )
        }
    )
    port, printed = make_port([STOP_OPTION])

    run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    options = _menu_options(printed)
    # First occurrence wins the dedup (moderate), so its annotation survives.
    assert options.count('Research "broader widgets" instead (moderate coverage)') == 1
    assert not any("rich coverage" in opt for opt in options)


# -- _repair_comparison_specs: §4.0b, found by build order step 12's live
# verification (SdkSurveyor had no recovery from this — real, reproducible) --


def test_repair_injects_missing_dimension_names():
    parsed = {
        "comparison_specs": ["weight"],
        "dimensions": [{"name": "weight"}, {"name": "material"}, {"name": "color"}],
    }
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired["comparison_specs"] == ["weight", "material", "color"]
    assert len(caveats) == 1
    assert "material" in caveats[0] and "color" in caveats[0]


def test_repair_noop_when_already_a_superset():
    parsed = {
        "comparison_specs": ["weight", "material", "extra_spec"],
        "dimensions": [{"name": "weight"}, {"name": "material"}],
    }
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired["comparison_specs"] == ["weight", "material", "extra_spec"]
    assert caveats == []


def test_repair_noop_when_no_dimensions():
    parsed = {"comparison_specs": ["weight"], "dimensions": []}
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired is parsed  # untouched — not even a copy needed
    assert caveats == []


def test_repair_does_not_mutate_input():
    parsed = {
        "comparison_specs": ["weight"],
        "dimensions": [{"name": "material"}],
    }
    original = {k: list(v) for k, v in parsed.items()}
    _repair_comparison_specs(parsed)
    assert parsed == original


def test_repair_leaves_malformed_dimensions_alone():
    """Not a general sanitizer — a genuinely malformed response should
    still fail loudly at SurveyReport(**parsed), not be coerced quietly."""
    parsed = {"comparison_specs": ["weight"], "dimensions": "not a list"}
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired is parsed
    assert caveats == []


def test_repair_leaves_malformed_comparison_specs_alone():
    parsed = {"comparison_specs": "not a list", "dimensions": [{"name": "material"}]}
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired is parsed
    assert caveats == []


def test_repair_skips_dimensions_missing_a_name():
    parsed = {
        "comparison_specs": [],
        "dimensions": [{"splits": {}}, {"name": None}, {"name": ""}, {"name": "material"}],
    }
    repaired, caveats = _repair_comparison_specs(parsed)
    assert repaired["comparison_specs"] == ["material"]
    assert len(caveats) == 1


def test_run_survey_propagates_repair_caveat_from_surveyor():
    """End to end: whatever a Surveyor puts on RawSurvey.caveats reaches
    SurveyOutcome.caveats — the seam _repair_comparison_specs plugs into."""
    surveyor = FakeSurveyor(
        {"widgets": make_raw(coverage="rich", caveats=["comparison_specs was missing ['x']"])}
    )
    port, _ = make_port([])
    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))
    assert "comparison_specs was missing ['x']" in outcome.caveats


# -- _verify_exemplars: §8.1a constraint 1 ------------------------------------


def test_verified_exemplar_is_kept():
    cluster = make_cluster(exemplar_products=["Widget Pro"])
    verified, caveats = _verify_exemplars(
        [cluster], evidence_pool=["The Widget Pro costs $250."]
    )
    assert verified[0].exemplar_products == ["Widget Pro"]
    assert caveats == []


def test_unverified_exemplar_is_dropped_and_caveat_logged():
    cluster = make_cluster(label="Mid-tier", exemplar_products=["Fictional Widget X9"])
    verified, caveats = _verify_exemplars(
        [cluster], evidence_pool=["Nothing about that product here."]
    )
    assert verified[0].exemplar_products == []
    assert len(caveats) == 1
    assert "Fictional Widget X9" in caveats[0]
    assert "Mid-tier" in caveats[0]


def test_matching_is_case_and_whitespace_insensitive():
    cluster = make_cluster(exemplar_products=["widget   pro"])
    verified, caveats = _verify_exemplars(
        [cluster], evidence_pool=["Reviewers loved the WIDGET PRO this year."]
    )
    assert verified[0].exemplar_products == ["widget   pro"]
    assert caveats == []


def test_mixed_verified_and_unverified_in_one_cluster():
    cluster = make_cluster(exemplar_products=["Widget Pro", "Fake Widget"])
    verified, caveats = _verify_exemplars(
        [cluster], evidence_pool=["The Widget Pro is well reviewed."]
    )
    assert verified[0].exemplar_products == ["Widget Pro"]
    assert len(caveats) == 1
    assert "Fake Widget" in caveats[0]


def test_empty_evidence_pool_drops_all_exemplars():
    cluster = make_cluster(exemplar_products=["Widget Pro"])
    verified, caveats = _verify_exemplars([cluster], evidence_pool=[])
    assert verified[0].exemplar_products == []
    assert len(caveats) == 1


# -- integration: run_survey strips an invented exemplar name ----------------


def test_run_survey_strips_invented_exemplar_end_to_end():
    report_kwargs = dict(
        coverage="rich",
        clusters=[make_cluster(label="Mid-tier", exemplar_products=["Invented Ghost Widget"])],
    )
    surveyor = FakeSurveyor(
        {
            "widgets": RawSurvey(
                report=make_survey_report(**report_kwargs),
                evidence_pool=["Nothing matches that name anywhere."],
            )
        }
    )
    port, _ = make_port([])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.survey.clusters[0].exemplar_products == []
    assert any("Invented Ghost Widget" in c for c in outcome.caveats)


# -- _validate_secondhand_risk_factors: §4.3, applied to SURVEY's output -----
# (added post-step-8 — see module docstring)


UNSEEN_URL = "https://forum.example.com/thread/invented"
SEARCHED_URL = "https://forum.example.com/thread/42"
FETCHED_URL = "https://example.com/teardown-report"


def test_factor_citing_a_never_seen_url_is_dropped_and_caveat_logged():
    factor = make_sourced_value(source_url=UNSEEN_URL, source_type="community")
    kept, caveats = _validate_secondhand_risk_factors([factor], FetchLedger())
    assert kept == []
    assert len(caveats) == 1
    assert UNSEEN_URL in caveats[0]
    assert "§4.3" in caveats[0]


def test_factor_citing_a_seen_not_fetched_url_is_kept():
    """The exact distinction invariant 3 names: judgment-bearing SURVEY
    values may cite seen_not_fetched — a risk factor is a warning, not a
    spec, so it must NOT need a full fetch to be admissible."""
    ledger = FetchLedger()
    ledger.record_seen(SEARCHED_URL)
    factor = make_sourced_value(source_url=SEARCHED_URL, source_type="community")

    kept, caveats = _validate_secondhand_risk_factors([factor], ledger)

    assert kept == [factor]
    assert caveats == []


def test_factor_citing_a_fetched_url_is_kept():
    ledger = FetchLedger()
    ledger.record_fetch(FETCHED_URL)
    factor = make_sourced_value(source_url=FETCHED_URL, source_type="testing_outlet")

    kept, caveats = _validate_secondhand_risk_factors([factor], ledger)

    assert kept == [factor]
    assert caveats == []


def test_mixed_verified_and_unverified_factors():
    ledger = FetchLedger()
    ledger.record_seen(SEARCHED_URL)
    good = make_sourced_value(source_url=SEARCHED_URL, source_type="community")
    bad = make_sourced_value(source_url=UNSEEN_URL, source_type="community")

    kept, caveats = _validate_secondhand_risk_factors([good, bad], ledger)

    assert kept == [good]
    assert len(caveats) == 1
    assert UNSEEN_URL in caveats[0]


def test_empty_ledger_drops_all_factors():
    factor = make_sourced_value(source_url=FETCHED_URL, source_type="community")
    kept, caveats = _validate_secondhand_risk_factors([factor], FetchLedger())
    assert kept == []
    assert len(caveats) == 1


# -- integration: run_survey strips an unverified secondhand risk factor -----


def test_run_survey_drops_unverified_secondhand_risk_factor_end_to_end():
    report_kwargs = dict(
        coverage="rich",
        secondhand_risk_factors=[
            make_sourced_value(source_url=UNSEEN_URL, source_type="community")
        ],
    )
    surveyor = FakeSurveyor(
        {"widgets": RawSurvey(report=make_survey_report(**report_kwargs))}
    )
    port, _ = make_port([])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, FetchLedger(), make_run_budget()))

    assert outcome.survey.secondhand_risk_factors == []
    assert any(UNSEEN_URL in c for c in outcome.caveats)


def test_run_survey_keeps_secondhand_risk_factor_seen_via_search():
    ledger = FetchLedger()
    ledger.record_seen(SEARCHED_URL)
    report_kwargs = dict(
        coverage="rich",
        secondhand_risk_factors=[
            make_sourced_value(source_url=SEARCHED_URL, source_type="community")
        ],
    )
    surveyor = FakeSurveyor(
        {"widgets": RawSurvey(report=make_survey_report(**report_kwargs))}
    )
    port, _ = make_port([])

    outcome = run(run_survey("widgets", make_location(), surveyor, port, ledger, make_run_budget()))

    assert len(outcome.survey.secondhand_risk_factors) == 1
    assert outcome.survey.secondhand_risk_factors[0].source_url == SEARCHED_URL
