"""Pydantic data model for Product Scout (spec docs/handoff.md §4, v7).

Rewritten against spec **v7** — the v3 model this replaced had no denominator
discipline on the evidence ratios (§4.0a/§4.0b), let models populate
`EvidenceProfile` directly (§4.0d now forbids this), and conflated `Scored`
with a second copy of `confidence` (removed — it lives once, on
`Product.evidence.confidence`).

Declaration order follows §4.0c's own code block, since that block already
resolves the dependency ordering (`SourcedValue` before `Product`,
`EvidenceProfile` before `Product`, etc.). `Caveat` (§5.5) is declared just
before `RunRecord`, which is the first thing that references it.

house style, carried from v3: validate hard, fail loudly (§4's own
directive); hard constraints stated in prose are real validators, not prompt
wording — CLAUDE.md invariant 3 ("no source, no field... enforced in
validation") and invariant 6 (empty `cons` is a validation failure).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _require_real_url(v: str, *, field_name: str) -> str:
    """Shared enforcement of invariant 3 ('No source, no field').

    A source URL must be a non-empty, real http(s) URL. This is a *shape*
    check only — a model can still emit a URL it never fetched. §4.3's
    stronger guarantee (rejecting a URL the run's fetch ledger never saw) is
    a run-scoped concern enforced at the record_product/tool boundary
    (build order step 8), not something a standalone pydantic model can
    check for itself.
    """
    v = v.strip()
    if not v or not _URL_RE.match(v):
        raise ValueError(
            f"{field_name} must be a real http(s) URL — no source, no field "
            f"(invariant 3). Omit the field instead of passing a placeholder; "
            f"got {v!r}."
        )
    return v


def normalize_url(url: str) -> str:
    """§4.3's normalization rule: scheme + host + path, query string discarded.

    Shared by `confidence.py` (deduplicating sources for `EvidenceProfile`)
    and, later, the run-scoped fetch ledger (build order step 8) — both need
    the identical rule, or a redirect/tracking-parameter mismatch between
    them would silently reject good data. Exact string comparison is
    explicitly called out in §4.3 as the wrong approach.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


# ---------------------------------------------------------------------------
# GateAnswer: §3.4 shows this defined in io/port.py, but TopicAnswer.gate_answer
# (below) needs the type at step 1, before io/port.py exists (step 2). Defined
# here instead — models.py is the more foundational module, so io/port.py
# imports it from here once built, rather than models.py reaching forward
# into a module that doesn't exist yet.
# ---------------------------------------------------------------------------
GateAnswer = Literal["must_have", "must_avoid", "persuadable", "no_preference"]


class SourcedValue(BaseModel):
    value: str
    native_unit: str | None = None
    source_url: str  # REQUIRED, and ledger-validated (§4.3) at the tool boundary
    source_type: Literal[
        "manufacturer", "testing_outlet", "aggregator", "retailer", "community"
    ]
    has_stated_methodology: bool  # orthogonal to source_type; §14
    corroborated_by: list[str] = []
    conflicting_values: list[str] = []
    observed_at: datetime
    from_trusted_source: bool = False  # §15 seam — unwired in v1

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, v: str) -> str:
        return _require_real_url(v, field_name="SourcedValue.source_url")


class Location(BaseModel):
    country: str  # ISO 3166-1 alpha-2
    currency: str  # ISO 4217


class Availability(BaseModel):
    sold_in_region: bool
    regional_names: list[SourcedValue] = []
    ships_to_region: bool | None
    ships_from: str | None
    ships_from_signal: (
        Literal["shipping_policy", "cctld", "currency", "language", "fallback"] | None
    )
    ships_from_confidence: float  # DERIVED from ships_from_signal; §10.6 — a
    # pure function of the signal, never model-asserted (invariant 4's
    # ships_from_confidence clause). The derivation itself lands at build
    # order step 11 (§10); this schema seam is what step 1 owns.
    import_caveats: list[str] = []
    shipping_estimate_native: float | None = None  # only when CONFIRMED cross-border
    duty_estimate_native: float | None = None
    landed_price_native: float | None = None


class PricingModel(BaseModel):
    model_type: Literal[
        "one_time",
        "subscription_only",
        "one_time_plus_subscription",
        "freemium",
        "usage_based",
        "financed_major_purchase",
    ]
    upfront_amount: float | None = None
    recurring_amount: float | None = None
    recurring_period: Literal["monthly", "annual"] | None = None
    recurring_required_for_core: bool = False
    total_cost_1yr: float | None = None  # None for usage_based & financed; §11.3
    price_currency: str
    price_tax_inclusive: bool | None = None  # None = undetermined; §10.3a
    price_source_url: str
    price_observed_at: datetime
    price_overridden: bool = False  # set by rescore; §16

    @field_validator("price_source_url")
    @classmethod
    def _validate_price_source_url(cls, v: str) -> str:
        return _require_real_url(v, field_name="PricingModel.price_source_url")


