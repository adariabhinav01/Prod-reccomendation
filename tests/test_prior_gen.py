"""Unit tests for phases/prior_gen.py — Phase 5 PRIOR-GEN (build order
step 10). `SdkPriorGenResearcher` (the real SDK-calling adapter) is
intentionally not exercised here — see the module docstring; only
`_admit_finding`'s §12.1/§12.6 omit/disclose split and `run_prior_gen`'s
pass-through/short-circuit logic (against a fake) are.
"""

import asyncio

from product_scout.hooks.budget import RunBudget
from product_scout.hooks.ledger import FetchLedger
from product_scout.phases.prior_gen import (
    PriorGenResearcher,
    RawPriorGen,
    RawPriorGenFinding,
    _admit_finding,
    run_prior_gen,
)
from tests.conftest import make_location, make_product, make_run_budget, make_survey_report

SPEC_URL = "https://example.com/prior-spec-sheet"
PRICE_URL = "https://example.com/prior-product"
REVIEW_URL = "https://example.com/prior-review"


def run(coro):
    return asyncio.run(coro)


def make_ledger(*, fetched: list[str] = (), seen: list[str] = ()) -> FetchLedger:
    ledger = FetchLedger()
    for url in fetched:
        ledger.record_fetch(url)
    for url in seen:
        ledger.record_seen(url)
    return ledger


def default_ledger() -> FetchLedger:
    return make_ledger(fetched=[SPEC_URL, PRICE_URL], seen=[REVIEW_URL])


def full_valid_product_args(**overrides) -> dict:
    """A complete, valid record_product-shaped dict — same shape as
    test_record_product.py's `full_valid_args`, kept as a local copy per
    this codebase's per-test-module convention. `generation`/`cluster_key`/
    `role` are deliberately "wrong" here (current/mismatched/omitted) to
    prove `_admit_finding` overwrites them rather than trusting the model."""
    defaults = dict(
        name="Widget Classic",
        brand="Acme",
        generation="current",  # deliberately wrong; _admit_finding must force "prior"
        cluster_key="wrong-cluster",  # deliberately wrong; must inherit the seed's
        cluster_rationale="Last year's dual-motor mid-tier desk.",
        strength_archetype="value",
        pricing={
            "model_type": "one_time",
            "upfront_amount": 119.0,
            "recurring_amount": None,
            "recurring_period": None,
            "recurring_required_for_core": False,
            "total_cost_1yr": 119.0,
            "price_currency": "USD",
            "price_tax_inclusive": None,
            "price_source_url": PRICE_URL,
            "price_observed_at": "2026-08-01T00:00:00+00:00",
        },
        availability={"sold_in_region": True},
        specs={
            "weight": {
                "value": "44 lb",
                "source_url": SPEC_URL,
                "source_type": "manufacturer",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        },
        pros=["much cheaper"],
        cons=["slightly heavier"],
        ownership_notes=[],
        review_sources=[REVIEW_URL],
        in_budget=True,
    )
    defaults.update(overrides)
    return defaults


def make_finding(**overrides) -> RawPriorGenFinding:
    defaults = dict(
        seed_product_name="Widget Pro",
        predecessor_found=True,
        worth_promoting=True,
        price_delta_note="$80 cheaper new",
        capability_delta_note="Slightly heavier, same core spec",
        product=full_valid_product_args(),
    )
    defaults.update(overrides)
    return RawPriorGenFinding(**defaults)


class FakePriorGenResearcher:
    """PriorGenResearcher test double: returns a fixed `RawPriorGen`,
    regardless of input, and records every call it actually received."""

    def __init__(self, raw: RawPriorGen):
        self._raw = raw
        self.calls: list[tuple] = []

    async def research(self, seeds, ledger, low_evidence_mode, budget, progress=None) -> RawPriorGen:
        self.calls.append((seeds, ledger, low_evidence_mode, budget))
        return self._raw


def test_fake_researcher_satisfies_prior_gen_researcher():
    assert isinstance(FakePriorGenResearcher(RawPriorGen(findings=[])), PriorGenResearcher)


# -- _admit_finding: §12.1/§12.6 omit/disclose split --------------------------


def test_admit_finding_silent_when_no_predecessor_found():
    """§12.1: 'No "not applicable" line for a first-generation product.'"""
    seed = make_product(name="Widget Pro")
    finding = make_finding(predecessor_found=False, worth_promoting=False, product=None)

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), default_ledger(), make_location(), False)

    assert product is None
    assert caveat is None  # absence of relevance — no caveat manufactured


def test_admit_finding_silent_when_not_worth_promoting():
    """§12.1: 'No mention of a prior generation whose discount doesn't
    justify the capability gap.'"""
    seed = make_product(name="Widget Pro")
    finding = make_finding(predecessor_found=True, worth_promoting=False, product=None)

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), default_ledger(), make_location(), False)

    assert product is None
    assert caveat is None


def test_admit_finding_silent_on_unknown_seed_name():
    finding = make_finding(seed_product_name="Ghost Product")
    product, caveat = _admit_finding(finding, {}, make_survey_report(), default_ledger(), make_location(), False)
    assert product is None
    assert caveat is None


