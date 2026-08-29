"""Unit tests for tools/record_product.py — the `record_product` MCP tool
(build order step 8, rewritten against the current v7 schema). Covers the
step's explicit deliverable — §4.3 ledger validation, the three named
cases — plus the pre-existing invariants this tool has always enforced:
no source/no field, every product needs a con, and confidence is never
model-suppliable.
"""

import asyncio

import jsonschema
import pytest

from product_scout.confidence import LOW_EVIDENCE_CONFIDENCE_CLAMP, compute_confidence
from product_scout.hooks.ledger import FetchLedger
from product_scout.tools.record_product import (
    RECORD_PRODUCT_SCHEMA,
    ListProductSink,
    make_record_product,
)
from tests.conftest import make_location, make_survey_report

SPEC_URL = "https://example.com/spec-sheet"
PRICE_URL = "https://example.com/product"
REVIEW_URL = "https://example.com/review"
OWNERSHIP_URL = "https://forum.example.com/thread/1"


def run(coro):
    return asyncio.run(coro)


def make_ledger(*, fetched: list[str] = (), seen: list[str] = ()) -> FetchLedger:
    ledger = FetchLedger()
    for url in fetched:
        ledger.record_fetch(url)
    for url in seen:
        ledger.record_seen(url)
    return ledger


def full_valid_args(**overrides) -> dict:
    """A complete, valid record_product args dict against the current
    schema. Tool-arg-dict shape — distinct from conftest.py's pydantic-
    model factories, so it stays local to this file."""
    defaults = dict(
        name="Widget Pro",
        brand="Acme",
        generation="current",
        cluster_key="mid-tier",
        cluster_rationale="Dual-motor mid-tier desks under $300.",
        strength_archetype="value",
        pricing={
            "model_type": "one_time",
            "upfront_amount": 199.0,
            "recurring_amount": None,
            "recurring_period": None,
            "recurring_required_for_core": False,
            "total_cost_1yr": 199.0,
            "price_currency": "USD",
            "price_tax_inclusive": None,
            "price_source_url": PRICE_URL,
            "price_observed_at": "2026-08-01T00:00:00+00:00",
        },
        availability={"sold_in_region": True},
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": SPEC_URL,
                "source_type": "manufacturer",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        },
        pros=["sturdy"],
        cons=["expensive"],
        ownership_notes=[],
        review_sources=[REVIEW_URL],
        in_budget=True,
    )
    defaults.update(overrides)
    return defaults


def call_record_product(
    sink, ledger, args, *, survey=None, location=None, low_evidence_mode=False, progress=None
):
    tool_def = make_record_product(
        sink, survey or make_survey_report(), ledger, location or make_location(),
        low_evidence_mode, progress,
    )
    return run(tool_def.handler(args))


