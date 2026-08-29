"""Unit tests for eval.py — the §17.1 golden set (build order step 15).

Every phase that could make a real API/network call is faked here, the
same convention every other test file in this repo follows — `run_stable`
defaults `refiner`/`scorer`/`synthesizer` to real `Sdk*` adapters, but this
file always injects fakes.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from product_scout.eval import (
    EvalCase,
    EvalQuestionPort,
    ExpectedShape,
    PriceAssertion,
    SuiteReport,
    check_axis_kind_recorded,
    check_confidence_ordering,
    check_constraint_satisfaction,
    check_every_product_has_a_con,
    check_ledger_validity,
    check_must_appear_products,
    check_price_assertions,
    check_shared_denominator,
    check_verdict_shape,
    load_case,
    run_eval_suite,
    run_stable,
)
from product_scout.hooks.ledger import FetchLedger
from product_scout.io.port import AxisSpec, IMPORTANCE_SKIP_DEFAULT, POSITION_SKIP_DEFAULT, TopicPrompt
from product_scout.models import Verdict
from product_scout.store.runs import RunStore
from tests.conftest import (
    make_availability,
    make_dimension,
    make_evidence_profile,
    make_location,
    make_pricing_model,
    make_product,
    make_run_record,
    make_survey_report,
)
from tests.test_orchestrator import FakeRefiner
from tests.test_rescore import FakeScorer, FakeSynthesizer, _happy_scoring, _happy_synthesis


def run(coro):
    return asyncio.run(coro)


def make_eval_case(**overrides) -> EvalCase:
    defaults = dict(
        name="rich-desks",
        category="rich",
        product_type="standing desks",
        location=make_location(),
        units="imperial",
        intake_script=["no", "no limit", "", ""],
        expected_shape=ExpectedShape(kind="rich"),
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


# -- EvalCase / fixture round-trips ------------------------------------------


def test_load_case_round_trips(tmp_path):
    case = make_eval_case()
    (tmp_path / "case.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")
    assert load_case(tmp_path) == case


# -- EvalQuestionPort ---------------------------------------------------------


def test_ask_choice_keeps_the_last_option():
    port = EvalQuestionPort(intake_script=[])
    answer = run(port.ask_choice("Broaden?", ["Broader widgets", "Keep my scope"], "Stop here"))
    assert answer == "Keep my scope"


def test_ask_topic_position_axis_uses_skip_default():
    port = EvalQuestionPort(intake_script=[])
    topic = TopicPrompt(
        topic="motor configuration",
        dimension_name="motor_count",
        gate_question="Do you need dual motors?",
        gate_description="...",
        axis=AxisSpec(kind="position", low_label="single", high_label="dual", why_this_matters="..."),
        free_text_prompt="Anything else?",
    )
    answer = run(port.ask_topic(topic))
    assert answer.gate_answer == "no_preference"
    assert answer.axis_kind == "position"
    assert answer.axis_value == POSITION_SKIP_DEFAULT
    assert answer.axis_skipped is True
    assert answer.became_filter is False


def test_ask_topic_importance_axis_uses_skip_default():
    port = EvalQuestionPort(intake_script=[])
    topic = TopicPrompt(
        topic="noise level",
        dimension_name=None,
        gate_question="Does noise matter?",
        gate_description="...",
        axis=AxisSpec(kind="importance", low_label="doesn't matter", high_label="critical", why_this_matters="..."),
        free_text_prompt="Anything else?",
    )
    answer = run(port.ask_topic(topic))
    assert answer.axis_value == IMPORTANCE_SKIP_DEFAULT


def test_ask_topic_with_no_axis_skips_cleanly():
    port = EvalQuestionPort(intake_script=[])
    topic = TopicPrompt(
        topic="brand preference", dimension_name=None, gate_question="Brand matter?",
        gate_description="...", axis=None, free_text_prompt="Anything else?",
    )
    answer = run(port.ask_topic(topic))
    assert answer.axis_kind is None
    assert answer.axis_value is None


def test_offer_bailout_always_declines():
    port = EvalQuestionPort(intake_script=[])
    assert run(port.offer_bailout()) is False


def test_ask_text_pops_scripted_answers_in_order():
    port = EvalQuestionPort(intake_script=["no", "no limit", "", ""])
    answers = [run(port.ask_text("q")) for _ in range(4)]
    assert answers == ["no", "no limit", "", ""]


def test_ask_text_raises_actionable_error_when_script_exhausted():
    port = EvalQuestionPort(intake_script=["no"])
    run(port.ask_text("q"))
    with pytest.raises(ValueError, match="ran out of answers"):
        run(port.ask_text("q"))


# -- stable checks -------------------------------------------------------


def test_check_every_product_has_a_con_passes_normally():
    record = make_run_record()
    assert check_every_product_has_a_con(record) == []


def test_check_every_product_has_a_con_catches_a_bypassed_empty_list():
    # Pydantic's min_length=1 makes this unconstructible normally —
    # model_construct bypasses validation to exercise the check itself.
    bad_product = make_product().model_copy(update={"cons": []})
    record = make_run_record(products=[bad_product])
    assert check_every_product_has_a_con(record) == ["Widget Pro"]


def test_check_constraint_satisfaction_passes_with_few_products():
    record = make_run_record(products=[make_product()])
    assert check_constraint_satisfaction(record) == []


def test_check_constraint_satisfaction_flags_insufficient_archetype_diversity():
    products = [
        make_product(name=f"Product {i}", strength_archetype="value", role="recommendation", in_budget=True)
        for i in range(3)
    ]
    record = make_run_record(products=products)
    violations = check_constraint_satisfaction(record)
    assert len(violations) == 1
    assert "distinct strength_archetype" in violations[0]


def test_check_constraint_satisfaction_passes_with_diverse_archetypes():
    archetypes = ["value", "premium", "compact"]
    products = [
        make_product(name=f"Product {i}", strength_archetype=a, role="recommendation", in_budget=True)
        for i, a in enumerate(archetypes)
    ]
    record = make_run_record(products=products)
    assert check_constraint_satisfaction(record) == []


def test_check_constraint_satisfaction_flags_row_cap_violation():
    products = [make_product(name=f"Product {i}") for i in range(13)]
    record = make_run_record(products=products)
    violations = check_constraint_satisfaction(record)
    assert any("hard cap" in v for v in violations)


def test_check_ledger_validity_passes_when_sources_are_admissible():
    ledger = FetchLedger()
    ledger.record_fetch("https://example.com/spec-sheet")
    ledger.record_fetch("https://example.com/product")
    ledger.record_seen("https://example.com/review")
    record = make_run_record()
    assert check_ledger_validity(record, ledger) == []


def test_check_ledger_validity_catches_a_spec_citing_an_unadmitted_url():
    ledger = FetchLedger()  # empty — nothing fetched or seen
    record = make_run_record()
    violations = check_ledger_validity(record, ledger)
    assert any("specs['weight']" in v for v in violations)


def test_check_ledger_validity_skips_prior_gen_products():
    ledger = FetchLedger()
    prior_product = make_product(generation="prior")
    record = make_run_record(products=[prior_product])
    assert check_ledger_validity(record, ledger) == []


def test_check_axis_kind_recorded_passes_when_all_dimensions_have_one():
    survey = make_survey_report(dimensions=[make_dimension(axis_kind="position")])
    record = make_run_record(survey=survey)
    assert check_axis_kind_recorded(record) == []


def test_check_axis_kind_recorded_flags_a_missing_axis_kind():
    survey = make_survey_report(dimensions=[make_dimension(name="motor_count", axis_kind=None)])
    record = make_run_record(survey=survey)
    assert check_axis_kind_recorded(record) == ["motor_count"]


def test_check_shared_denominator_is_always_empty():
    assert check_shared_denominator(make_run_record()) == []


def test_check_verdict_shape_rich_fails_on_insufficient_evidence():
    record = make_run_record(
        verdict=Verdict(action="INSUFFICIENT_EVIDENCE", reasoning="...", timing_note=None)
    )
    case = make_eval_case(expected_shape=ExpectedShape(kind="rich"))
    assert check_verdict_shape(record, case) != []


def test_check_verdict_shape_sparse_requires_low_evidence_mode():
    record = make_run_record(low_evidence_mode=False)
    case = make_eval_case(expected_shape=ExpectedShape(kind="sparse"))
    assert check_verdict_shape(record, case) != []
    record_ok = make_run_record(low_evidence_mode=True)
    assert check_verdict_shape(record_ok, case) == []


def test_check_verdict_shape_commodity_requires_flag():
    record = make_run_record(commodity_category=False)
    case = make_eval_case(expected_shape=ExpectedShape(kind="commodity"))
    assert check_verdict_shape(record, case) != []


def test_check_verdict_shape_software_requires_category_kind():
    survey = make_survey_report(category_kind="physical")
    record = make_run_record(survey=survey)
    case = make_eval_case(expected_shape=ExpectedShape(kind="software"))
    assert check_verdict_shape(record, case) != []


def test_check_verdict_shape_cross_border_requires_a_landed_price():
    record = make_run_record(products=[make_product(availability=make_availability(landed_price_native=None))])
    case = make_eval_case(expected_shape=ExpectedShape(kind="cross_border"))
    assert check_verdict_shape(record, case) != []
    record_ok = make_run_record(
        products=[make_product(availability=make_availability(landed_price_native=250.0))]
    )
    assert check_verdict_shape(record_ok, case) == []


# -- decaying checks -----------------------------------------------------


def test_check_must_appear_products_flags_missing_names():
    record = make_run_record(products=[make_product(name="Widget Pro")])
    case = make_eval_case(must_appear_products=["Widget Pro", "Widget Max"])
    assert check_must_appear_products(record, case) == ["Widget Max"]


def test_check_confidence_ordering_flags_a_broken_order():
    low = make_product(name="Low", evidence=make_evidence_profile(confidence=0.3))
    high = make_product(name="High", evidence=make_evidence_profile(confidence=0.9))
    record = make_run_record(products=[low, high])
    case = make_eval_case(expected_confidence_order=["Low", "High"])  # wrong order
    assert check_confidence_ordering(record, case) != []


def test_check_price_assertions_flags_a_price_outside_tolerance():
    record = make_run_record(products=[make_product(pricing=make_pricing_model(upfront_amount=500.0))])
    case = make_eval_case(price_assertions=[PriceAssertion(product_name="Widget Pro", expected_price=100.0)])
    assert check_price_assertions(record, case) != []


def test_check_price_assertions_passes_within_tolerance():
    record = make_run_record(products=[make_product(pricing=make_pricing_model(upfront_amount=200.0))])
    case = make_eval_case(
        price_assertions=[PriceAssertion(product_name="Widget Pro", expected_price=199.0, tolerance=0.10)]
    )
    assert check_price_assertions(record, case) == []


# -- run_stable / run_eval_suite (fully fake-driven; no live call) ----------


def _write_case_fixtures(case_dir, case: EvalCase):
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "case.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")

    # §8.1a's _verify_exemplars (inside run_survey) strips a cluster's
    # exemplar if it's not backed by the evidence pool, which would leave
    # _extraction_candidates empty and make run_extraction short-circuit
    # to [] without ever calling the (frozen) extractor — same gotcha
    # test_orchestrator.py's own _raw_survey() helper documents.
    survey = make_survey_report()
    (case_dir / "survey.json").write_text(
        json.dumps({
            "report": json.loads(survey.model_dump_json()),
            "evidence_pool": ["The Widget Pro is a popular choice."],
            "caveats": [],
        }),
        encoding="utf-8",
    )
    product = make_product()
    (case_dir / "products.json").write_text(
        json.dumps([json.loads(product.model_dump_json())]), encoding="utf-8"
    )
    (case_dir / "timing.json").write_text(
        json.dumps({
            "signal_found": False, "successor_expected": None, "price_trend": None,
            "technology_transition": None, "basis_notes": [], "recommends_wait": False,
        }),
        encoding="utf-8",
    )
    (case_dir / "prior_gen.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    ledger_entries = [
        {"url": u, "mode": "fetched", "status": "200", "observed_at": datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()}
        for u in ("https://example.com/spec-sheet", "https://example.com/product")
    ]
    ledger_entries.append(
        {"url": "https://example.com/review", "mode": "seen_not_fetched", "status": None,
         "observed_at": datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()}
    )
    (case_dir / "ledger.json").write_text(json.dumps(ledger_entries), encoding="utf-8")


def _happy_case_fakes():
    return dict(
        refiner=FakeRefiner([]),
        scorer=FakeScorer(_happy_scoring()),
        synthesizer=FakeSynthesizer(_happy_synthesis()),
    )


def test_run_stable_produces_a_record_from_frozen_fixtures(tmp_path):
    case = make_eval_case()
    case_dir = tmp_path / "cases" / case.name
    _write_case_fixtures(case_dir, case)
    store = RunStore(root=tmp_path / ".product-scout")

    record = run(run_stable(case, case_dir, store, tmp_path / "scratch", **_happy_case_fakes()))

    assert record is not None
    assert record.product_type == "standing desks"
    assert record.products[0].name == "Widget Pro"


def test_run_eval_suite_stable_only_reports_pass_for_a_clean_case(tmp_path):
    case = make_eval_case()
    cases_dir = tmp_path / "cases"
    _write_case_fixtures(cases_dir / case.name, case)
    store = RunStore(root=tmp_path / ".product-scout")

    report = run(
        run_eval_suite(cases_dir, store, tmp_path / "scratch", stable_only=True, **_happy_case_fakes())
    )

    assert isinstance(report, SuiteReport)
    assert len(report.results) == 1
    assert report.results[0].case_name == case.name
    assert report.ok is True


def test_run_eval_suite_stable_only_reports_fail_when_a_stable_check_breaks(tmp_path):
    case = make_eval_case(expected_shape=ExpectedShape(kind="commodity"))  # our fixture isn't commodity
    cases_dir = tmp_path / "cases"
    _write_case_fixtures(cases_dir / case.name, case)
    store = RunStore(root=tmp_path / ".product-scout")

    report = run(
        run_eval_suite(cases_dir, store, tmp_path / "scratch", stable_only=True, **_happy_case_fakes())
    )

    assert report.ok is False
    assert report.results[0].stable["verdict_shape"] != []


def test_run_eval_suite_with_zero_cases_is_a_failure_not_a_vacuous_pass(tmp_path):
    """`all(...)` over an empty sequence is vacuously True — without an
    explicit guard, an empty (or missing) cases_dir would make
    `scout eval --stable-only` silently report success while checking
    nothing at all."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    store = RunStore(root=tmp_path / ".product-scout")

    report = run(
        run_eval_suite(cases_dir, store, tmp_path / "scratch", stable_only=True, **_happy_case_fakes())
    )

    assert report.results == []
    assert report.ok is False


def test_run_eval_suite_with_a_nonexistent_cases_dir_is_also_a_failure(tmp_path):
    """cases_dir itself missing (not just empty) must not raise — same
    "no cases found" outcome as an empty directory."""
    store = RunStore(root=tmp_path / ".product-scout")

    report = run(
        run_eval_suite(
            tmp_path / "does-not-exist", store, tmp_path / "scratch",
            stable_only=True, **_happy_case_fakes(),
        )
    )

    assert report.results == []
    assert report.ok is False
