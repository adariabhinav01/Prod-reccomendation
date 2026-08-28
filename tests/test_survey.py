"""Unit tests for phases/survey.py — Phase 1 SURVEY + the §8.1/§8.1a/§8.2
coverage gate, broadening interrupt, and one-shot latch (build order
step 5). `SdkSurveyor` (the real SDK-calling adapter) is intentionally not
exercised here — see the module docstring; only `run_survey`'s
gate/interrupt/latch orchestration (against a fake) and `_verify_exemplars`
(pure, plain string fixtures) are.
"""

import asyncio

from product_scout.io.cli_port import CLIQuestionPort
from product_scout.models import Location
from product_scout.phases.survey import (
    KEEP_SCOPE_OPTION,
    STOP_OPTION,
    RawSurvey,
    Surveyor,
    _verify_exemplars,
    run_survey,
)
from tests.conftest import make_broader_category, make_cluster, make_survey_report


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

    async def survey(self, product_type: str, location: Location) -> RawSurvey:
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
    report = make_survey_report(**overrides)
    return RawSurvey(report=report, evidence_pool=evidence_pool)


# -- protocol conformance -----------------------------------------------------


def test_fake_surveyor_satisfies_surveyor():
    surveyor = FakeSurveyor({"widgets": make_raw()})
    assert isinstance(surveyor, Surveyor)


# -- rich / moderate: proceed without interrupting ----------------------------


def test_rich_coverage_proceeds_without_interrupt():
    surveyor = FakeSurveyor({"widgets": make_raw(coverage="rich")})
    port, _ = make_port([])  # would raise StopIteration if the port were called

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

    assert outcome.proceed is True
    assert outcome.low_evidence_mode is False
    assert outcome.category_broadening_offered is False


# -- location is threaded through ---------------------------------------------


def test_location_passed_through_to_surveyor():
    surveyor = FakeSurveyor({"widgets": make_raw(coverage="rich")})
    port, _ = make_port([])
    location = make_location(country="DE", currency="EUR")

    run(run_survey("widgets", location, surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("vintage widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

    assert outcome.proceed is False
    assert outcome.category_broadening_offered is True
    assert _menu_options(printed) == []


def test_zero_suggested_categories_reprompts_on_garbage_input():
    surveyor = FakeSurveyor(
        {"widgets": make_raw(coverage="barren", suggested_broader_categories=[])}
    )
    port, _ = make_port(["maybe", "yes"])

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

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

    run(run_survey("widgets", make_location(), surveyor, port))

    options = _menu_options(printed)
    # First occurrence wins the dedup (moderate), so its annotation survives.
    assert options.count('Research "broader widgets" instead (moderate coverage)') == 1
    assert not any("rich coverage" in opt for opt in options)


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

    outcome = run(run_survey("widgets", make_location(), surveyor, port))

    assert outcome.survey.clusters[0].exemplar_products == []
    assert any("Invented Ghost Widget" in c for c in outcome.caveats)
