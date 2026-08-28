"""Unit tests for location.py — spec docs/handoff.md §10, build order step 11."""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

from product_scout.location import (
    LANDED_PRICE_MATERIALITY_THRESHOLD,
    SHIPS_FROM_CONFIDENCE,
    SHIPS_FROM_UNCERTAIN_THRESHOLD,
    assess_location_impact,
    build_below_threshold_shipping_caveats,
    build_ships_from_uncertainty_caveat,
    build_tax_convention_caveats,
    is_confirmed_cross_border,
    resolve_landed_pricing,
    ships_from_confidence_for,
    validate_ships_from_signal,
)
from tests.conftest import make_availability, make_pricing_model, make_product

# -- §10.6 ships_from_confidence table ---------------------------------------


@pytest.mark.parametrize("signal,expected", list(SHIPS_FROM_CONFIDENCE.items()))
def test_ships_from_confidence_table_exhaustive(signal, expected):
    assert ships_from_confidence_for(signal) == expected


def test_ships_from_confidence_none_signal_is_zero():
    """No signal reported at all — the pre-step-11 placeholder state — reads
    as no evidence, same convention as confidence.py elsewhere."""
    assert ships_from_confidence_for(None) == 0.0


def test_only_shipping_policy_clears_the_uncertain_threshold():
    """§10.6: 'Only an explicit policy statement clears the threshold.'"""
    for signal, confidence in SHIPS_FROM_CONFIDENCE.items():
        cleared = confidence >= SHIPS_FROM_UNCERTAIN_THRESHOLD
        assert cleared == (signal == "shipping_policy")


def test_ships_from_confidence_is_a_pure_function_of_signal_alone():
    """§17.2: 'unit-test that derivation is a pure function of
    ships_from_signal.' Same signal in -> same confidence out, regardless of
    call order or any other state."""
    for signal in list(SHIPS_FROM_CONFIDENCE) + [None]:
        results = {ships_from_confidence_for(signal) for _ in range(5)}
        assert len(results) == 1


def test_ships_from_confidence_never_named_in_a_prompt_template():
    """§17.2: 'grep prompts for the field name.' Walks every module in
    product_scout.phases (where every *_PROMPT_TEMPLATE lives) and asserts
    the literal string never appears in any string constant — the model is
    told to report `ships_from_signal`, never a confidence number."""
    import product_scout.phases as phases_pkg

    phases_dir = Path(phases_pkg.__file__).parent
    for info in pkgutil.iter_modules([str(phases_dir)]):
        source = (phases_dir / f"{info.name}.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "ships_from_confidence" not in node.value, (
                    f"product_scout/phases/{info.name}.py contains "
                    "'ships_from_confidence' in a string constant — this "
                    "field must never be asked of a model (§10.6/invariant 4)"
                )


# -- cctld exclusion-list downgrade ------------------------------------------


def test_cctld_signal_with_geographic_tld_is_kept():
    assert validate_ships_from_signal("cctld", "https://example.de/shipping") == "cctld"


@pytest.mark.parametrize("url", ["https://example.io/shipping", "https://example.com/shipping"])
def test_cctld_signal_with_excluded_or_non_cc_tld_is_downgraded(url):
    """§10.6's exclusion list, enforced in code: a domain-hacked or generic
    TLD carries no geographic signal even if a model claims otherwise."""
    assert validate_ships_from_signal("cctld", url) == "fallback"


def test_cctld_signal_with_no_source_url_is_downgraded():
    assert validate_ships_from_signal("cctld", None) == "fallback"


@pytest.mark.parametrize("signal", ["shipping_policy", "currency", "language", "fallback", None])
def test_non_cctld_signals_pass_through_unchanged(signal):
    assert validate_ships_from_signal(signal, "https://example.io/x") == signal


# -- confirmed cross-border ---------------------------------------------------


def test_undetermined_origin_is_never_confirmed_cross_border():
    """§10.3: 'Undetermined origin produces no estimate.'"""
    assert is_confirmed_cross_border(None, "US") is False


def test_matching_country_is_not_cross_border():
    assert is_confirmed_cross_border("US", "US") is False
    assert is_confirmed_cross_border("us", "US") is False  # case-insensitive