class EvidenceProfile(BaseModel):
    """How much we actually know about this product.

    §4.0d: EVERY field here is DERIVED in Python — no field is ever emitted
    by an extraction or analysis model. `confidence.py`'s
    `build_evidence_profile()` is the only constructor; nothing else should
    build one. `validate_assignment=True` exists for exactly one legitimate
    reason: `confidence` (and `confidence_note`) can't be known until the
    rest of the profile exists, so callers construct with a placeholder and
    reassign — see `confidence.py`.
    """

    model_config = ConfigDict(validate_assignment=True)

    source_count: int = Field(ge=0)  # distinct source URLs across specs,
    # ownership_notes, and review_sources. Not reviews.
    independent_review_count: int = Field(ge=0)  # sources of type
    # testing_outlet or aggregator
    extracted_spec_count: int = Field(ge=0)  # |comparison_specs ∩ specs|
    has_tier1_specs: bool
    has_methodology_backed_source: bool
    corroboration_ratio: float = Field(ge=0.0, le=1.0)  # §4.0a
    conflict_ratio: float = Field(ge=0.0, le=1.0)  # §4.0a
    recency_factor: float = Field(ge=0.0, le=1.0)  # observed_at vs the
    # category_kind window; §4.1
    confidence: float = Field(ge=0.0, le=1.0)  # compute_confidence(); §4.1
    confidence_note: str  # rendered from the above

    @model_validator(mode="after")
    def _coherent(self) -> "EvidenceProfile":
        """§4.0c's coherence validator, transcribed exactly.

        A lone SOURCE cannot corroborate or conflict with anything. Keyed on
        `source_count`, NOT `independent_review_count`: a manufacturer spec
        page and one independent review reporting the same figure IS
        corroboration, at independent_review_count == 1. Keying on reviews
        would reject ordinary extractor output.

        Deliberately NO rule constraining the SUM of the two ratios — see
        §4.0a: a single spec can legitimately be both corroborated (two
        sources agree) and conflicted (a third dissents).
        """
        if self.source_count <= 1 and (
            self.corroboration_ratio != 0.0 or self.conflict_ratio != 0.0
        ):
            raise ValueError(
                "corroboration_ratio and conflict_ratio must be 0.0 when "
                "source_count <= 1 — there is nothing to agree or disagree with"
            )
        if self.extracted_spec_count == 0 and self.conflict_ratio != 0.0:
            raise ValueError(
                "conflict_ratio must be 0.0 when no comparison specs were "
                "extracted — its denominator is empty"
            )
        return self


class Product(BaseModel):
    name: str
    brand: str
    generation: Literal["current", "prior"]
    role: Literal[
        "recommendation",
        "baseline_current",
        "reference_above_budget",
        "reference_unavailable",
        "reference_displaced",
    ] = "recommendation"
    cluster_key: str  # prior-gen INHERITS its sibling's key
    cluster_rationale: str
    strength_archetype: str  # free string; the 6-8-distinct/default-7 pool
    # cap (§0) is an orchestration-level rule, not a schema enum
    pricing: PricingModel
    availability: Availability
    specs: dict[str, SourcedValue]
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)  # HARD CONSTRAINT — invariant 6
    ownership_notes: list[SourcedValue] = []
    review_sources: list[str] = Field(min_length=1)
    in_budget: bool
    evidence: EvidenceProfile


class Scored(BaseModel):
    product_name: str
    score: float = Field(ge=0.0, le=10.0)  # holistic judgment, never a formula
    rationale: str
    score_at_minus_10pct: float
    score_at_minus_20pct: float
    score_at_minus_30pct: float
    flip_point_amount: float | None  # only within the sampled range; §5.2
    flip_point_note: str | None  # states the bound, or the suppression reason
    # NOTE: confidence is NOT stored here — deliberately, unlike v3. It lives
    # once, on Product.evidence.confidence, and is joined at render. Two
    # stored copies of one computed number is how the scale ends up showing
    # a different value than the caveats cite.
    #
    # NOTE: eligibility (§5.2's flip_point_eligible predicate, in
    # confidence.py) is evaluated in Phase 6a BEFORE a Scored is
    # constructed — it is not a model_validator here, unlike v3's
    # confidence-floor validator. Scored no longer carries confidence to
    # validate against, and eligibility also needs
    # Product.evidence.independent_review_count, which only Product has.


class Verdict(BaseModel):
    action: Literal[
        "BUY",
        "WAIT",
        "CONSIDER_CHEAPER_CATEGORY",
        "KEEP_CURRENT",
        "INSUFFICIENT_EVIDENCE",
    ]
    reasoning: str
    timing_note: str | None


class BroaderCategory(BaseModel):
    name: str
    rationale: str
    estimated_coverage: Literal["rich", "moderate", "sparse", "barren"]


