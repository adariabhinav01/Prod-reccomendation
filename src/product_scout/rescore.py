"""`scout rescore` (spec docs/handoff.md §16; build order step 14).

Loads a saved `RunRecord`, overrides one or more product prices, re-runs
**phases 6a/6b only** (§16: "No searching, no fetching"), and writes a
linked new run. This module owns exactly that — the three refusal/warning
conditions, the price-override application, and the two-phase re-run — and
nothing else; `cli.py` is a thin wrapper that parses `--set` and prints the
result.

CLAUDE.md invariant 11 ("No conversational carryover between phases...
This is what makes `rescore` work") is why this is possible at all:
`run_rescore` never touches SURVEY/EXTRACTION/TIMING/PRIOR-GEN — everything
6a/6b need (`survey`, `intake`, `topics`, `low_evidence_mode`, `timing`) is
already sitting in the loaded `RunRecord`.

Of the three §16 conditions, only location mismatch is a genuine refusal
(raises `RescoreRefused`, no new run is written). `truncated_at_phase` and
skill-hash mismatch are warnings — surfaced in `RescoreResult.warnings`,
never blocking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from product_scout import config
from product_scout.models import Location, RunRecord
from product_scout.orchestrator import _REQUIRED_SKILLS, _truncation_caveat, merge_caveats
from product_scout.phases.scoring import Scorer, SdkScorer, run_scoring
from product_scout.phases.synthesis import SdkSynthesizer, Synthesizer, run_synthesis
from product_scout.pricing import PriceOverrideError, apply_price_override
from product_scout.settings import load as load_settings
from product_scout.skills import assert_skill_loaded, hash_skill, hash_required_skills
from product_scout.store.checkpoint import PHASE_NAMES
from product_scout.store.runs import RunStore, generate_run_id


class RescoreRefused(Exception):
    """The one hard-stop §16 condition: the run's location no longer
    matches what's currently configured. Region scoping happens in SURVEY
    and shapes which clusters exist at all — a location change can't be
    patched by re-running 6a/6b, so this refuses rather than silently
    answering for the wrong region."""


class RescoreResult(BaseModel):
    record: RunRecord
    warnings: list[str] = []


def _resolve_current_location(settings_path: Path | str | None) -> Location | None:
    settings = load_settings(settings_path)
    if not settings.location.has_location:
        return None
    return Location(country=settings.location.country, currency=settings.location.currency)


def _skill_hash_warnings(record: RunRecord) -> list[str]:
    """§16: 'warn, naming which skills changed.' Checked against every
    skill the ORIGINAL run depended on (`_REQUIRED_SKILLS`), not just
    recommendation-logic — a changed research-protocol skill is still
    worth surfacing even though `rescore` doesn't re-run research."""
    current = hash_required_skills(_REQUIRED_SKILLS)
    changed = [name for name in _REQUIRED_SKILLS if record.skill_hashes.get(name) != current.get(name)]
    if not changed:
        return []
    return [f"Skill(s) changed since this run: {', '.join(changed)}."]


def _truncation_warning(record: RunRecord) -> list[str]:
    if record.truncated_at_phase is None:
        return []
    phase_name = PHASE_NAMES[record.truncated_at_phase]
    return [f"This run's product set is incomplete — research was truncated during {phase_name}."]


async def run_rescore(
    run_id: str,
    overrides: dict[str, float],
    store: RunStore,
    *,
    settings_path: Path | str | None = None,
    scorer: Scorer | None = None,
    synthesizer: Synthesizer | None = None,
) -> RescoreResult:
    """Load `run_id`, apply `overrides` (product name -> new price), re-run
    6a SCORING + 6b SYNTHESIS, save and render a new linked run. Raises
    `FileNotFoundError` (no such run), `RescoreRefused` (location
    mismatch), or `pricing.PriceOverrideError` (unknown product name, or a
    product whose pricing model has no single overridable price)."""
    scorer = scorer or SdkScorer()
    synthesizer = synthesizer or SdkSynthesizer()

    record = store.load(run_id)

    current_location = _resolve_current_location(settings_path)
    if current_location is None:
        raise RescoreRefused(
            "No location is configured — run `scout config set location.country/currency` "
            "first, or start a fresh `scout research` run."
        )
    if current_location != record.location:
        raise RescoreRefused(
            f"This run was researched for {record.location.country}/{record.location.currency}, "
            f"but the currently configured location is "
            f"{current_location.country}/{current_location.currency}. Region scoping happens "
            "in SURVEY and can't be patched by rescoring — start a fresh run instead."
        )

    assert_skill_loaded(config.RECOMMENDATION_LOGIC_SKILL)

    products_by_name = {p.name: p for p in record.products}
    unknown = [name for name in overrides if name not in products_by_name]
    if unknown:
        raise PriceOverrideError(
            f"No product(s) named {unknown} in run {run_id!r}. "
            f"Known products: {sorted(products_by_name)}."
        )

    now = datetime.now(timezone.utc)
    updated_products = [
        apply_price_override(product, overrides[product.name], record.location, record.intake.budget_ceiling, now)
        if product.name in overrides
        else product
        for product in record.products
    ]

    warnings = [*_truncation_warning(record), *_skill_hash_warnings(record)]

    new_run_id = generate_run_id()

    scoring_outcome = await run_scoring(
        updated_products, record.survey, record.intake, record.topics, record.low_evidence_mode, scorer
    )
    store.save_checkpoint(new_run_id, "scoring", scoring_outcome.model_dump(mode="json"))

    synthesis_outcome = await run_synthesis(
        scoring_outcome.products, scoring_outcome.scores, record.survey, record.intake,
        record.timing, record.low_evidence_mode, synthesizer,
    )
    store.save_checkpoint(new_run_id, "synthesis", synthesis_outcome.model_dump(mode="json"))

    caveats = merge_caveats(
        [*scoring_outcome.caveats, *synthesis_outcome.caveats], scoring_outcome.products
    )
    if record.truncated_at_phase is not None:
        caveats.append(_truncation_caveat(record.truncated_at_phase, record.survey, scoring_outcome.products))

    new_skill_hashes = dict(record.skill_hashes)
    new_skill_hashes[config.RECOMMENDATION_LOGIC_SKILL] = hash_skill(config.RECOMMENDATION_LOGIC_SKILL)

    new_record = RunRecord(
        run_id=new_run_id,
        created_at=now,
        product_type=record.product_type,
        original_product_type=record.original_product_type,
        location=record.location,
        units=record.units,
        intake=record.intake,
        survey=record.survey,
        topics=record.topics,
        low_evidence_mode=record.low_evidence_mode,
        commodity_category=record.commodity_category,
        category_broadening_offered=record.category_broadening_offered,
        truncated_at_phase=record.truncated_at_phase,
        products=scoring_outcome.products,
        timing=record.timing,
        verdict=synthesis_outcome.verdict,
        scores=synthesis_outcome.scores,
        caveats=caveats,
        model_ids={"haiku": record.model_ids["haiku"], "opus": config.MODEL_OPUS},
        skill_hashes=new_skill_hashes,
        trusted_sources=record.trusted_sources,
        rescored_from=record.run_id,
    )

    store.save(new_record)
    store.save_report(new_record)

    return RescoreResult(record=new_record, warnings=warnings)
