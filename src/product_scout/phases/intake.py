"""Phase 0 — INTAKE (spec docs/handoff.md §7, build order step 3).

"Deterministic Python, no model" (§1). The four fixed questions run before
any model call and before Phase 1's coverage probe, so `run_intake` takes
`product_type` as a plain string — the category is assumed already known
(e.g. from a CLI argument), not asked here; §7 only defines the four
questions below.

### SPEC GAP-FILL — `Intake` output shape

§7 names four questions but §4's `RunRecord.intake` is a bare, untyped
`dict` — no schema is given anywhere in the spec for what Phase 0 must
produce. `Intake` here is designed from §7's own wording, one field per
question. It is deliberately **not** referenced by `RunRecord` (which keeps
`intake: dict` exactly as spec'd) — `Intake` exists so construction is
validated hard and fails loudly (§4's own directive, applied here by
analogy), and whatever wires this phase into the orchestrator later stores
`Intake.model_dump()` into that dict.

### SPEC GAP-FILL — a dollar amount for "flexible" budget too

§7 item 2 gives three budget modes but only spells out what the number does
for `hard_ceiling` (§5.1's above-budget reference row is a cutoff against
it). It says nothing about whether "flexible if quality justifies it" needs
a number at all. `Intake` requires one for `flexible` too, on the theory
that "flexible" has to be flexible *relative to something* — without an
anchor, Opus (Phase 7, not yet built) has no way to judge whether a given
price is a reasonable stretch or a big one, and "flexible" would silently
collapse into "no limit" in practice. E.g. a $600 anchor makes a $650 desk
an easy stretch ("$50 more for steel vs. laminate") and a $1,400 desk a
hard sell, both readable as `in_budget` judgment calls with a stated
reason — with no anchor at all, every price looks equally arbitrary. Not
spec-mandated; revisit if a later phase's actual use of `budget_usd` proves
this wrong.

### SPEC GAP-FILL — required-feature filtering

CLAUDE.md's step-3 line is explicit: "Required features become filters, not
preferences." §7 item 3 states the same rule but never specifies a
mechanism, and `Product` (§4) has no dedicated features field — the closest
analog is `specs: dict[str, SourcedValue]`. `filter_by_required_features`
below matches a required feature by case-insensitive substring against
either a spec's key or its extracted value, since a real extractor might
tag a feature either way (e.g. key "Bluetooth" vs. value "Bluetooth 5.0"
under a generic key like "connectivity"). This is deliberately permissive:
a false-positive match is the safer failure mode than wrongly excluding a
real product on a technicality. This function is pure and callable now, but
nothing calls it until Discovery/Extraction exist (step 5) — no `Product`
list exists this early in the pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from product_scout.io.port import QuestionPort
from product_scout.models import Product


class Intake(BaseModel):
    """Phase 0's output — see the module SPEC GAP-FILL note above."""

    owns_current: bool
    current_model: str | None  # required iff owns_current
    budget_mode: Literal["hard_ceiling", "flexible", "no_limit"]
    budget_usd: float | None = Field(default=None, gt=0)  # required iff not no_limit
    required_features: list[str] = []
    named_candidates: list[str] = []

    @model_validator(mode="after")
    def _current_model_matches_ownership(self) -> "Intake":
        if self.owns_current and not self.current_model:
            raise ValueError(
                "Intake.current_model is required when owns_current=True (§7 item 1)."
            )
        if not self.owns_current and self.current_model:
            raise ValueError(
                "Intake.current_model must be None when owns_current=False "
                f"(§7 item 1); got {self.current_model!r}."
            )
        return self

    @model_validator(mode="after")
    def _budget_amount_matches_mode(self) -> "Intake":
        if self.budget_mode == "no_limit" and self.budget_usd is not None:
            raise ValueError(
                "Intake.budget_usd must be None when budget_mode='no_limit' "
                f"(§7 item 2); got {self.budget_usd!r}."
            )
        if self.budget_mode != "no_limit" and self.budget_usd is None:
            raise ValueError(
                "Intake.budget_usd is required when budget_mode != 'no_limit' "
                "(§7 item 2) — including 'flexible', which needs an anchor "
                "too; see the module SPEC GAP-FILL note."
            )
        return self


