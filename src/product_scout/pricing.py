"""Price-override arithmetic for `rescore` (spec docs/handoff.md §16; build
order step 14).

§16's "Override semantics" paragraph names three fields that must
**recompute** when `rescore` overrides a price — `in_budget`,
`total_cost_1yr`, `landed_price_native` — but specifies no formula for any
of them. `landed_price_native` is the one exception: `location.py`'s
`resolve_landed_pricing`/`is_confirmed_cross_border` already do that
arithmetic (extraction time uses them too), so this module only composes
them, it doesn't reinvent them. `total_cost_1yr` and `in_budget` genuinely
have no prior formula anywhere in this codebase — both are Haiku's own
extraction-time judgment calls today (`tools/record_product.py`) — so the
functions below are new, pure, and unit-tested in isolation, the same way
`location.py`'s functions are.

**Override scope is deliberately narrow.** `--set "Name=199"` only
overrides `PricingModel.upfront_amount`, so it only makes sense for a
pricing model that HAS one as its headline number:  `one_time`,
`one_time_plus_subscription`, `freemium`, `financed_major_purchase`.
`subscription_only`/`usage_based` products have no single number `--set`
could sensibly replace (a subscription's headline is `recurring_amount`; a
usage-based product has no fixed price at all) — `apply_price_override`
refuses on those rather than guessing which field the user meant.
"""

from __future__ import annotations

from datetime import datetime

from product_scout.location import is_confirmed_cross_border, resolve_landed_pricing
from product_scout.models import Location, PricingModel, Product

# The pricing models `--set` can override — every model_type whose headline
# number is `upfront_amount`. See module docstring.
OVERRIDABLE_MODEL_TYPES: frozenset[str] = frozenset(
    {"one_time", "one_time_plus_subscription", "freemium", "financed_major_purchase"}
)


class PriceOverrideError(ValueError):
    """A `--set` override that can't be applied — an unknown product name,
    or a product whose `model_type` isn't in `OVERRIDABLE_MODEL_TYPES`.
    Subclasses `ValueError` so `cli.py` can catch input-validation failures
    generically, mirroring `settings.SettingsError`'s own precedent."""


def recompute_total_cost_1yr(pricing: PricingModel, new_upfront: float) -> float | None:
    """`upfront + (recurring × periods/yr)`, counting the recurring
    component only when it's required for core use (`recurring_required_for_core`)
    — an optional add-on subscription isn't part of the product's own cost
    of ownership. `new_upfront` is passed explicitly rather than read off
    `pricing.upfront_amount` so this stays a pure function of its
    arguments, callable before the override has actually been written into
    a `PricingModel` — the same shape `apply_price_override` needs it in."""
    total = new_upfront
    if pricing.recurring_required_for_core and pricing.recurring_amount is not None:
        periods_per_year = 12 if pricing.recurring_period == "monthly" else 1
        total += pricing.recurring_amount * periods_per_year
    return round(total, 2)


def recompute_in_budget(product: Product, budget_ceiling: float | None) -> bool:
    """No budget ceiling means nothing is ever out of budget. Otherwise
    compares whichever price is the most complete picture of what the
    buyer actually pays: `landed_price_native` (confirmed cross-border
    total) if set, else `total_cost_1yr`, else the bare `upfront_amount`.
    Mirrors the same "compare the landed price when there is one"
    precedent as `location.assess_location_impact`'s
    `pushes_outside_budget` check."""
    if budget_ceiling is None:
        return True
    availability = product.availability
    pricing = product.pricing
    effective_price = (
        availability.landed_price_native
        if availability.landed_price_native is not None
        else pricing.total_cost_1yr
        if pricing.total_cost_1yr is not None
        else pricing.upfront_amount
    )
    if effective_price is None:
        return True
    return effective_price <= budget_ceiling


def apply_price_override(
    product: Product,
    new_price: float,
    location: Location,
    budget_ceiling: float | None,
    observed_at: datetime,
) -> Product:
    """The composed rescore operation for one product: validate the
    model_type is overridable, write the new `upfront_amount`, recompute
    `total_cost_1yr`/`landed_price_native`/`in_budget`, and stamp
    `price_observed_at`/`price_overridden` — all in one immutable update,
    returning a new `Product` (matches `phases/scoring.py`'s
    `_apply_raw_scores` model_copy style; nothing here mutates its input).
    """
    pricing = product.pricing
    if pricing.model_type not in OVERRIDABLE_MODEL_TYPES:
        raise PriceOverrideError(
            f"'{product.name}' has pricing model_type={pricing.model_type!r}, which "
            "has no single overridable price — rescore only supports "
            f"{sorted(OVERRIDABLE_MODEL_TYPES)}."
        )

    new_total_cost_1yr = recompute_total_cost_1yr(pricing, new_price)
    new_pricing = pricing.model_copy(
        update={
            "upfront_amount": new_price,
            "total_cost_1yr": new_total_cost_1yr,
            "price_observed_at": observed_at,
            "price_overridden": True,
        }
    )

    availability = product.availability
    confirmed_cross_border = is_confirmed_cross_border(availability.ships_from, location.country)
    shipping, duty, landed = resolve_landed_pricing(
        new_pricing,
        confirmed_cross_border,
        availability.shipping_estimate_native,
        availability.duty_estimate_native,
    )
    new_availability = availability.model_copy(
        update={
            "shipping_estimate_native": shipping,
            "duty_estimate_native": duty,
            "landed_price_native": landed,
        }
    )

    updated = product.model_copy(update={"pricing": new_pricing, "availability": new_availability})
    return updated.model_copy(update={"in_budget": recompute_in_budget(updated, budget_ceiling)})