class Recorder:
    """Stand-in for `QuestionPort.report_progress` — see
    tests/test_progress_hook.py, same shape."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


def default_ledger() -> FetchLedger:
    """A ledger admitting every URL `full_valid_args()` cites, at the mode
    each field actually requires."""
    return make_ledger(fetched=[SPEC_URL, PRICE_URL], seen=[REVIEW_URL])


# -- §4.3 case 1: a spec citing an unfetched URL is rejected -----------------


def test_spec_citing_unfetched_url_is_rejected():
    sink = ListProductSink()
    ledger = make_ledger(fetched=[PRICE_URL], seen=[REVIEW_URL])  # SPEC_URL absent
    result = call_record_product(sink, ledger, full_valid_args())
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert "§4.3" in result["content"][0]["text"]
    assert sink.products == []


def test_price_citing_unfetched_url_is_rejected():
    sink = ListProductSink()
    ledger = make_ledger(fetched=[SPEC_URL], seen=[REVIEW_URL])  # PRICE_URL absent
    result = call_record_product(sink, ledger, full_valid_args())
    assert result["is_error"] is True
    assert "pricing.price_source_url" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_citing_a_url_only_seen_via_search_is_rejected():
    """The URL is real and was actually observed by the run — just never
    fetched. Still inadmissible for a spec value (§4.3's second sentence:
    "A search snippet is not a page")."""
    sink = ListProductSink()
    ledger = make_ledger(fetched=[PRICE_URL], seen=[SPEC_URL, REVIEW_URL])
    result = call_record_product(sink, ledger, full_valid_args())
    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "specs['weight']" in text
    assert "only seen in search results" in text
    assert sink.products == []


# -- §4.3 case 2: a redirect does not cause false rejection ------------------


def test_spec_citing_the_post_redirect_url_is_accepted():
    sink = ListProductSink()
    landed_url = "https://cdn.example.com/spec-sheet-final"
    ledger = FetchLedger()
    ledger.record_fetch(SPEC_URL, redirected_to=landed_url)
    ledger.record_fetch(PRICE_URL)
    ledger.record_seen(REVIEW_URL)

    args = full_valid_args()
    args["specs"]["weight"]["source_url"] = landed_url  # cite the post-redirect URL

    result = call_record_product(sink, ledger, args)
    assert result.get("is_error") is not True
    assert len(sink.products) == 1


def test_spec_citing_the_pre_redirect_url_is_also_accepted():
    """Both directions must work — citing whichever URL you actually
    navigated to (pre- or post-redirect) is correct provenance."""
    sink = ListProductSink()
    ledger = FetchLedger()
    ledger.record_fetch(SPEC_URL, redirected_to="https://cdn.example.com/spec-sheet-final")
    ledger.record_fetch(PRICE_URL)
    ledger.record_seen(REVIEW_URL)

    result = call_record_product(sink, ledger, full_valid_args())  # cites SPEC_URL, pre-redirect
    assert result.get("is_error") is not True
    assert len(sink.products) == 1


def test_redirect_with_query_string_and_trailing_slash_variance_still_admits():
    """Combines the redirect allowance with §4.3's normalization rule —
    citing a query-string/trailing-slash variant of the post-redirect URL
    must not be falsely rejected either."""
    sink = ListProductSink()
    ledger = FetchLedger()
    ledger.record_fetch(SPEC_URL, redirected_to="https://cdn.example.com/spec-sheet-final")
    ledger.record_fetch(PRICE_URL)
    ledger.record_seen(REVIEW_URL)

    args = full_valid_args()
    args["specs"]["weight"]["source_url"] = (
        "https://cdn.example.com/spec-sheet-final/?ref=email"
    )
    result = call_record_product(sink, ledger, args)
    assert result.get("is_error") is not True
    assert len(sink.products) == 1


# -- §4.3 case 3: seen_not_fetched admissible for judgment, not specs --------


def test_ownership_note_citing_a_search_only_url_is_accepted():
    sink = ListProductSink()
    ledger = make_ledger(fetched=[SPEC_URL, PRICE_URL], seen=[OWNERSHIP_URL, REVIEW_URL])
    args = full_valid_args(
        ownership_notes=[
            {
                "value": "several owners report the arm loosens after a year",
                "source_url": OWNERSHIP_URL,
                "source_type": "community",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        ]
    )
    result = call_record_product(sink, ledger, args)
    assert result.get("is_error") is not True
    assert len(sink.products) == 1
    assert sink.products[0].ownership_notes[0].source_url == OWNERSHIP_URL


def test_ownership_note_citing_a_never_seen_url_is_still_rejected():
    """seen_not_fetched being admissible for judgment doesn't mean
    ANYTHING is admissible — a URL absent from the ledger entirely (never
    fetched, never searched) still fails."""
    sink = ListProductSink()
    ledger = make_ledger(fetched=[SPEC_URL, PRICE_URL], seen=[REVIEW_URL])
    args = full_valid_args(
        ownership_notes=[
            {
                "value": "owners report longevity issues",
                "source_url": "https://forum.example.com/thread/invented",
                "source_type": "community",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        ]
    )
    result = call_record_product(sink, ledger, args)
    assert result["is_error"] is True
    assert "ownership_notes[0]" in result["content"][0]["text"]
    assert sink.products == []


def test_review_source_citing_a_search_only_url_is_accepted():
    """review_sources are bare URLs (no SourcedValue wrapper) but are
    judgment-bearing the same way ownership_notes are — either access mode
    admissible."""
    sink = ListProductSink()
    ledger = make_ledger(fetched=[SPEC_URL, PRICE_URL], seen=[REVIEW_URL])
    result = call_record_product(sink, ledger, full_valid_args())
    assert result.get("is_error") is not True
    assert len(sink.products) == 1


# -- invariant 3: no source, no field (pre-existing, re-verified against the
#    new schema) -------------------------------------------------------------


def test_spec_missing_source_url_entirely_rejected_at_schema_layer():
    args = full_valid_args()
    del args["specs"]["weight"]["source_url"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


def test_spec_empty_string_source_url_rejected():
    sink = ListProductSink()
    args = full_valid_args()
    args["specs"]["weight"]["source_url"] = ""
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_na_placeholder_source_url_rejected():
    """The literal "pressure" case — a model rationalizing a placeholder
    instead of a real citation."""
    sink = ListProductSink()
    args = full_valid_args()
    args["specs"]["weight"]["source_url"] = "N/A"
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_spec_non_http_scheme_source_url_rejected():
    sink = ListProductSink()
    args = full_valid_args()
    args["specs"]["weight"]["source_url"] = "ftp://example.com/spec-sheet"
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert sink.products == []


def test_multiple_bad_specs_all_named_in_one_rejection():
    sink = ListProductSink()
    args = full_valid_args(
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": "",
                "source_type": "manufacturer",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            },
            "height": {
                "value": "30 in",
                "source_url": "N/A",
                "source_type": "manufacturer",
                "has_stated_methodology": False,
                "observed_at": "2026-08-01T00:00:00+00:00",
            },
        }
    )
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "specs['weight']" in text
    assert "specs['height']" in text
    assert sink.products == []


# -- invariant 6: every product needs at least one con ------------------------


def test_empty_cons_rejected():
    sink = ListProductSink()
    result = call_record_product(sink, default_ledger(), full_valid_args(cons=[]))
    assert result["is_error"] is True
    assert sink.products == []


def test_empty_cons_rejected_at_schema_layer_too():
    """Enforced at both layers — the wire-level jsonschema check
    (minItems: 1) also rejects, not just the handler's pydantic
    construction."""
    args = full_valid_args(cons=[])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


# -- invariant 4: confidence/evidence-derived fields are never model-suppliable --


def test_confidence_field_rejected_at_schema_layer():
    """additionalProperties: false makes this structural, not just a
    handler convention — the model cannot supply confidence even if it
    tried."""
    args = full_valid_args()
    args["confidence"] = 0.99
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


def test_independent_review_count_rejected_at_schema_layer():
    """Unlike the pre-v7 version of this tool, the model no longer reports
    this at all — build_evidence_profile derives it from review_sources."""
    args = full_valid_args()
    args["independent_review_count"] = 5
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=args, schema=RECORD_PRODUCT_SCHEMA)


def test_ships_from_confidence_is_never_model_suppliable():
    """§10.6 / invariant 4 names this field specifically alongside
    confidence. It isn't even in the schema's availability object — the
    handler always fills it in as 0.0 (§10.6's real derivation is build
    order step 11)."""
    assert "ships_from_confidence" not in RECORD_PRODUCT_SCHEMA["properties"]["availability"][
        "properties"
    ]


# -- positive control + evidence wiring ---------------------------------------


def test_valid_product_accepted():
    sink = ListProductSink()
    result = call_record_product(sink, default_ledger(), full_valid_args())
    assert result.get("is_error") is not True
    assert len(sink.products) == 1

    product = sink.products[0]
    assert product.name == "Widget Pro"
    assert product.evidence.confidence == compute_confidence(product.evidence)
    assert product.evidence.confidence_note  # non-empty
    assert product.evidence.independent_review_count == 1  # derived from review_sources


def test_confidence_note_is_deterministic():
    """Same args, called twice -> identical note text. No model involved
    in this test at all — pins the determinism the handler guarantees."""
    args = full_valid_args()
    sink_a, sink_b = ListProductSink(), ListProductSink()
    call_record_product(sink_a, default_ledger(), args)
    call_record_product(sink_b, default_ledger(), args)
    assert sink_a.products[0].evidence.confidence_note == (
        sink_b.products[0].evidence.confidence_note
    )


def test_availability_defaults_are_never_model_supplied():
    """No ships_from info at all in the args -> stays undetermined. Unlike
    the pre-step-11 version of this test, `ships_from_confidence` is now a
    REAL derivation (`location.ships_from_confidence_for`), not a
    hard-coded placeholder — it just happens to agree with 0.0 here because
    `ships_from_signal` is None, exactly as `ships_from_confidence_for(None)`
    is unit-tested to do in test_location.py."""
    sink = ListProductSink()
    call_record_product(sink, default_ledger(), full_valid_args())
    availability = sink.products[0].availability
    assert availability.ships_from_confidence == 0.0
    assert availability.ships_from_signal is None


# -- §10.6/§10.3 ships_from ladder + landed pricing (build order step 11) ---

SHIPPING_POLICY_URL = "https://example.com/shipping-policy"


def _args_with_ships_from(**availability_overrides) -> dict:
    args = full_valid_args()
    args["availability"] = {"sold_in_region": True, **availability_overrides}
    return args


def test_ships_from_confidence_is_derived_from_reported_signal():
    sink = ListProductSink()
    ledger = make_ledger(
        fetched=[SPEC_URL, PRICE_URL, SHIPPING_POLICY_URL], seen=[REVIEW_URL]
    )
    args = _args_with_ships_from(
        ships_from="DE",
        ships_from_signal="shipping_policy",
        ships_from_source_url=SHIPPING_POLICY_URL,
    )
    call_record_product(sink, ledger, args)
    availability = sink.products[0].availability
    assert availability.ships_from == "DE"
    assert availability.ships_from_signal == "shipping_policy"
    assert availability.ships_from_confidence == 0.98


def test_ships_from_source_url_required_alongside_signal():
    sink = ListProductSink()
    args = _args_with_ships_from(ships_from="DE", ships_from_signal="shipping_policy")
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    assert "ships_from_source_url" in result["content"][0]["text"]
    assert sink.products == []


def test_ships_from_source_url_is_ledger_validated():
    sink = ListProductSink()
    args = _args_with_ships_from(
        ships_from="DE",
        ships_from_signal="shipping_policy",
        ships_from_source_url=SHIPPING_POLICY_URL,  # never fetched or seen
    )
    result = call_record_product(sink, default_ledger(), args)
    assert result["is_error"] is True
    assert "ships_from_source_url" in result["content"][0]["text"]


def test_cctld_claim_on_excluded_tld_is_downgraded_to_fallback():
    """§10.6's exclusion list, exercised through the tool boundary."""
    sink = ListProductSink()
    io_url = "https://example.io/shipping"
    ledger = make_ledger(fetched=[SPEC_URL, PRICE_URL, io_url], seen=[REVIEW_URL])
    args = _args_with_ships_from(
        ships_from="DE", ships_from_signal="cctld", ships_from_source_url=io_url
    )
    call_record_product(sink, ledger, args)
    availability = sink.products[0].availability
    assert availability.ships_from_signal == "fallback"
    assert availability.ships_from_confidence == 0.25


