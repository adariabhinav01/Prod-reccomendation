"""Pydantic data model for Product Scout (spec docs/handoff.md §4).

Everything downstream hangs off these schemas. Per §4's own directive:
"Use pydantic; validate hard, fail loudly." Hard constraints that the spec
states in prose are enforced here as real validators, not left to prompt
wording — see CLAUDE.md invariant 2 ("No source, no field... Enforced in
validation, not just in prompts") and invariant 4 (empty `cons` is a
validation failure).

Declaration order deviates from §4's pseudocode only where Python requires
it: `EvidenceProfile` must exist before `Product`, since `Product.evidence`
references it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# §5.2 — "Suppress flip points below 0.70 confidence." Shared here so later
# phases (analysis, render) import the same floor rather than re-declaring
# the magic number.
FLIP_POINT_CONFIDENCE_FLOOR: float = 0.70

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _require_real_url(v: str, *, field_name: str) -> str:
    """Shared enforcement of invariant 2 ('No source, no field').

    A source URL must be a non-empty, real http(s) URL. An extractor that
    cannot cite one must omit the field entirely, never pass a placeholder
    like "", "N/A", or "unknown".
    """
    v = v.strip()
    if not v or not _URL_RE.match(v):
        raise ValueError(
            f"{field_name} must be a real http(s) URL — no source, no field "
            f"(invariant 2). Omit the field instead of passing a placeholder; "
            f"got {v!r}."
        )
    return v


class SourcedValue(BaseModel):
    value: str
    source_url: str  # REQUIRED — no source, no field
    source_tier: int = Field(ge=1, le=5)  # 1-5, see §11
    corroborated_by: list[str] = []
    conflicting_values: list[str] = []
    from_trusted_source: bool = False  # §12 seam — unwired in v1

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, v: str) -> str:
        return _require_real_url(v, field_name="SourcedValue.source_url")


class EvidenceProfile(BaseModel):
    """How much we actually know about this product."""

    model_config = ConfigDict(validate_assignment=True)

    independent_review_count: int = Field(ge=0)  # distinct Tier 2/3 sources
    has_tier1_specs: bool
    has_methodology_backed_source: bool  # >=1 source disclosing methodology
    corroboration_ratio: float = Field(ge=0.0, le=1.0)
    conflict_ratio: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)  # COMPUTED, not model-assigned (§4.1)
    confidence_note: str  # one line: what drove this level


class Product(BaseModel):
    name: str
    brand: str
    generation: Literal["current", "prior"]
    price_usd: float
    price_source_url: str
    price_observed_at: datetime
    specs: dict[str, SourcedValue]
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)  # <- HARD CONSTRAINT, §5.1 / invariant 4
    strength_archetype: Literal[
        "value",
        "performance",
        "aesthetic",
        "durability",
        "ergonomics",
        "features",
        "support",
    ]
    in_budget: bool
    review_sources: list[str] = Field(min_length=1)
    evidence: EvidenceProfile  # NEW in v2

    @field_validator("price_source_url")
    @classmethod
    def _validate_price_source_url(cls, v: str) -> str:
        return _require_real_url(v, field_name="Product.price_source_url")


class Scored(BaseModel):
    product_name: str
    score: float = Field(ge=0.0, le=10.0)  # one decimal, model-assigned judgment
    confidence: float = Field(ge=0.0, le=1.0)  # copied from EvidenceProfile
    rationale: str
    flip_point_usd: float | None  # None when confidence < 0.70 (§5.2)
    flip_point_note: str | None
    score_at_minus_10pct: float
    score_at_minus_20pct: float

    @model_validator(mode="after")
    def _flip_point_suppressed_below_confidence_floor(self) -> "Scored":
        """§5.2: 'Suppress flip points below 0.70 confidence.'

        One-directional only: reject a non-null flip_point_usd on a
        sub-threshold-confidence product. Does NOT require a non-null flip
        point above the threshold, because a null flip point is also
        legitimately correct for the #1 overall pick (nothing to flip to) —
        this model has no way to distinguish that from suppression.
        """
        if (
            self.confidence < FLIP_POINT_CONFIDENCE_FLOOR
            and self.flip_point_usd is not None
        ):
            raise ValueError(
                "Scored.flip_point_usd must be None when confidence < "
                f"{FLIP_POINT_CONFIDENCE_FLOOR} (§5.2); got "
                f"confidence={self.confidence} with "
                f"flip_point_usd={self.flip_point_usd}."
            )
        return self


class Verdict(BaseModel):
    action: Literal[
        "BUY",
        "WAIT",
        "CONSIDER_CHEAPER_CATEGORY",
        "KEEP_CURRENT",
        "INSUFFICIENT_EVIDENCE",  # new in v2
    ]
    reasoning: str
    timing_note: str | None


class BroaderCategory(BaseModel):  # NEW in v3
    name: str  # e.g. "film scanners"
    rationale: str  # why this would have better coverage
    estimated_coverage: Literal["rich", "moderate", "sparse", "barren"]


class CoverageReport(BaseModel):  # output of Phase 1
    estimated_product_count: int
    independent_review_sources_found: int
    has_methodology_backed_testing: bool
    coverage: Literal["rich", "moderate", "sparse", "barren"]
    suggested_broader_categories: list[BroaderCategory] = []  # NEW in v3
    notes: str


# ---------------------------------------------------------------------------
# SPEC GAP-FILL — TimingAssessment / TimingSignal
#
# RunRecord.timing: TimingAssessment is referenced in §4 but never defined
# anywhere in docs/handoff.md. Designed here from context, not spec text:
#   §1   — Phase 4 (TIMING, Haiku) assesses "releases, price trends, tech
#          transitions".
#   §6.5 — "Every timing claim carries its basis... and never appears as
#          bare fact. If Phase 4 finds nothing credible, it says 'no timing
#          signal found' rather than manufacturing one."
#
# Shape: a list of labeled, basis-carrying signals (one per distinct claim,
# tagged by which of the three §1 categories it falls under), plus a
# has_signal/summary pair for the "nothing found" case. The model_validator
# makes §6.5's rule structural rather than a prompt-only convention.
# ---------------------------------------------------------------------------


class TimingSignal(BaseModel):
    """A single timing claim — always carries its basis (§6.5)."""

    kind: Literal["release_cycle", "price_trend", "tech_transition"]
    claim: str  # e.g. "Successor expected within 6 weeks"
    basis: str  # e.g. "Manufacturer announced Q4 launch on 2026-07-01"
    source_url: str | None = None  # cite when available; None only for
    # pattern-based reasoning ("historically refreshed each September"),
    # which §6.5 accepts as a valid basis even without a citable URL.


class TimingAssessment(BaseModel):
    """Phase 4 (TIMING) output. Predictive by nature — see §6.5."""

    has_signal: bool
    signals: list[TimingSignal] = []
    summary: str  # "no timing signal found" (verbatim) when has_signal is False

    @model_validator(mode="after")
    def _signals_consistent_with_has_signal(self) -> "TimingAssessment":
        if self.has_signal and not self.signals:
            raise ValueError(
                "TimingAssessment.has_signal=True requires at least one "
                "TimingSignal (§6.5) — a signal claim needs a basis, not "
                "just a flag."
            )
        if not self.has_signal and self.signals:
            raise ValueError(
                "TimingAssessment.has_signal=False must not carry signals — "
                "set has_signal=True if there are TimingSignal entries."
            )
        return self


class RunRecord(BaseModel):  # <- persisted as JSON
    run_id: str
    created_at: datetime
    product_type: str
    intake: dict
    refine: dict
    coverage: CoverageReport
    low_evidence_mode: bool
    original_product_type: str | None  # set if the user broadened the category
    category_broadening_offered: bool  # NEW in v3 — latch, see §8.1
    products: list[Product]
    timing: TimingAssessment
    verdict: Verdict
    scores: list[Scored]
    caveats: list[str]
    model_ids: dict[str, str]  # for reproducible rescore
    trusted_sources: list[str] = []  # §12 seam — always empty in v1


# ---------------------------------------------------------------------------
# §4.1 — Confidence is computed, not judged.
#
# Exact mechanism from the spec — do not change; only the weights are
# tunable later, per §16 item 1.
#
# Usage pattern: `confidence` is a required field on EvidenceProfile, so
# construct the profile with a provisional value, compute the real one, then
# assign it back (EvidenceProfile.model_config has validate_assignment=True,
# so this reassignment is still bound-checked):
#
#     profile = EvidenceProfile(confidence=0.0, **other_fields)
#     profile.confidence = compute_confidence(profile)
# ---------------------------------------------------------------------------


def compute_confidence(e: EvidenceProfile) -> float:
    raw = (
        0.15 * float(e.has_tier1_specs)
        + 0.20 * float(e.has_methodology_backed_source)
        + 0.40 * min(e.independent_review_count, 4) / 4.0
        + 0.15 * e.corroboration_ratio
        + 0.10 * (1.0 - e.conflict_ratio)
    )
    return round(min(max(raw, 0.0), 1.0), 3)