def test_admit_finding_discloses_missing_product_data():
    """§12.6: 'A failed fetch or relaxed constraint is a limitation IN THE
    RESEARCH — always disclosed.' Worth-promoting-but-no-data is exactly
    that: a real research gap, not an absence of relevance."""
    seed = make_product(name="Widget Pro")
    finding = make_finding(product=None)

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), default_ledger(), make_location(), False)

    assert product is None
    assert caveat is not None
    assert "Widget Pro" in caveat


def test_admit_finding_discloses_ledger_rejection():
    seed = make_product(name="Widget Pro")
    finding = make_finding()
    bad_ledger = make_ledger(fetched=[PRICE_URL], seen=[REVIEW_URL])  # SPEC_URL missing

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), bad_ledger, make_location(), False)

    assert product is None
    assert caveat is not None
    assert "Widget Pro" in caveat
    assert "could not be sourced" in caveat


def test_admit_finding_discloses_malformed_product_dict():
    seed = make_product(name="Widget Pro")
    broken_args = full_valid_product_args()
    del broken_args["cons"]  # required key missing -> KeyError inside build_product_from_args
    finding = make_finding(product=broken_args)

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), default_ledger(), make_location(), False)

    assert product is None
    assert caveat is not None
    assert "malformed" in caveat


def test_admit_finding_builds_product_and_forces_structural_fields():
    seed = make_product(name="Widget Pro", cluster_key="mid-tier")
    finding = make_finding()

    product, caveat = _admit_finding(finding, {"Widget Pro": seed}, make_survey_report(), default_ledger(), make_location(), False)

    assert caveat is None
    assert product is not None
    assert product.name == "Widget Classic"
    # Structural fields overwritten regardless of what the model sent:
    assert product.generation == "prior"
    assert product.cluster_key == "mid-tier"  # inherited from the SEED, not the model's own
    assert product.role == "recommendation"


# -- run_prior_gen ----------------------------------------------------------


def test_run_prior_gen_short_circuits_on_no_current_gen_seeds():
    researcher = FakePriorGenResearcher(RawPriorGen(findings=[make_finding()]))
    prior_seed = make_product(name="Widget Classic", generation="prior")

    outcome = run(
        run_prior_gen(
            [prior_seed],
            make_survey_report(),
            FetchLedger(),
            make_location(),
            False,
            make_run_budget(),
            researcher,
        )
    )

    assert outcome.products == []
    assert outcome.caveats == []
    assert researcher.calls == []


def test_run_prior_gen_filters_to_current_generation_seeds():
    current_seed = make_product(name="Widget Pro", generation="current")
    prior_seed = make_product(name="Widget Classic", generation="prior")
    researcher = FakePriorGenResearcher(RawPriorGen(findings=[]))

    run(
        run_prior_gen(
            [current_seed, prior_seed],
            make_survey_report(),
            FetchLedger(),
            make_location(),
            False,
            make_run_budget(),
            researcher,
        )
    )

    assert len(researcher.calls) == 1
    seeds_passed = researcher.calls[0][0]
    assert [p.name for p in seeds_passed] == ["Widget Pro"]


def test_run_prior_gen_end_to_end_mixed_findings():
    seed = make_product(name="Widget Pro", cluster_key="mid-tier", generation="current")
    findings = [
        make_finding(seed_product_name="Widget Pro"),  # admitted
        make_finding(
            seed_product_name="Widget Pro",
            predecessor_found=False,
            worth_promoting=False,
            product=None,
        ),  # silent
    ]
    researcher = FakePriorGenResearcher(RawPriorGen(findings=findings))

    outcome = run(
        run_prior_gen(
            [seed],
            make_survey_report(),
            default_ledger(),
            make_location(),
            False,
            make_run_budget(),
            researcher,
        )
    )

    assert len(outcome.products) == 1
    assert outcome.products[0].generation == "prior"
    assert outcome.caveats == []  # the silent finding contributed nothing


def test_run_prior_gen_threads_low_evidence_mode_to_researcher():
    seed = make_product(name="Widget Pro", generation="current")
    researcher = FakePriorGenResearcher(RawPriorGen(findings=[]))
    run(
        run_prior_gen(
            [seed],
            make_survey_report(),
            FetchLedger(),
            make_location(),
            True,
            make_run_budget(),
            researcher,
        )
    )
    assert researcher.calls[0][2] is True


def test_run_prior_gen_threads_low_evidence_mode_to_admit_finding():
    """A community-sourced predecessor spec is rejected in standard mode
    and admitted in low-evidence mode — end to end through run_prior_gen,
    not just at _admit_finding's own boundary."""
    seed = make_product(name="Widget Pro", cluster_key="mid-tier", generation="current")
    community_args = full_valid_product_args(
        specs={
            "weight": {
                "value": "44 lb",
                "source_url": "https://forum.example.com/thread/1",
                "source_type": "community",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        }
    )
    finding = make_finding(product=community_args)
    ledger = make_ledger(fetched=[PRICE_URL, "https://forum.example.com/thread/1"], seen=[REVIEW_URL])
    researcher = FakePriorGenResearcher(RawPriorGen(findings=[finding]))

    standard_outcome = run(
        run_prior_gen(
            [seed], make_survey_report(), ledger, make_location(), False, make_run_budget(), researcher
        )
    )
    assert standard_outcome.products == []
    assert len(standard_outcome.caveats) == 1

    low_evidence_outcome = run(
        run_prior_gen(
            [seed], make_survey_report(), ledger, make_location(), True, make_run_budget(), researcher
        )
    )
    assert len(low_evidence_outcome.products) == 1