def test_confirmed_cross_border_computes_landed_price():
    sink = ListProductSink()
    ledger = make_ledger(
        fetched=[SPEC_URL, PRICE_URL, SHIPPING_POLICY_URL], seen=[REVIEW_URL]
    )
    args = _args_with_ships_from(
        ships_from="DE",
        ships_from_signal="shipping_policy",
        ships_from_source_url=SHIPPING_POLICY_URL,
        shipping_estimate_native=15.0,
        duty_estimate_native=5.0,
    )
    call_record_product(sink, ledger, args, location=make_location(country="US", currency="USD"))
    availability = sink.products[0].availability
    assert availability.landed_price_native == 219.0  # 199.0 upfront + 15 + 5


def test_same_region_product_never_gets_a_landed_price_even_if_model_sent_one():
    """Defensive: §10.3's gate is enforced in code, not trusted from the
    model — a same-region product's shipping/duty figures are discarded."""
    sink = ListProductSink()
    ledger = make_ledger(
        fetched=[SPEC_URL, PRICE_URL, SHIPPING_POLICY_URL], seen=[REVIEW_URL]
    )
    args = _args_with_ships_from(
        ships_from="US",
        ships_from_signal="shipping_policy",
        ships_from_source_url=SHIPPING_POLICY_URL,
        shipping_estimate_native=15.0,
        duty_estimate_native=5.0,
    )
    call_record_product(sink, ledger, args, location=make_location(country="US", currency="USD"))
    availability = sink.products[0].availability
    assert availability.shipping_estimate_native is None
    assert availability.duty_estimate_native is None
    assert availability.landed_price_native is None