def test_mismatched_country_is_cross_border():
    assert is_confirmed_cross_border("DE", "US") is True


# -- landed pricing: the "only when needed" gate -----------------------------


def test_not_confirmed_cross_border_nulls_everything():
    """Defensive: even if a model sent shipping/duty figures, Python
    discards them for a same-region product — 'only when needed' is
    enforced here, not trusted from the model."""
    result = resolve_landed_pricing(
        make_pricing_model(upfront_amount=100.0), False, 12.0, 8.0
    )
    assert result == (None, None, None)


def test_confirmed_cross_border_computes_landed_price():
    shipping, duty, landed = resolve_landed_pricing(
        make_pricing_model(upfront_amount=100.0), True, 12.0, 8.0
    )
    assert (shipping, duty, landed) == (12.0, 8.0, 120.0)


def test_confirmed_cross_border_missing_shipping_or_duty_treated_as_zero():
    _, _, landed = resolve_landed_pricing(
        make_pricing_model(upfront_amount=100.0), True, None, None
    )
    assert landed == 100.0


def test_confirmed_cross_border_no_upfront_amount_yields_no_landed_price():
    shipping, duty, landed = resolve_landed_pricing(
        make_pricing_model(upfront_amount=None), True, 12.0, 8.0
    )
    assert (shipping, duty, landed) == (12.0, 8.0, None)


# -- §10.4 materiality / §10.3a tax-convention suppression -------------------


def _cross_border_product(**overrides) -> "Product":  # noqa: F821 - typing convenience only
    defaults = dict(
        pricing=make_pricing_model(upfront_amount=100.0, price_tax_inclusive=True),
        availability=make_availability(
            sold_in_region=True,
            ships_from="DE",
            ships_from_signal="shipping_policy",
            ships_from_confidence=0.98,
            landed_price_native=100.0,
        ),
    )
    defaults.update(overrides)
    return make_product(**defaults)


def test_not_sold_in_region_is_always_material():
    product = make_product(availability=make_availability(sold_in_region=False))
    impact = assess_location_impact(product, budget_ceiling=None)
    assert impact.material is True


def test_below_threshold_landed_markup_is_not_material():
    product = _cross_border_product(
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=110.0,  # +10%, under the 15% threshold
        )
    )
    impact = assess_location_impact(product, budget_ceiling=None)
    assert impact.exceeds_threshold is False
    assert impact.material is False


def test_above_threshold_landed_markup_is_material():
    product = _cross_border_product(
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=120.0,  # +20%, over the 15% threshold
        )
    )
    impact = assess_location_impact(product, budget_ceiling=None)
    assert impact.exceeds_threshold is True
    assert impact.material is True


def test_landed_price_over_budget_ceiling_is_material():
    product = _cross_border_product(
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=105.0,
        )
    )
    impact = assess_location_impact(product, budget_ceiling=100.0)
    assert impact.pushes_outside_budget is True
    assert impact.material is True


def test_landed_price_within_budget_ceiling_is_not_pushed_outside():
    product = _cross_border_product(
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=105.0,
        )
    )
    impact = assess_location_impact(product, budget_ceiling=200.0)
    assert impact.pushes_outside_budget is False


def test_undetermined_tax_convention_suppresses_threshold_and_budget_checks():
    """§10.3a: 'None suppresses the §10.4 comparison entirely.'"""
    product = _cross_border_product(
        pricing=make_pricing_model(upfront_amount=100.0, price_tax_inclusive=None),
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=150.0,  # would clear both triggers if evaluated
        ),
    )
    impact = assess_location_impact(product, budget_ceiling=100.0)
    assert impact.exceeds_threshold is None
    assert impact.pushes_outside_budget is None
    assert impact.tax_convention_undetermined is True
    # not sold_in_region is untouched by the suppression, so material can
    # still be True/False independent of it — here sold_in_region=True and
    # both other triggers are suppressed, so material is False.
    assert impact.material is False


def test_no_landed_price_at_all_does_not_flag_tax_convention_undetermined():
    """An in-region product's unknown tax convention has nothing to
    suppress — §12.6's 'omit absence-of-relevance.'"""
    product = make_product(
        pricing=make_pricing_model(price_tax_inclusive=None),
        availability=make_availability(sold_in_region=True, landed_price_native=None),
    )
    impact = assess_location_impact(product, budget_ceiling=None)
    assert impact.tax_convention_undetermined is False


