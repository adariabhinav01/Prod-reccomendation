"""`record_product` — the EXTRACTION-phase MCP tool (spec docs/handoff.md
§3/§4/§4.3, build order step 8).

Rewritten against the current (v7) `Product`/`PricingModel`/`Availability`/
`SourcedValue`/`EvidenceProfile` schemas in `models.py` — the version this
replaces was built at build order step 5 against a pre-v7 shape
(`price_usd`, a flat `price_source_url`, integer `source_tier`, a model-
reported `independent_review_count`/`has_methodology_backed_source`) that
no longer matches `models.py` at all; `phases/survey.py`'s own docstring
already flagged this file as carrying that debt.

This remains the primary enforcement point for two invariants, exactly as
before, plus a third this build step adds:

- **Invariant 3 ("no source, no field").** Every `SourcedValue`
  (`specs[*]`, `ownership_notes[*]`) and `pricing.price_source_url` must be
  a real, well-formed `http(s)` URL — `SourcedValue`'s own validator
  (`models.py`) already enforces the *shape*; rejected calls name every
  bad field so the model can drop them and re-call once.
- **Invariant 4 ("confidence is computed in Python, never model-
  assigned").** `RECORD_PRODUCT_SCHEMA` has no `confidence`,
  `corroboration_ratio`, `conflict_ratio`, `independent_review_count`, or
  `has_methodology_backed_source` property at all, and sets
  `"additionalProperties": false` throughout — the model cannot supply any
  of these even if it tried. Unlike the file this replaces,
  `independent_review_count`/`has_methodology_backed_source` are no longer
  model-reported inputs either: `confidence.py`'s `build_evidence_profile`
  (build order step 1) derives *both* straight from `review_sources`/
  `specs`/`ownership_notes`, which this handler already validates. A
  model that can't set confidence but *could* set its own review count
  wouldn't actually be out of the loop — CLAUDE.md invariant 4 names this
  exact failure mode.
- **§4.3 ledger validation (this build step).** Every `source_url` is
  checked against a run-scoped `hooks.ledger.FetchLedger` before
  construction — presence of a well-formed URL was never a hallucination
  guard (a model can invent a plausible one); only a ledger the model
  didn't write to itself can be. Specs and price require a `fetched`
  entry (§4.3: "Spec values require a fetched entry. A search snippet is
  not a page."); `ownership_notes` and `review_sources` — both judgment-
  bearing per §14 — accept either `fetched` or `seen_not_fetched`.

### `Availability` and `role` are intentionally minimal here

`Availability`'s full shape (`ships_from`, `ships_from_signal`, the §10.6
inference ladder, landed pricing) is explicitly a later, separate build
step ("11. Location end to end") — this handler accepts only
`sold_in_region` from the model and fills the rest with the "not yet
determined" defaults (`ships_from_confidence=0.0` — never model-supplied;
CLAUDE.md invariant 4 names this field specifically alongside
`confidence`). `role` defaults to `Product`'s own default
(`"recommendation"`) when the model omits it; baseline/reference role-
tagging logic (§7.2) isn't wired at the prompt level in this step either.

### Construction is two-pass: placeholder evidence, then the real one

`build_evidence_profile(product, survey, ...)` takes an already-built
`Product` — it reads `product.specs`/`ownership_notes`/`review_sources`
directly (§4.0d). So a *draft* `Product` is built first with a coherent-
but-empty placeholder `EvidenceProfile` (`source_count=0`, both ratios
`0.0` — trivially satisfies `EvidenceProfile`'s own coherence validator),
then `build_evidence_profile` computes the real one from that draft, and
`model_copy(update={"evidence": ...})` produces the final `Product`. This
mirrors `confidence.py`'s own two-step "provisional, then reassign"
pattern rather than inventing a second one.

### `build_product_from_args` is a public, reusable core (added build order
### step 10)

The validation body below — every §4.3 ledger check, the two-pass
evidence construction above, invariant 6's cons check — used to live
entirely inside the `record_product` tool closure. `phases/prior_gen.py`
(build order step 10) needs the *exact same* validation contract applied
to a prior-generation candidate parsed from a final JSON message rather
than a live tool call (§3's own `PHASES` sample grants `prior_gen` no
`record_product` tool, so there is no tool-call boundary to validate at).
Rather than re-implement ~80 lines of invariant-critical logic a second
time — the exact drift risk §4.3 itself warns about for `normalize_url`
("reimplementing... would risk the two drifting apart") — this function
is extracted as the shared, public core both call sites use.
`build_product_from_args` is pure and side-effect-free (no `ProductSink`
dependency): it returns `(Product, None)` on success or `(None,
error_text)` on failure, with `error_text` identical to what
`record_product`'s tool response always carried, so the tool wrapper below
is now a thin adapter from that tuple to the MCP response-dict shape —
existing tests, which only inspect the response dict, are unaffected by
this refactor. `phases/prior_gen.py` gets no such retry loop (a single
final message, not a live conversation), so it treats `None` as "drop this
candidate, log one caveat" rather than re-prompting — see that module's
docstring.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import tool
from pydantic import ValidationError

from product_scout.confidence import build_evidence_profile
from product_scout.hooks.ledger import FetchLedger
from product_scout.models import (
    Availability,
    EvidenceProfile,
    PricingModel,
    Product,
    SourcedValue,
    SurveyReport,
)

_PLACEHOLDER_EVIDENCE = EvidenceProfile(
    source_count=0,
    independent_review_count=0,
    extracted_spec_count=0,
    has_tier1_specs=False,
    has_methodology_backed_source=False,
    corroboration_ratio=0.0,
    conflict_ratio=0.0,
    recency_factor=0.0,
    confidence=0.0,
    confidence_note="",
)


@runtime_checkable
class ProductSink(Protocol):
    """Where a validated `Product` goes once `record_product` accepts it."""

    def add(self, product: Product) -> None: ...


class ListProductSink:
    """Trivial concrete `ProductSink` — shared by the real extractor adapter
    (`phases/extraction.py`'s `SdkExtractor`) and by tests."""

    def __init__(self) -> None:
        self.products: list[Product] = []

    def add(self, product: Product) -> None:
        self.products.append(product)


_SOURCED_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {"type": "string"},
        "native_unit": {"type": ["string", "null"]},
        "source_url": {"type": "string"},
        "source_type": {
            "type": "string",
            "enum": ["manufacturer", "testing_outlet", "aggregator", "retailer", "community"],
        },
        "has_stated_methodology": {"type": "boolean"},
        "corroborated_by": {"type": "array", "items": {"type": "string"}},
        "conflicting_values": {"type": "array", "items": {"type": "string"}},
        "observed_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 timestamp for when this value was observed.",
        },
    },
    "required": ["value", "source_url", "source_type", "has_stated_methodology", "observed_at"],
}

