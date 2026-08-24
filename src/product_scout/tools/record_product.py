"""`record_product` — the Extraction-phase MCP tool (spec docs/handoff.md §3,
build order step 5).

This is the primary enforcement point for CLAUDE.md invariant 2 ("No source,
no field... An extractor that can't cite one omits the field rather than
inventing it. Enforced in validation, not just in prompts") and invariant 3
("confidence is computed in Python from evidence counts and is never
model-assigned"). Both are enforced structurally here, not just documented:

- Invariant 2: every `specs[*]` entry is built as a real `SourcedValue`
  (`models.py`'s own `_require_real_url` validator rejects "", "N/A", and
  non-http(s) URLs); any that fail reject the *whole* call — nothing partial
  ever reaches the sink — with a message naming every bad field so the model
  can drop them all and re-call once.
- Invariant 3: `RECORD_PRODUCT_SCHEMA` has no `confidence`,
  `corroboration_ratio`, or `conflict_ratio` property at all, and sets
  `"additionalProperties": false` at the top level — the model cannot supply
  a confidence value even if it tried; jsonschema rejects the call before
  the handler runs. `confidence` is always computed via `models.py`'s
  existing `compute_confidence()`.

### SPEC GAP-FILL — `confidence_note` is deterministic, not model-supplied

§4 describes `confidence_note` as "one line: what drove this level" but
doesn't say who writes it. Generating it deterministically from the same
evidence numbers that feed `compute_confidence()` (rather than asking the
model for a free-text note) keeps the same invariant-3 spirit — a
model-authored explanation of a model-uninfluenced number is a smaller but
analogous laundering risk. If a future step wants model-authored nuance
(e.g. explaining *why* corroboration was low), that belongs in a
separately-labeled field, not a weakened `confidence_note`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import tool
from pydantic import ValidationError

from product_scout.models import EvidenceProfile, Product, SourcedValue, compute_confidence


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
    "properties": {
        "value": {"type": "string"},
        "source_url": {"type": "string"},
        "source_tier": {"type": "integer", "minimum": 1, "maximum": 5},
        "corroborated_by": {"type": "array", "items": {"type": "string"}},
        "conflicting_values": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["value", "source_url", "source_tier"],
}

RECORD_PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,  # invariant 3: no confidence/*_ratio smuggled in
    "properties": {
        "name": {"type": "string"},
        "brand": {"type": "string"},
        "generation": {"type": "string", "enum": ["current", "prior"]},
        "price_usd": {"type": "number", "exclusiveMinimum": 0},
        "price_source_url": {"type": "string"},
        "price_observed_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 timestamp for when this price was observed.",
        },
        "specs": {
            "type": "object",
            "description": (
                "Spec name -> sourced value. Omit a spec entirely if you "
                "cannot cite a real http(s) source_url for it — never "
                "invent one (invariant 2: no source, no field)."
            ),
            "additionalProperties": _SOURCED_VALUE_SCHEMA,
        },
        "pros": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "cons": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": (
                "At least one real drawback — this app is not a sales tool."
            ),
        },
        "strength_archetype": {
            "type": "string",
            "enum": [
                "value",
                "performance",
                "aesthetic",
                "durability",
                "ergonomics",
                "features",
                "support",
            ],
        },
        "in_budget": {"type": "boolean"},
        "review_sources": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "independent_review_count": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Distinct Tier 2/3 sources you actually used for judgment, "
                "not just cited for specs."
            ),
        },
        "has_methodology_backed_source": {
            "type": "boolean",
            "description": (
                "True iff >=1 source disclosed a testing methodology "
                "(number of units tested, or published measurements)."
            ),
        },
    },
    "required": [
        "name",
        "brand",
        "generation",
        "price_usd",
        "price_source_url",
        "price_observed_at",
        "specs",
        "pros",
        "cons",
        "strength_archetype",
        "in_budget",
        "review_sources",
        "independent_review_count",
        "has_methodology_backed_source",
    ],
}


def _build_confidence_note(evidence: EvidenceProfile) -> str:
    """Deterministic one-liner — see module SPEC GAP-FILL note above."""
    methodology = (
        "with methodology-backed testing"
        if evidence.has_methodology_backed_source
        else "without methodology-backed testing"
    )
    return (
        f"{evidence.independent_review_count} independent review(s), "
        f"{methodology}, {evidence.corroboration_ratio:.0%} of specs "
        f"corroborated, {evidence.conflict_ratio:.0%} conflicting."
    )


def _derive_evidence_profile(
    specs: dict[str, SourcedValue],
    independent_review_count: int,
    has_methodology_backed_source: bool,
) -> EvidenceProfile:
    """Every field here is either passed through from a model-supplied fact
    that isn't mechanically derivable (`independent_review_count`,
    `has_methodology_backed_source`), or computed straight from `specs` —
    never from anything the model asserts about its own confidence."""
    has_tier1_specs = any(sv.source_tier == 1 for sv in specs.values())
    n_specs = len(specs)
    if n_specs == 0:
        corroboration_ratio = 0.0
        conflict_ratio = 0.0
    else:
        corroboration_ratio = sum(
            1 for sv in specs.values() if sv.corroborated_by
        ) / n_specs
        conflict_ratio = sum(
            1 for sv in specs.values() if sv.conflicting_values
        ) / n_specs

    evidence = EvidenceProfile(
        independent_review_count=independent_review_count,
        has_tier1_specs=has_tier1_specs,
        has_methodology_backed_source=has_methodology_backed_source,
        corroboration_ratio=corroboration_ratio,
        conflict_ratio=conflict_ratio,
        confidence=0.0,  # provisional; computed below (models.py's own pattern)
        confidence_note="",  # filled in below
    )
    evidence.confidence = compute_confidence(evidence)
    evidence.confidence_note = _build_confidence_note(evidence)
    return evidence


def make_record_product(sink: ProductSink):
    """Factory mirroring the spec's `make_ask_user(port)` pattern (§3)."""

    @tool(
        "record_product",
        "Record one fully-researched product with its sourced specs. Every "
        "spec you include must carry a real, citable http(s) source_url — "
        "omit any field you cannot source rather than inventing a URL. "
        "Rejected calls tell you exactly which field(s) to drop.",
        RECORD_PRODUCT_SCHEMA,
    )
    async def record_product(args: dict[str, Any]) -> dict[str, Any]:
        # 1. Build each spec individually so a bad source_url can be
        #    reported by name, not just "validation failed somewhere" — and
        #    so the whole call rejects rather than silently dropping the
        #    offending spec and recording a partial product.
        bad_fields: list[str] = []
        specs: dict[str, SourcedValue] = {}
        for spec_name, raw in args["specs"].items():
            try:
                specs[spec_name] = SourcedValue(**raw)
            except ValidationError:
                bad_fields.append(f"specs[{spec_name!r}].source_url")

        if bad_fields:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "record_product rejected — the following "
                            "field(s) lack a real http(s) source_url: "
                            + ", ".join(bad_fields)
                            + ". Omit each listed field entirely (remove it "
                            "from `specs`) and call record_product again — "
                            "do not invent a URL (invariant 2: no source, "
                            "no field)."
                        ),
                    }
                ],
                "is_error": True,
            }

        # 2. Derive EvidenceProfile deterministically — never accept a
        #    model-supplied confidence (invariant 3; also structurally
        #    unavailable per RECORD_PRODUCT_SCHEMA's additionalProperties).
        evidence = _derive_evidence_profile(
            specs,
            independent_review_count=args["independent_review_count"],
            has_methodology_backed_source=args["has_methodology_backed_source"],
        )

        # 3. Construct and validate the full Product. This is also where a
        #    bad price_source_url or empty cons surfaces — both already
        #    enforced by Product's own validators (models.py); no need to
        #    duplicate that logic here.
        try:
            product = Product(
                name=args["name"],
                brand=args["brand"],
                generation=args["generation"],
                price_usd=args["price_usd"],
                price_source_url=args["price_source_url"],
                price_observed_at=args["price_observed_at"],
                specs=specs,
                pros=args["pros"],
                cons=args["cons"],
                strength_archetype=args["strength_archetype"],
                in_budget=args["in_budget"],
                review_sources=args["review_sources"],
                evidence=evidence,
            )
        except ValidationError as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"record_product rejected: {e}. If this is "
                            "about price_source_url, a product needs a "
                            "real http(s) price source — re-fetch it or "
                            "drop this candidate. If this is about cons, "
                            "add at least one real drawback (invariant 4: "
                            "every product needs at least one con)."
                        ),
                    }
                ],
                "is_error": True,
            }

        sink.add(product)
        return {
            "content": [
                {"type": "text", "text": f"Recorded {product.name}."}
            ],
            "structuredContent": {
                "recorded": True,
                "name": product.name,
                "confidence": evidence.confidence,
            },
        }

    return record_product