def test_materiality_threshold_constant_is_fifteen_percent():
    assert LANDED_PRICE_MATERIALITY_THRESHOLD == 0.15


# -- caveat builders -----------------------------------------------------


def test_ships_from_uncertainty_caveat_is_none_when_nothing_uncertain():
    products = [
        make_product(
            availability=make_availability(
                ships_from="DE", ships_from_signal="shipping_policy", ships_from_confidence=0.98
            )
        )
    ]
    assert build_ships_from_uncertainty_caveat(products) is None


def test_ships_from_uncertainty_caveat_is_none_when_no_product_has_a_ships_from():
    products = [make_product(availability=make_availability(ships_from=None))]
    assert build_ships_from_uncertainty_caveat(products) is None


def test_ships_from_uncertainty_caveat_class_collapses():
    """§5.5 rule 3 / §10.6: one line, counting instances, not one per row."""
    uncertain = make_product(
        name="A",
        availability=make_availability(
            ships_from="DE", ships_from_signal="cctld", ships_from_confidence=0.90
        ),
    )
    also_uncertain = make_product(
        name="B",
        availability=make_availability(
            ships_from="FR", ships_from_signal="currency", ships_from_confidence=0.70
        ),
    )
    certain = make_product(
        name="C",
        availability=make_availability(
            ships_from="GB", ships_from_signal="shipping_policy", ships_from_confidence=0.98
        ),
    )
    caveat = build_ships_from_uncertainty_caveat([uncertain, also_uncertain, certain])
    assert caveat is not None
    assert caveat.tier == "provenance"
    assert caveat.anchor is None
    assert caveat.instance_count == 2
    assert "2" in caveat.text and "3" in caveat.text


def test_below_threshold_caveat_fires_for_material_free_cross_border_product():
    """§10.4: 'Below the threshold: one caveat line' — distinct from both
    the always-present mundane note and the material-consequence path."""
    product = _cross_border_product(
        name="Below",
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=110.0,  # +10%, under the 15% threshold
        ),
    )
    caveats = build_below_threshold_shipping_caveats([product], budget_ceiling=None)
    assert len(caveats) == 1
    assert caveats[0].tier == "provenance"
    assert caveats[0].anchor == "Below"


def test_below_threshold_caveat_silent_when_material():
    product = _cross_border_product(
        name="Material",
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=120.0,  # +20%, over the threshold
        ),
    )
    assert build_below_threshold_shipping_caveats([product], budget_ceiling=None) == []


def test_below_threshold_caveat_silent_when_not_cross_border():
    product = make_product(availability=make_availability(sold_in_region=True, landed_price_native=None))
    assert build_below_threshold_shipping_caveats([product], budget_ceiling=None) == []


def test_below_threshold_caveat_silent_when_tax_convention_undetermined():
    """No double coverage: build_tax_convention_caveats already covers
    this product with its own, decision-affecting line."""
    product = _cross_border_product(
        name="Undetermined",
        pricing=make_pricing_model(upfront_amount=100.0, price_tax_inclusive=None),
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=110.0,
        ),
    )
    assert build_below_threshold_shipping_caveats([product], budget_ceiling=None) == []


def test_tax_convention_caveat_fires_only_for_confirmed_cross_border_undetermined():
    fires = _cross_border_product(
        name="Fires",
        pricing=make_pricing_model(upfront_amount=100.0, price_tax_inclusive=None),
        availability=make_availability(
            sold_in_region=True, ships_from="DE", ships_from_confidence=0.98,
            landed_price_native=110.0,
        ),
    )
    silent_in_region = make_product(
        name="Silent",
        pricing=make_pricing_model(price_tax_inclusive=None),
        availability=make_availability(sold_in_region=True, landed_price_native=None),
    )
    caveats = build_tax_convention_caveats([fires, silent_in_region])
    assert len(caveats) == 1
    assert caveats[0].tier == "decision_affecting"
    assert caveats[0].anchor == "Fires"
    assert "undetermined tax convention" in caveats[0].text