# -- §14/§8.3 community-source-for-specs gate + confidence clamp (build order step 12) --


def _args_with_community_spec(**overrides) -> dict:
    args = full_valid_args(**overrides)
    args["specs"] = {
        "weight": {
            "value": "42 lb",
            "source_url": "https://forum.example.com/thread/42",
            "source_type": "community",
            "has_stated_methodology": False,
            "observed_at": "2026-08-01T00:00:00+00:00",
        }
    }
    return args


def test_community_spec_rejected_in_standard_mode():
    sink = ListProductSink()
    ledger = make_ledger(
        fetched=[PRICE_URL, "https://forum.example.com/thread/42"], seen=[REVIEW_URL]
    )
    result = call_record_product(
        sink, ledger, _args_with_community_spec(), low_evidence_mode=False
    )
    assert result["is_error"] is True
    assert "specs['weight']" in result["content"][0]["text"]
    assert "community" in result["content"][0]["text"].lower()
    assert sink.products == []


def test_community_spec_admitted_in_low_evidence_mode():
    sink = ListProductSink()
    ledger = make_ledger(
        fetched=[PRICE_URL, "https://forum.example.com/thread/42"], seen=[REVIEW_URL]
    )
    result = call_record_product(
        sink, ledger, _args_with_community_spec(), low_evidence_mode=True
    )
    assert result.get("is_error") is not True
    assert len(sink.products) == 1
    assert sink.products[0].specs["weight"].source_type == "community"


