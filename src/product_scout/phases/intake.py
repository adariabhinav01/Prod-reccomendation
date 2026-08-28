"""Phase 0 — INTAKE (spec docs/handoff.md §7, build order step 4).

§1: "0 INTAKE Python ── fixed questions, no model calls." §9 opens with
"Read by Opus in Phase 2," and §9.8 is the one sentence distinguishing this
phase from that machinery: "Phase 0 only catches requirements the user knows
to name." So the five §7.1 questions are flat, direct prompts — never routed
through `ask_topic`'s gate/axis composite, which is REFINE's (Phase 2's)
primitive and which needs `Dimension`/`SurveyReport` data that doesn't exist
until Phase 1 (SURVEY) has run. `run_intake` asks its questions via
`QuestionPort.ask_text` (build order step 4) and does its own deterministic
parsing (yes/no, budget regex, CSV splitting) in Python.

`run_intake`'s output is `models.IntakeAnswers` directly — the schema
`RunRecord.intake` actually uses. (An earlier draft of this module defined
its own `Intake` model with a different shape — `owns_current`/`budget_mode`
enum/`named_candidates` — that was never reconciled with `IntakeAnswers` and
has been removed.)

### Location capture (§10.1)

§7.1 item 5: "Location, only if not already in settings (§10.1)." §10.1:
"Asked once in Phase 0 if missing, written back, reused silently." This
phase checks `settings.load(...).location.has_location` first; if already
set, it's reused silently and nothing is asked. If missing, both country and
currency are asked (the spec gives no country -> currency derivation rule)
and written back via `settings.set_value`/`settings.save`.

### Units derivation (§0/§10.1, build order step 11)

"Units: derived from location on first run, overridable." `_ask_location`
also resolves `settings.display.units`: if it's already set (a prior run
derived it, or the user overrode it via `scout config set display.units`),
it's left alone and reused silently, exactly like country/currency above. If
it's still `None` — the "not yet derived" state `settings.py`'s
`DisplaySettings` docstring describes — `render.units.derive_units_from_country`
resolves it from the (possibly just-asked) country and it's written back the
same way. This runs even when country/currency were already set and nothing
was asked this run, since an existing config predating this build step can
have a location with `units` still `None`.

### Upgrade baseline (§7.2)

§7.2 says the user's current product "is researched" and enters scoring
with `role="baseline_current"" — but not by Phase 0, which runs before any
model call exists in the pipeline. This phase's entire contract for the
upgrade path is `current_model: str | None`; the actual research of that
product into a scored `Product` is a downstream phase's job.

### Required features (§7.1 item 3)

"Required features — filters, not preferences." Phase 0's job is capture
only: store the strings in `IntakeAnswers.required_features`. Downstream
representative-selection (EXTRACTION, per §1's "representatives chosen
against requirements") is where the filtering actually happens.
`filter_by_required_features` below is a pure utility for that later
consumer — nothing in this phase calls it, since no `Product` list exists
this early in the pipeline. It matches a required feature by
case-insensitive substring against either a spec's key or its extracted
value, since a real extractor might tag a feature either way (e.g. key
"Bluetooth" vs. value "Bluetooth 5.0" under a generic key like
"connectivity"). Deliberately permissive: a false-positive match is the
safer failure mode than wrongly excluding a real product on a technicality.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from product_scout.io.port import QuestionPort
from product_scout.models import IntakeAnswers, Location, Product
from product_scout.render.units import derive_units_from_country
from product_scout.settings import load, save, set_value

_YES_TOKENS = frozenset(("y", "yes"))
_NO_TOKENS = frozenset(("n", "no"))

# §7.1 item 2: "a single numeric ceiling plus a free-text note... puts the
# highest figure in the ceiling." One free-text question, parsed by regex —
# not a multiple-choice budget-mode UI, which doesn't exist in
# IntakeAnswers's schema at all.
_NUMBER_PATTERN = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)")
_NO_LIMIT_PATTERN = re.compile(r"\b(no limit|unlimited|no cap|n/a|none)\b", re.IGNORECASE)


def _parse_list(raw: str) -> list[str]:
    """Comma-separated free text -> a clean list. Blank input -> []."""
    return [item.strip() for item in raw.split(",") if item.strip()]


async def _ask_yes_no(port: QuestionPort, prompt: str) -> bool:
    """Loops until a yes/no answer comes back. No escape hatch — Phase 0 has
    none (§9.6's escape hatches are REFINE-specific)."""
    while True:
        raw = (await port.ask_text(prompt)).strip().lower()
        if raw in _YES_TOKENS:
            return True
        if raw in _NO_TOKENS:
            return False
        prompt = f"{raw!r} — please answer yes or no."


async def _ask_budget(port: QuestionPort) -> tuple[float | None, str]:
    """§7.1 item 2. Returns (budget_ceiling, budget_note) — note is the raw
    text verbatim (§4.0c: `# verbatim; §7.1`), ceiling is the highest dollar
    figure found, or None if the text says there's no limit. Reprompts
    (blank input included) until one of the two is found — Phase 0 has no
    escape hatch to fall back on, so ambiguous answers just get clarified.
    """
    prompt = (
        "What's your budget? You can give a single number, a tiered "
        "statement (e.g. 'under $220 for something good, $150 for "
        "adequate'), or say 'no limit'."
    )
    while True:
        raw = await port.ask_text(prompt)
        numbers = [float(n.replace(",", "")) for n in _NUMBER_PATTERN.findall(raw)]
        if numbers:
            return max(numbers), raw.strip()
        if _NO_LIMIT_PATTERN.search(raw):
            return None, raw.strip()
        prompt = (
            f"{raw!r} didn't give me a number or 'no limit' — what's the "
            "most you'd spend, or say 'no limit'?"
        )


async def _ask_nonblank(port: QuestionPort, prompt: str) -> str:
    while True:
        raw = (await port.ask_text(prompt)).strip()
        if raw:
            return raw
        prompt = f"That can't be blank — {prompt[0].lower()}{prompt[1:]}"


async def _ask_location(
    port: QuestionPort, settings_path: Path | str | None
) -> tuple[Location, Literal["imperial", "metric"]]:
    """§7.1 item 5 / §10.1: asked once if missing, written back, reused
    silently. No country -> currency derivation is attempted (see module
    docstring) — both are asked explicitly when missing. Also resolves
    `display.units` (§0/§10.1, build order step 11) — see module docstring's
    "Units derivation" section for why this runs on every call, not just the
    branch that actually asked a question."""
    settings = load(settings_path)
    if settings.location.has_location:
        location = Location(
            country=settings.location.country,
            currency=settings.location.currency,
        )
    else:
        country = (
            await _ask_nonblank(port, "What country are you shopping from? (e.g. US, DE, GB)")
        ).upper()
        currency = (
            await _ask_nonblank(
                port, "What currency should prices be shown in? (e.g. USD, EUR, GBP)"
            )
        ).upper()

        settings = set_value(settings, "location.country", country)
        settings = set_value(settings, "location.currency", currency)
        location = Location(country=country, currency=currency)

    units = settings.display.units
    if units is None:
        units = derive_units_from_country(location.country)
        settings = set_value(settings, "display.units", units)

    save(settings, settings_path)
    return location, units


async def run_intake(
    product_type: str,
    port: QuestionPort,
    *,
    settings_path: Path | str | None = None,
) -> tuple[IntakeAnswers, Location, Literal["imperial", "metric"]]:
    """Ask the five §7.1 questions, in order, and return the validated
    result plus the resolved `Location` (a separate top-level `RunRecord`
    field, not nested under `intake`) and `units` (also a separate top-level
    `RunRecord` field, §10.1/build order step 11)."""
    # 1. Ownership (§7 item 1) — gates KEEP_CURRENT downstream (§6.2, step 7).
    owns_current_version = await _ask_yes_no(
        port, f"Do you already own a {product_type}? (yes/no)"
    )
    current_model = None
    if owns_current_version:
        current_model = await port.ask_text(
            f"What's the current {product_type} model you own?"
        )

    # 2. Budget (§7 item 2).
    budget_ceiling, budget_note = await _ask_budget(port)

    # 3. Required features (§7 item 3) — filters, not preferences; see
    # filter_by_required_features below.
    features_raw = await port.ask_text(
        "Any required features? (comma-separated, or leave blank for none)"
    )
    required_features = _parse_list(features_raw)

    # 4. Named candidates (§7 item 4) — enter the shortlist with identical
    # treatment once Discovery/Extraction exist; captured here only.
    candidates_raw = await port.ask_text(
        "Any specific products you're already considering? "
        "(comma-separated, or leave blank for none)"
    )
    candidates_under_consideration = _parse_list(candidates_raw)

    # 5. Location (§7 item 5 / §10.1) — only if not already in settings.
    # Also resolves `units` (build order step 11 — see module docstring).
    location, units = await _ask_location(port, settings_path)

    intake = IntakeAnswers(
        owns_current_version=owns_current_version,
        current_model=current_model,
        budget_ceiling=budget_ceiling,
        budget_note=budget_note,
        required_features=required_features,
        candidates_under_consideration=candidates_under_consideration,
    )
    return intake, location, units


def filter_by_required_features(
    products: list[Product], required_features: list[str]
) -> list[Product]:
    """§7 item 3 / CLAUDE.md: required features are filters, not preferences.

    A product is kept only if every required feature matches — by
    case-insensitive substring — a spec key or its extracted value for that
    product. See the module docstring for why substring matching was
    chosen. `required_features == []` is a no-op (returns `products`
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