_PRICING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model_type": {
            "type": "string",
            "enum": [
                "one_time",
                "subscription_only",
                "one_time_plus_subscription",
                "freemium",
                "usage_based",
                "financed_major_purchase",
            ],
        },
        "upfront_amount": {"type": ["number", "null"]},
        "recurring_amount": {"type": ["number", "null"]},
        "recurring_period": {"type": ["string", "null"], "enum": ["monthly", "annual", None]},
        "recurring_required_for_core": {"type": "boolean"},
        "total_cost_1yr": {
            "type": ["number", "null"],
            "description": "Null for usage_based and financed_major_purchase model types (§11.3).",
        },
        "price_currency": {"type": "string"},
        "price_tax_inclusive": {
            "type": ["boolean", "null"],
            "description": "Null when undetermined (§10.3a) — never guess.",
        },
        "price_source_url": {"type": "string"},
        "price_observed_at": {"type": "string", "format": "date-time"},
    },
    "required": ["model_type", "price_currency", "price_source_url", "price_observed_at"],
}

_AVAILABILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sold_in_region": {"type": "boolean"},
    },
    "required": ["sold_in_region"],
}

RECORD_PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,  # invariant 4: no confidence/ratios/review-count smuggled in
    "properties": {
        "name": {"type": "string"},
        "brand": {"type": "string"},
        "generation": {"type": "string", "enum": ["current", "prior"]},
        "role": {
            "type": "string",
            "enum": [
                "recommendation",
                "baseline_current",
                "reference_above_budget",
                "reference_unavailable",
                "reference_displaced",
            ],
        },
        "cluster_key": {"type": "string"},
        "cluster_rationale": {"type": "string"},
        "strength_archetype": {"type": "string"},
        "pricing": _PRICING_SCHEMA,
        "availability": _AVAILABILITY_SCHEMA,
        "specs": {
            "type": "object",
            "description": (
                "Spec name -> sourced value. Omit a spec entirely if you "
                "cannot cite a real http(s) source_url you actually "
                "fetched — never invent one (invariant 3: no source, no "
                "field). A source_url you only saw in search results, "
                "never fetched, is not admissible here."
            ),
            "additionalProperties": _SOURCED_VALUE_SCHEMA,
        },
        "pros": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "cons": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "At least one real drawback — this app is not a sales tool.",
        },
        "ownership_notes": {
            "type": "array",
            "items": _SOURCED_VALUE_SCHEMA,
            "description": (
                "Community sentiment on reliability, longevity, ownership "
                "experience (§14) — a source_url only seen in search "
                "results is admissible here, unlike specs."
            ),
        },
        "review_sources": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Distinct sources you actually used for judgment.",
        },
        "in_budget": {"type": "boolean"},
    },
    "required": [
        "name",
        "brand",
        "generation",
        "cluster_key",
        "cluster_rationale",
        "strength_archetype",
        "pricing",
        "availability",
        "specs",
        "pros",
        "cons",
        "review_sources",
        "in_budget",
    ],
}