def test_non_community_spec_unaffected_by_mode():
    """The gate is source_type-specific — a manufacturer spec is admissible
    in either mode."""
    sink_standard, sink_low = ListProductSink(), ListProductSink()
    result_standard = call_record_product(
        sink_standard, default_ledger(), full_valid_args(), low_evidence_mode=False
    )
    result_low = call_record_product(
        sink_low, default_ledger(), full_valid_args(), low_evidence_mode=True
    )
    assert result_standard.get("is_error") is not True
    assert result_low.get("is_error") is not True


def test_low_evidence_mode_threaded_to_confidence_clamp():
    """End-to-end: record_product's low_evidence_mode reaches
    build_evidence_profile's §8.3 clamp, not just the spec-gate check."""
    # A saturated-evidence product (6 review sources, tier-1 + methodology,
    # corroborated) computes well above LOW_EVIDENCE_CONFIDENCE_CLAMP.
    args = full_valid_args(
        specs={
            "weight": {
                "value": "42 lb",
                "source_url": SPEC_URL,
                "source_type": "manufacturer",
                "has_stated_methodology": True,
                "corroborated_by": ["https://other.example.com/review"],
                "observed_at": "2026-08-01T00:00:00+00:00",
            }
        },
        review_sources=[f"https://example.com/review{i}" for i in range(6)],
    )
    ledger = make_ledger(
        fetched=[SPEC_URL, PRICE_URL],
        seen=[f"https://example.com/review{i}" for i in range(6)],
    )

    sink_standard = ListProductSink()
    call_record_product(sink_standard, ledger, args, low_evidence_mode=False)
    unclamped = sink_standard.products[0].evidence.confidence

    sink_low = ListProductSink()
    call_record_product(sink_low, ledger, args, low_evidence_mode=True)
    clamped = sink_low.products[0].evidence.confidence

    assert unclamped > LOW_EVIDENCE_CONFIDENCE_CLAMP
    assert clamped == LOW_EVIDENCE_CONFIDENCE_CLAMP


# -- §16.2 progress ticks: record_product isn't a WebFetch/WebSearch call,
# so hooks/progress.py's PostToolUse matcher never sees it — ticked inline
# here instead, straight from the one place that knows the outcome. -------


def test_progress_ticks_on_successful_record():
    sink = ListProductSink()
    progress = Recorder()
    call_record_product(sink, default_ledger(), full_valid_args(), progress=progress)
    assert progress.messages == ["  recorded Widget Pro"]


def test_progress_ticks_verbatim_error_text_on_rejection():
    sink = ListProductSink()
    ledger = make_ledger(fetched=[PRICE_URL], seen=[REVIEW_URL])  # SPEC_URL absent
    progress = Recorder()
    result = call_record_product(sink, ledger, full_valid_args(), progress=progress)
    assert len(progress.messages) == 1
    # Same text the tool itself returned — not re-wrapped or duplicated.
    assert progress.messages[0] == f"  {result['content'][0]['text']}"


def test_no_progress_fn_is_a_silent_no_op():
    """`progress=None` (the default) never raises — every existing caller
    that doesn't pass it keeps working unchanged."""
    sink = ListProductSink()
    result = call_record_product(sink, default_ledger(), full_valid_args())
    assert result.get("is_error") is not True