_OWNERSHIP_OPTIONS = ["Yes, I'm upgrading", "No, this is a new purchase"]

_BUDGET_OPTIONS = [
    "Hard ceiling",
    "Flexible if quality justifies it",
    "No limit",
]
_BUDGET_MODE_BY_OPTION: dict[str, Literal["hard_ceiling", "flexible", "no_limit"]] = {
    "Hard ceiling": "hard_ceiling",
    "Flexible if quality justifies it": "flexible",
    "No limit": "no_limit",
}


def _parse_list(raw: str) -> list[str]:
    """Comma-separated free text -> a clean list. Blank input -> []."""
    return [item.strip() for item in raw.split(",") if item.strip()]


async def _ask_budget_amount(port: QuestionPort) -> float:
    """Loops until a positive number comes back — ask_text has no built-in
    validation (§7's port contract), so the phase owns retry/feedback here.
    """
    prompt = "What's the dollar amount? (e.g. 500)"
    while True:
        raw = await port.ask_text(prompt)
        try:
            amount = float(raw)
        except ValueError:
            prompt = f"{raw!r} isn't a number. What's the dollar amount? (e.g. 500)"
            continue
        if amount <= 0:
            prompt = "The amount must be greater than 0. What's the dollar amount?"
            continue
        return amount


async def run_intake(product_type: str, port: QuestionPort) -> Intake:
    """Ask the four §7 questions, in order, and return the validated result.

    All `ask()` calls use `escape_hatch="none"` — the §9 two-attempt loop is
    Phase 6/7-specific; these are fixed, deterministic questions asked
    before any model call.
    """
    # 1. Ownership (§7 item 1) — gates KEEP_CURRENT downstream (§6.2, step 7).
    ownership = await port.ask(
        f"Do you already own a {product_type}?",
        _OWNERSHIP_OPTIONS,
        escape_hatch="none",
    )
    owns_current = ownership == "Yes, I'm upgrading"
    current_model = None
    if owns_current:
        current_model = await port.ask_text(
            f"What's the current {product_type} model you own?"
        )

    # 2. Budget (§7 item 2) — hard ceiling still yields an above-budget
    # reference row later (§5.1); that enforcement is out of scope here.
    budget_choice = await port.ask(
        "What's your budget?", _BUDGET_OPTIONS, escape_hatch="none"
    )
    budget_mode = _BUDGET_MODE_BY_OPTION[budget_choice]
    budget_usd = None
    if budget_mode != "no_limit":
        budget_usd = await _ask_budget_amount(port)

    # 3. Required features (§7 item 3) — filters, not preferences; see
    # filter_by_required_features below.
    features_raw = await port.ask_text(
        "Any required features? (comma-separated, or leave blank for none)"
    )
    required_features = _parse_list(features_raw)

    # 4. Named candidates (§7 item 4) — enter the shortlist automatically
    # once Discovery exists (step 5); captured here only.
    candidates_raw = await port.ask_text(
        "Any specific products you're already considering? "
        "(comma-separated, or leave blank for none)"
    )
    named_candidates = _parse_list(candidates_raw)

    return Intake(
        owns_current=owns_current,
        current_model=current_model,
        budget_mode=budget_mode,
        budget_usd=budget_usd,
        required_features=required_features,
        named_candidates=named_candidates,
    )


def filter_by_required_features(
    products: list[Product], required_features: list[str]
) -> list[Product]:
    """§7 item 3 / CLAUDE.md: required features are filters, not preferences.

    A product is kept only if every required feature matches — by
    case-insensitive substring — a spec key or its extracted value for that
    product. See the module SPEC GAP-FILL note for why substring matching
    was chosen. `required_features == []` is a no-op (returns `products`
    unchanged).
    """
    if not required_features:
        return products

    def _has_feature(product: Product, feature: str) -> bool:
        needle = feature.strip().lower()
        if not needle:
            return True  # blank entries impose no constraint
        for key, sourced in product.specs.items():
            if needle in key.lower() or needle in sourced.value.lower():
                return True
        return False

    return [
        product
        for product in products
        if all(_has_feature(product, feature) for feature in required_features)
    ]