def _ledger_rejection_reason(ledger: FetchLedger, url: str) -> str:
    """Human-readable reason for a §4.3 ledger rejection — distinguishes
    "never seen at all" from "seen but only via search," since the second
    is specifically what a model should learn to fix by fetching the page
    rather than by finding a different citation entirely."""
    mode = ledger.mode_for(url)
    if mode == "seen_not_fetched":
        return (
            "was only seen in search results, never fetched — spec/price "
            "values require a real fetch, not a snippet (§4.3)"
        )
    return (
        "was never fetched or searched in this run — the ledger has no "
        "record of it (§4.3); a well-formed URL is not enough, since a "
        "model can invent a plausible one"
    )


def _validate_sourced_value(
    raw: dict[str, Any], ledger: FetchLedger, *, require_fetched: bool, label: str
) -> tuple[SourcedValue | None, str | None]:
    """Shape first (via `SourcedValue`'s own validator), then §4.3 ledger
    admissibility — in that order, so a structurally-invalid URL ("N/A",
    empty, non-http) gets pydantic's own specific message rather than the
    less-actionable "not in ledger" one."""
    try:
        sourced_value = SourcedValue(**raw)
    except ValidationError as e:
        return None, f"{label}: {e}"

    if not ledger.is_admissible(sourced_value.source_url, require_fetched=require_fetched):
        return None, f"{label}.source_url {_ledger_rejection_reason(ledger, sourced_value.source_url)}"

    return sourced_value, None


