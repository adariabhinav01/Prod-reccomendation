"""Location: the storefront inference ladder is derived, not judged (spec
docs/handoff.md §10, build order step 11).

Same governing principle `confidence.py` states for `EvidenceProfile.confidence`
(§4.1), restated by §10.6 for this domain and named specifically by CLAUDE.md
invariant 4: "The same principle governs `ships_from_confidence`." A model
reports the *signal* it observed (which rung of the §10.6 ladder it climbed) —
never the confidence number itself. Everything numeric in this module is a
pure function over already-reported, ledger-validated data, which is why all
of it is fully testable without a model call, exactly like `confidence.py`.

This module owns three distinct derivations, kept separate because they run
at different points in the pipeline and need different inputs:

1. **`ships_from_confidence_for`** — §10.6's fixed signal -> confidence table.
   Runs at EXTRACTION time (`tools/record_product.py`), the moment a model
   reports `ships_from_signal`.
2. **`resolve_landed_pricing`** — §10.3's "only when needed" gate plus the
   landed-price arithmetic. Also runs at EXTRACTION time, immediately after
   (1), since both need only the single product being recorded.
3. **`assess_location_impact`** — §10.4's three triggers (unavailable, >15%
   landed markup, pushes outside budget) and §10.3a's tax-convention
   suppression. Runs later, at SCORING time (`phases/scoring.py`), because
   only that phase has `IntakeAnswers.budget_ceiling` in scope.

`build_ships_from_uncertainty_caveat`/`build_tax_convention_caveats`/
`build_below_threshold_shipping_caveats` are a fourth, run-scoped
derivation (they need the *whole* product list, not one product) and are
genuinely different from everything else in this module: they return real
`Caveat` objects, not `list[str]`, because `render/report.py`'s
tiering/anchoring/class-collapse machinery (§5.5) already depends on
`Caveat.tier`/`anchor`/`instance_count` and a plain string can't carry that.
Like `phases/extraction.py`'s `run_extraction`/`phases/scoring.py`'s
`run_scoring`, they are pure functions with no caller yet — no
`orchestrator.py` exists in this codebase to assemble a final `RunRecord.caveats`
list from every phase's output. They are unit-tested standalone here, ready
for that future wiring.

§10.4's own text separates three distinct things this module must not
conflate: the mundane availability note every product gets regardless
(unconditional, already `render/report.py`'s job — nothing to generate
here), "one caveat line" for a confirmed cross-border product that stays
below the materiality threshold (`build_below_threshold_shipping_caveats`),
and the first-class-input consequence for a product that clears it
(`assess_location_impact`'s `material` flag, consumed by
`phases/scoring.py`'s `_reassign_role_for_location`).
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from product_scout.models import Caveat, PricingModel, Product

ShipsFromSignal = Literal["shipping_policy", "cctld", "currency", "language", "fallback"]

# §10.6's table — a fixed signal -> confidence lookup, never model-assigned.
SHIPS_FROM_CONFIDENCE: dict[str, float] = {
    "shipping_policy": 0.98,
    "cctld": 0.90,
    "currency": 0.70,
    "language": 0.40,
    "fallback": 0.25,
}

# §10.6: "Below 0.95, present the inference and mark it uncertain. Only an
# explicit policy statement clears the threshold" — `shipping_policy` (0.98)
# is the only table entry that clears this; every other signal stays uncertain
# by construction.
SHIPS_FROM_UNCERTAIN_THRESHOLD: float = 0.95

# §10.6: "a known-exclusion list for domain-hacked TLDs carrying no
# geographic signal."
CCTLD_EXCLUSION_TLDS: frozenset[str] = frozenset(
    {"io", "co", "ai", "tv", "me", "fm", "ly", "gg", "sh"}
)

# §10.4: "landed price exceeds native price by >15%."
LANDED_PRICE_MATERIALITY_THRESHOLD: float = 0.15


def ships_from_confidence_for(signal: str | None) -> float:
    """§10.6's fixed signal -> confidence table, and nothing else. `None`
    (no signal reported at all — the pre-step-11 placeholder state) yields
    `0.0`, the same "no evidence" reading `confidence.py`'s own functions use
    elsewhere in this codebase.

    Deliberately takes only `signal` — no `Product`, no `Availability`. §17.2
    asks for a unit test confirming this derivation is "a pure function of
    `ships_from_signal`"; a narrower parameter list is what makes that
    property structurally true rather than merely tested.
    """
    if signal is None:
        return 0.0
    return SHIPS_FROM_CONFIDENCE[signal]


def _domain_of(url: str) -> str:
    return urlsplit(url.strip()).netloc.lower().split(":")[0]


def _has_geographic_tld(url: str) -> bool:
    """A ccTLD carries real geographic signal only when it's a plausible
    ISO 3166-1 two-letter code AND not on the §10.6 domain-hacked exclusion
    list. `.com`/`.net`/`.org`/etc. (not two letters) and `.io`/`.ai`/etc.
    (two letters, but bought for the word, not the country) both fail this."""
    domain = _domain_of(url)
    if not domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    return len(tld) == 2 and tld not in CCTLD_EXCLUSION_TLDS


def validate_ships_from_signal(
    signal: ShipsFromSignal | None, source_url: str | None
) -> ShipsFromSignal | None:
    """A defensive downgrade, not a re-derivation: if a model claims
    `signal == "cctld"` but the domain it cited doesn't actually carry
    geographic signal (§10.6's exclusion list, or an obviously non-ccTLD
    like `.com`), downgrade to `"fallback"` rather than trust an
    unverifiable claim at face value.

    This is what gives the exclusion list real teeth — it's checked in code,
    not just stated in skill prose a model might misapply — mirroring
    invariant 3's own reasoning that "presence alone was never a
    hallucination guard," applied here to a signal claim instead of a
    source_url's mere existence. Every other signal passes through
    unchanged: there's no equivalent objective check for a currency/language/
    shipping-policy claim without re-fetching the page, which this module
    (pure, no I/O) deliberately does not do.
    """
    if signal == "cctld" and (not source_url or not _has_geographic_tld(source_url)):
        return "fallback"
    return signal


def is_confirmed_cross_border(ships_from: str | None, location_country: str) -> bool:
    """§10.2/§10.3: "Landed-cost research triggers only on a confirmed
    ships_from mismatch." `ships_from is None` (undetermined origin) is
    never confirmed — §10.3: "Undetermined origin produces no estimate."

    Case-insensitive exact match against `location_country` (an ISO
    3166-1 alpha-2 code). Documented limitation, in the same "flag the
    underspecified corner" style as `confidence.py`: there is no
    region-equivalence table, so a storefront reported as `"EU"` against a
    buyer in `"DE"` reads as cross-border even though it may not be. §10.6
    asks the model to report a country code "where determinable," which
    keeps this the common case; the multi-country-region case is a real,
    disclosed limitation, not a silent one.
    """
    if ships_from is None:
        return False
    return ships_from.strip().upper() != location_country.strip().upper()


def resolve_landed_pricing(
    pricing: PricingModel,
    confirmed_cross_border: bool,
    shipping_estimate_native: float | None,
    duty_estimate_native: float | None,
) -> tuple[float | None, float | None, float | None]:
    """§10.3's "only when needed" gate, enforced in Python regardless of
    what a model sent — "Undetermined origin produces no estimate" means
    exactly that: a model reporting `shipping_estimate_native` for a
    same-region product is discarded here, not trusted. Returns
    `(shipping_estimate_native, duty_estimate_native, landed_price_native)`.

    When confirmed cross-border, `landed_price_native` is
    `upfront_amount + shipping + duty` (treating an unreported shipping/duty
    component as `0.0`, since a model may confirm cross-border shipping
    without yet having a duty figure) — but only when `upfront_amount` is
    itself known; there's nothing to land otherwise.
    """
    if not confirmed_cross_border:
        return None, None, None

    if pricing.upfront_amount is None:
        return shipping_estimate_native, duty_estimate_native, None

    landed = pricing.upfront_amount + (shipping_estimate_native or 0.0) + (duty_estimate_native or 0.0)
    return shipping_estimate_native, duty_estimate_native, round(landed, 2)


class LocationImpact(BaseModel):
    """§10.4's verdict on a single product: does location matter enough to
    become "a first-class input to scoring, flip points, and table
    placement," or is it just the mundane availability note every product
    gets regardless? Computed in `phases/scoring.py` (needs
    `IntakeAnswers.budget_ceiling`, not in scope for a single-product
    EXTRACTION-time function) via `assess_location_impact` below."""

    sold_in_region: bool
    tax_convention_undetermined: bool
    exceeds_threshold: bool | None  # None when the %-check couldn't run (§10.3a)
    pushes_outside_budget: bool | None  # None when no budget ceiling was given
    material: bool  # the OR of all three §10.4 triggers


def assess_location_impact(product: Product, budget_ceiling: float | None) -> LocationImpact:
    """§10.4's three triggers: not sold in-region, landed price >15% over
    native, or landed price outside the stated budget. The second and third
    "require `price_tax_inclusive is not None`" (§10.3a) — modeled here as a
    single flag, since a product's landed price is arithmetically derived
    from its own `upfront_amount` (§10.3's `resolve_landed_pricing`), so
    native and landed share one tax convention by construction; there is no
    second, independently-flagged price to compare against for this
    single-product check (contrast §11.3's TCO suppression, which compares
    *two different products'* `price_tax_inclusive` values).
    """
    availability = product.availability
    pricing = product.pricing
    tax_undetermined = pricing.price_tax_inclusive is None

    exceeds_threshold: bool | None = None
    pushes_outside_budget: bool | None = None

    landed = availability.landed_price_native
    if landed is not None and pricing.upfront_amount and not tax_undetermined:
        exceeds_threshold = (
            landed - pricing.upfront_amount
        ) / pricing.upfront_amount > LANDED_PRICE_MATERIALITY_THRESHOLD
        if budget_ceiling is not None:
            pushes_outside_budget = landed > budget_ceiling

    material = (
        not availability.sold_in_region
        or bool(exceeds_threshold)
        or bool(pushes_outside_budget)
    )

    return LocationImpact(
        sold_in_region=availability.sold_in_region,
        tax_convention_undetermined=tax_undetermined and landed is not None,
        exceeds_threshold=exceeds_threshold,
        pushes_outside_budget=pushes_outside_budget,
        material=material,
    )


def build_ships_from_uncertainty_caveat(products: list[Product]) -> Caveat | None:
    """§10.6: "Because this flag attaches to most rows, it is a
    class-collapsed caveat per §5.5 rule 3" — one line, not one per product:
    *"shipping origin inferred rather than confirmed for N of M products."*

    Counts a product as uncertain when it has a reported `ships_from` at all
    (nothing to be uncertain ABOUT otherwise) and its confidence sits below
    `SHIPS_FROM_UNCERTAIN_THRESHOLD`. Returns `None` when no product both has
    a `ships_from` and is uncertain — §5.5's "a caveats section nobody reads
    is worth the same as no caveats" argues against an always-present line
    that's usually vacuous.
    """
    with_ships_from = [p for p in products if p.availability.ships_from is not None]
    uncertain = [
        p for p in with_ships_from if p.availability.ships_from_confidence < SHIPS_FROM_UNCERTAIN_THRESHOLD
    ]
    if not uncertain:
        return None
    return Caveat(
        tier="provenance",
        text=(
            f"Shipping origin inferred rather than confirmed for {len(uncertain)} "
            f"of {len(with_ships_from)} product(s) with a determined storefront."
        ),
        anchor=None,
        instance_count=len(uncertain),
    )


def build_tax_convention_caveats(products: list[Product]) -> list[Caveat]:
    """§10.3a, verbatim: *"prices for this product are quoted under an
    undetermined tax convention; cross-border cost comparison suppressed."*
    Decision-affecting (renders inline next to the product, per §5.5 rule 1)
    because it explains why the §10.4 comparison it's attached to is
    missing, not just a provenance footnote.

    Fires only when the suppression actually matters: a confirmed
    cross-border product with a landed price computed but an undetermined
    tax convention. An in-region product's `price_tax_inclusive` being
    unknown has nothing to suppress (§10.4's comparison never runs for it
    either way), so it stays silent — §12.6's "omit absence-of-relevance"
    principle, applied here.
    """
    caveats = []
    for product in products:
        impact = assess_location_impact(product, budget_ceiling=None)
        if impact.tax_convention_undetermined:
            caveats.append(
                Caveat(
                    tier="decision_affecting",
                    text=(
                        "Prices for this product are quoted under an undetermined "
                        "tax convention; cross-border cost comparison suppressed."
                    ),
                    anchor=product.name,
                    instance_count=1,
                )
            )
    return caveats


def build_below_threshold_shipping_caveats(
    products: list[Product], budget_ceiling: float | None
) -> list[Caveat]:
    """§10.4: "Below the threshold: one caveat line." Distinct from two
    other things this module already covers: the mundane availability
    note every product gets regardless (§10.4's first sentence — already
    unconditional in `render/report.py`'s `_render_availability_section`,
    nothing to add here), and the first-class-input consequence a MATERIAL
    product gets (`phases/scoring.py`'s `_reassign_role_for_location`). This
    is the third, narrower case: a confirmed cross-border product whose
    landed price doesn't clear either §10.4 trigger still gets one quiet,
    per-product note — not promoted to a scoring input, but not silently
    dropped either.

    Provenance-tier, not decision-affecting: by construction this only
    fires for a product that did NOT clear a materiality trigger, so
    nothing here should change the recommendation — it collapses into the
    §5.6 disclosure section (§5.5 rule 2) rather than rendering inline.

    Silent when the tax convention is undetermined — `build_tax_convention_caveats`
    already covers that product with its own, decision-affecting line, and
    covering the same product twice is exactly the caveat-overrun §5.5
    exists to prevent.
    """
    caveats = []
    for product in products:
        impact = assess_location_impact(product, budget_ceiling)
        landed = product.availability.landed_price_native
        if landed is None or impact.material or impact.tax_convention_undetermined:
            continue
        caveats.append(
            Caveat(
                tier="provenance",
                text=(
                    f"Ships from outside your region (estimated landed price "
                    f"{landed:,.2f}); this doesn't materially change the "
                    "comparison."
                ),
                anchor=product.name,
                instance_count=1,
            )
        )
    return caveats