class IntakeAnswers(BaseModel):
    owns_current_version: bool
    current_model: str | None = None  # when upgrading; §7.2
    budget_ceiling: float | None = None  # None = no limit
    budget_note: str = ""  # verbatim; §7.1
    required_features: list[str] = []  # FILTERS, not preferences
    candidates_under_consideration: list[str] = []

    @model_validator(mode="after")
    def _current_model_matches_ownership(self) -> "IntakeAnswers":
        if self.owns_current_version and not self.current_model:
            raise ValueError(
                "IntakeAnswers.current_model is required when "
                "owns_current_version=True (§7 item 1)."
            )
        if not self.owns_current_version and self.current_model:
            raise ValueError(
                "IntakeAnswers.current_model must be None when "
                f"owns_current_version=False (§7 item 1); got {self.current_model!r}."
            )
        return self


class TimingAssessment(BaseModel):
    """Phase 4 (TIMING) output. Predictive by nature — see §6.5: every claim
    carries its basis (`basis_notes`), never appears as bare fact."""

    signal_found: bool
    successor_expected: str | None = None
    price_trend: str | None = None
    technology_transition: str | None = None
    basis_notes: list[str] = []  # every claim carries its basis
    recommends_wait: bool


class Cluster(BaseModel):
    key: str
    label: str
    exemplar_products: list[str]  # UNSOURCED; §8.1a constraints apply —
    # these names can reach the user in question copy before a single URL
    # has been fetched, so they carry no source_url by design.
    price_range_native: tuple[float, float]  # indicative only, pre-extraction
    approximate_member_count: int


class Dimension(BaseModel):
    """A spec axis that separates clusters. Drives §9.7's stopping condition."""

    name: str
    splits: dict[str, str]  # cluster_key -> position on this dimension
    axis_kind: Literal["position", "importance"] | None  # decided in SURVEY; §9.4

    def separating_power(self, surviving: set[str]) -> int:
        """Count of distinct positions across surviving clusters."""
        return len({v for k, v in self.splits.items() if k in surviving})


class SurveyReport(BaseModel):
    category_kind: Literal["physical", "software_service", "hybrid"]
    coverage: Literal["rich", "moderate", "sparse", "barren"]
    differentiation: Literal["high", "moderate", "low"]
    estimated_product_count: int
    independent_review_sources_found: int
    has_methodology_backed_testing: bool
    clusters: list[Cluster]
    dimensions: list[Dimension]
    comparison_specs: list[str]  # §4.0b — the ratio denominator. Must
    # contain every Dimension.name. Target 5-10 for table legibility.
    pricing_complexity: Literal[
        "simple", "subscription_based", "financed_major_purchase", "hybrid"
    ]
    secondhand_risk_factors: list[SourcedValue] = []
    products_unavailable_in_region: int
    suggested_broader_categories: list[BroaderCategory] = []
    notes: str

    @field_validator("comparison_specs")
    @classmethod
    def _dimensions_covered(cls, v: list[str], info) -> list[str]:
        # NOTE: pydantic v2 field_validators only see already-validated
        # sibling fields via `info.data`, and field order in the class body
        # determines what's available — `dimensions` is declared before
        # `comparison_specs` above, so this is safe. If a case constructs
        # SurveyReport with dimensions=... omitted or invalid, info.data
        # simply won't have "dimensions" and this validator no-ops, which is
        # the right behavior: let the dimensions field's own validation
        # report that error instead of masking it here.
        dims = info.data.get("dimensions")
        if dims is not None:
            missing = [d.name for d in dims if d.name not in v]
            if missing:
                raise ValueError(
                    "SurveyReport.comparison_specs must contain every "
                    f"Dimension.name (§4.0b); missing: {missing}"
                )
        return v


class TopicAnswer(BaseModel):
    topic: str
    dimension_name: str | None  # which Dimension this addressed
    gate_answer: GateAnswer
    axis_kind: Literal["position", "importance"] | None
    axis_value: float | None
    axis_skipped: bool = False  # leans require explicit answers; §9.7
    free_text: str = ""
    became_filter: bool
    assumption_logged: str | None


class Caveat(BaseModel):
    """§5.5 — auto-generated, tiered. Requires no model call."""

    tier: Literal["decision_affecting", "provenance"]
    text: str
    anchor: str | None  # product name or table cell it attaches to
    instance_count: int = 1  # for class-collapsed caveats


class RunRecord(BaseModel):  # <- persisted as JSON
    run_id: str
    created_at: datetime
    product_type: str
    original_product_type: str | None
    location: Location  # mismatch invalidates rescore; §16
    units: Literal["imperial", "metric"]
    intake: IntakeAnswers
    survey: SurveyReport
    topics: list[TopicAnswer]
    low_evidence_mode: bool
    commodity_category: bool
    category_broadening_offered: bool
    truncated_at_phase: int | None  # cost-cap termination; §13.1
    products: list[Product]
    timing: TimingAssessment
    verdict: Verdict
    scores: list[Scored]
    caveats: list[Caveat]  # tiered; §5.5
    model_ids: dict[str, str]
    skill_hashes: dict[str, str]  # SHA-256 per SKILL.md; §16
    trusted_sources: list[str] = []  # §15 seam — always empty in v1