def build_product_from_args(
    args: dict[str, Any], survey: SurveyReport, ledger: FetchLedger
) -> tuple[Product | None, str | None]:
    """Validate `record_product`-shaped `args` and construct a `Product` —
    every §4.3 ledger check, invariant 6's cons check, and the two-pass
    evidence construction (§4.0d). See module docstring's "public,
    reusable core" section for why this is a standalone function rather
    than living inside the tool closure below.

    Returns `(product, None)` on success or `(None, error_text)` on
    failure, where `error_text` is the exact rejection message the
    `record_product` tool has always returned (so callers needing that
    live-retry framing — the tool wrapper below — get it verbatim; callers
    without a retry loop, like `phases/prior_gen.py`, still get an
    actionable, specific reason to log).
    """
    bad_fields: list[str] = []

    # 1. specs — require a `fetched` ledger entry (§4.3: spec values
    #    are facts, never judgment).
    specs: dict[str, SourcedValue] = {}
    for spec_name, raw in args["specs"].items():
        sourced_value, error = _validate_sourced_value(
            raw, ledger, require_fetched=True, label=f"specs[{spec_name!r}]"
        )
        if error:
            bad_fields.append(error)
        else:
            specs[spec_name] = sourced_value  # type: ignore[assignment]

    # 2. ownership_notes — judgment-bearing (§14): either access mode
    #    admissible.
    ownership_notes: list[SourcedValue] = []
    for i, raw in enumerate(args.get("ownership_notes", [])):
        sourced_value, error = _validate_sourced_value(
            raw, ledger, require_fetched=False, label=f"ownership_notes[{i}]"
        )
        if error:
            bad_fields.append(error)
        else:
            ownership_notes.append(sourced_value)  # type: ignore[arg-type]

    # 3. price_source_url — require fetched, same tier as specs.
    pricing_raw = args["pricing"]
    price_source_url = pricing_raw.get("price_source_url", "")
    if not ledger.is_admissible(price_source_url, require_fetched=True):
        bad_fields.append(
            f"pricing.price_source_url {_ledger_rejection_reason(ledger, price_source_url)}"
        )

    # 4. review_sources — bare URLs, judgment-bearing (used for
    #    independent_review_count, §14): either access mode admissible.
    for i, url in enumerate(args["review_sources"]):
        if not ledger.is_admissible(url, require_fetched=False):
            bad_fields.append(f"review_sources[{i}] {_ledger_rejection_reason(ledger, url)}")

    if bad_fields:
        return None, (
            "record_product rejected — the following "
            "field(s) are missing or not admissible: "
            + "; ".join(bad_fields)
            + ". Omit an unfixable field entirely (invariant 3: no "
            "source, no field), or fetch the page and re-cite it "
            "if you only searched, then call record_product again."
        )

    # 5. pricing / availability. Availability deliberately minimal —
    #    see module docstring.
    try:
        pricing = PricingModel(**pricing_raw)
    except ValidationError as e:
        return None, f"record_product rejected: pricing: {e}"

    availability = Availability(
        sold_in_region=args["availability"]["sold_in_region"],
        regional_names=[],
        ships_to_region=None,
        ships_from=None,
        ships_from_signal=None,
        ships_from_confidence=0.0,  # never model-supplied; invariant 4
        import_caveats=[],
    )

    # 6. Draft Product with placeholder evidence, so
    #    build_evidence_profile has something to derive from (§4.0d).
    try:
        draft = Product(
            name=args["name"],
            brand=args["brand"],
            generation=args["generation"],
            role=args.get("role", "recommendation"),
            cluster_key=args["cluster_key"],
            cluster_rationale=args["cluster_rationale"],
            strength_archetype=args["strength_archetype"],
            pricing=pricing,
            availability=availability,
            specs=specs,
            pros=args["pros"],
            cons=args["cons"],
            ownership_notes=ownership_notes,
            review_sources=args["review_sources"],
            in_budget=args["in_budget"],
            evidence=_PLACEHOLDER_EVIDENCE,
        )
    except ValidationError as e:
        return None, (
            f"record_product rejected: {e}. If this is about cons, add at least one "
            "real drawback (invariant 4: every product needs at least one con)."
        )

    # 7. Real EvidenceProfile, computed in Python — never model-supplied
    #    (invariant 4). This is what makes independent_review_count and
    #    has_methodology_backed_source safe to drop from the schema
    #    entirely rather than accept-then-ignore.
    evidence = build_evidence_profile(draft, survey)
    product = draft.model_copy(update={"evidence": evidence})
    return product, None


def make_record_product(sink: ProductSink, survey: SurveyReport, ledger: FetchLedger):
    """Factory mirroring `phases/refine.py`'s `Refiner`-seam-style
    dependency injection. `survey` is needed for
    `build_evidence_profile`'s `comparison_specs`/`category_kind` (§4.0d);
    `ledger` is the §4.3 enforcement point — both are the *same* instances
    the rest of a live run uses, per `hooks/ledger.py`'s "run-scoped, not
    phase-scoped" requirement."""

    @tool(
        "record_product",
        "Record one fully-researched product with its sourced specs. Every "
        "spec you include must carry a real, citable http(s) source_url "
        "you actually fetched — omit any field you cannot source rather "
        "than inventing a URL, and never cite a URL you only saw in "
        "search results. Rejected calls tell you exactly which field(s) "
        "to drop or re-fetch.",
        RECORD_PRODUCT_SCHEMA,
    )
    async def record_product(args: dict[str, Any]) -> dict[str, Any]:
        product, error_text = build_product_from_args(args, survey, ledger)
        if product is None:
            return {"content": [{"type": "text", "text": error_text}], "is_error": True}

        sink.add(product)
        return {
            "content": [{"type": "text", "text": f"Recorded {product.name}."}],
            "structuredContent": {
                "recorded": True,
                "name": product.name,
                "confidence": product.evidence.confidence,
            },
        }

    return record_product
