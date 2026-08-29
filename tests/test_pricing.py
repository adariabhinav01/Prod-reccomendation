"""Unit tests for pricing.py — the §16 price-override arithmetic (build
order step 14). No formula for these existed anywhere in the codebase
before this step; these tests pin the exact behavior."""

from datetime import datetime, timezone

import pytest

from product_scout.pricing import (
    OVERRIDABLE_MODEL_TYPES,
    PriceOverrideError,
    apply_price_override,
    recompute_in_budget,
    recompute_total_cost_1yr,
)
from tests.conftest import make_availability, make_location, make_pricing_model, make_product

OBSERVED_AT = datetime(2026, 8, 28, tzinfo=timezone.utc)


# -- recompute_total_cost_1yr --------------------------------------------


def test_recompute_total_cost_1yr_pure_one_time_is_just_the_new_price():
    pricing = make_pricing_model(model_type="one_time", recurring_amount=None)
    assert recompute_total_cost_1yr(pricing, 250.0) == 250.0


def test_recompute_total_cost_1yr_adds_required_monthly_subscription():
    pricing = make_pricing_model(
        model_type="one_time_plus_subscription",
        recurring_amount=10.0,
        recurring_period="monthly",
        recurring_required_for_core=True,
    )
    assert recompute_total_cost_1yr(pricing, 100.0) == 100.0 + 10.0 * 12


def test_recompute_total_cost_1yr_adds_required_annual_subscription():
    pricing = make_pricing_model(
        model_type="one_time_plus_subscription",
        recurring_amount=50.0,
        recurring_period="annual",
        recurring_required_for_core=True,
    )
    assert recompute_total_cost_1yr(pricing, 100.0) == 150.0


def test_recompute_total_cost_1yr_ignores_optional_addon_subscription():
    pricing = make_pricing_model(
        model_type="one_time_plus_subscription",
        recurring_amount=10.0,
        recurring_period="monthly",
        recurring_required_for_core=False,
    )
    assert recompute_total_cost_1yr(pricing, 100.0) == 100.0


# -- recompute_in_budget ---------------------------------------------------


def test_recompute_in_budget_true_when_no_ceiling():
    product = make_product()
    assert recompute_in_budget(product, None) is True


def test_recompute_in_budget_prefers_landed_price():
    product = make_product(
        availability=make_availability(landed_price_native=500.0),
        pricing=make_pricing_model(upfront_amount=100.0, total_cost_1yr=100.0),
    )
    assert recompute_in_budget(product, 300.0) is False


def test_recompute_in_budget_falls_back_to_total_cost_1yr():
    product = make_product(
        availability=make_availability(landed_price_native=None),
        pricing=make_pricing_model(upfront_amount=100.0, total_cost_1yr=400.0),
    )
    assert recompute_in_budget(product, 300.0) is False


def test_recompute_in_budget_falls_back_to_upfront_amount():
    product = make_product(
        availability=make_availability(landed_price_native=None),
        pricing=make_pricing_model(upfront_amount=250.0, total_cost_1yr=None),
    )
    assert recompute_in_budget(product, 300.0) is True


# -- apply_price_override ---------------------------------------------------


def test_apply_price_override_sets_upfront_and_recomputes_total_cost_1yr():
    product = make_product(pricing=make_pricing_model(model_type="one_time", upfront_amount=199.0))
    updated = apply_price_override(product, 149.0, make_location(), budget_ceiling=None, observed_at=OBSERVED_AT)

    assert updated.pricing.upfront_amount == 149.0
    assert updated.pricing.total_cost_1yr == 149.0
    assert updated.pricing.price_overridden is True
    assert updated.pricing.price_observed_at == OBSERVED_AT


def test_apply_price_override_does_not_mutate_the_original_product():
    product = make_product(pricing=make_pricing_model(upfront_amount=199.0))
    apply_price_override(product, 149.0, make_location(), budget_ceiling=None, observed_at=OBSERVED_AT)
    assert product.pricing.upfront_amount == 199.0
    assert product.pricing.price_overridden is False


def test_apply_price_override_recomputes_in_budget():
    product = make_product(pricing=make_pricing_model(upfront_amount=350.0, total_cost_1yr=350.0), in_budget=False)
    updated = apply_price_override(product, 100.0, make_location(), budget_ceiling=300.0, observed_at=OBSERVED_AT)
    assert updated.in_budget is True


def test_apply_price_override_recomputes_landed_price_for_confirmed_cross_border():
    product = make_product(
        pricing=make_pricing_model(upfront_amount=100.0),
        availability=make_availability(ships_from="DE", landed_price_native=130.0),
    )
    updated = apply_price_override(
        product, 200.0, make_location(country="US", currency="USD"), budget_ceiling=None, observed_at=OBSERVED_AT
    )
    assert updated.availability.landed_price_native == 200.0


def test_apply_price_override_no_landed_price_when_not_cross_border():
    product = make_product(
        pricing=make_pricing_model(upfront_amount=100.0),
        availability=make_availability(ships_from="US"),
    )
    updated = apply_price_override(
        product, 200.0, make_location(country="US", currency="USD"), budget_ceiling=None, observed_at=OBSERVED_AT
    )
    assert updated.availability.landed_price_native is None


@pytest.mark.parametrize("model_type", ["subscription_only", "usage_based"])
def test_apply_price_override_refuses_non_overridable_model_types(model_type):
    product = make_product(pricing=make_pricing_model(model_type=model_type, upfront_amount=None))
    with pytest.raises(PriceOverrideError):
        apply_price_override(product, 20.0, make_location(), budget_ceiling=None, observed_at=OBSERVED_AT)


def test_overridable_model_types_are_exactly_the_upfront_bearing_ones():
    assert OVERRIDABLE_MODEL_TYPES == {
        "one_time",
        "one_time_plus_subscription",
        "freemium",
        "financed_major_purchase",
    }
