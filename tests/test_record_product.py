"""Unit tests for tools/record_product.py — the `record_product` MCP tool
(build order step 5). The explicit deliverable of this step: verify the
`source_url` requirement actually holds, including under "pressure" (a
placeholder like "N/A" standing in for a real citation).
"""

import asyncio

import jsonschema
import pytest

from product_scout.models import compute_confidence
from product_scout.tools.record_product import (
    RECORD_PRODUCT_SCHEMA,
    ListProductSink,
    make_record_product,
)


def run(coro):
    return asyncio.run(coro)


def full_valid_args(**overrides) -> dict:
    """A complete, valid record_product args dict. Tool-arg-dict shape —
    distinct from conftest.py's pydantic-model factories, so it stays local
    to this file."""
    defaults = dict(
        name="Widget Pro",
        brand="Acme",
        generation="current",
        price_usd=199.0,
        price_source_url="https://example.com/product",
        price_observed_at="2026-08-01T00:00:00+00:00",
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": "https://example.com/spec-sheet",
                "source_tier": 1,
            }
        },
        pros=["sturdy"],
        cons=["expensive"],
        strength_archetype="value",
        in_budget=True,
        review_sources=["https://example.com/review"],
        independent_review_count=2,
        has_methodology_backed_source=True,
    )
    defaults.update(overrides)
    return defaults


def call_record_product(sink, args):
    tool_def = make_record_product(sink)
    return run(tool_def.handler(args))


# -- invariant 2: no source, no field -----------------------------------------


def test_spec_missing_source_url_entirely_rejected():
    sink = ListProductSink()
    args = full_valid_args(
        specs={"weight": {"value": "42 lb", "source_tier": 1}}
    )
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_empty_string_source_url_rejected():
    sink = ListProductSink()
    args = full_valid_args(
        specs={
            "weight": {"value": "42 lb", "source_url": "", "source_tier": 1}
        }
    )
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_na_placeholder_source_url_rejected():
    """The literal "pressure" case — a model rationalizing a placeholder
    instead of a real citation."""
    sink = ListProductSink()
    args = full_valid_args(
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": "N/A",
                "source_tier": 1,
            }
        }
    )
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_non_http_scheme_source_url_rejected():
    sink = ListProductSink()
    args = full_valid_args(
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": "ftp://example.com/spec-sheet",
                "source_tier": 1,
            }
        }
    )
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_multiple_bad_specs_all_named_in_one_rejection():
    sink = ListProductSink()
    args = full_valid_args(
        specs={
            "weight": {"value": "42 lb", "source_url": "", "source_tier": 1},
            "height": {
                "value": "30 in",
                "source_url": "N/A",
                "source_tier": 2,
            },
        }
    )
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "specs['weight']" in text
    assert "specs['height']" in text
    assert sink.products == []


def test_missing_price_source_url_rejected():
    sink = ListProductSink()
    args = full_valid_args(price_source_url="")
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert sink.products == []


# -- invariant 4: every product needs at least one con ------------------------


def test_empty_cons_rejected():
    sink = ListProductSink()
    args = full_valid_args(cons=[])
    result = call_record_product(sink, args)
    assert result["is_error"] is True
    assert sink.products == []


def test_empty_cons_rejected_at_schema_layer_too():
    """Enforced in validation at both layers — the wire-level jsonschema
    check (minItems: 1) also rejects, not just the handler's pydantic
    construction."""
    args = full_valid_args(cons=[])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


# -- invariant 3: confidence is computed, never model-assigned ----------------


def test_confidence_field_rejected_at_schema_layer():
    """additionalProperties: false makes this structural, not just a
    handler convention — the model cannot supply confidence even if it
    tried."""
    args = full_valid_args()
    args["confidence"] = 0.99
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


# -- positive control ----------------------------------------------------------


def test_valid_product_accepted():
    sink = ListProductSink()
    args = full_valid_args()
    result = call_record_product(sink, args)
    assert result.get("is_error") is not True
    assert len(sink.products) == 1

    product = sink.products[0]
    assert product.name == "Widget Pro"
    assert product.evidence.confidence == compute_confidence(product.evidence)
    assert product.evidence.confidence_note  # non-empty


def test_confidence_note_is_deterministic():
    """Same args, called twice -> identical note text. Proves the note
    isn't model-variance-dependent (there's no model in this test at all —
    this pins the shape of determinism the handler itself guarantees)."""
    args = full_valid_args()
    sink_a, sink_b = ListProductSink(), ListProductSink()
    call_record_product(sink_a, args)
    call_record_product(sink_b, args)
    assert sink_a.products[0].evidence.confidence_note == (
        sink_b.products[0].evidence.confidence_note
    )
