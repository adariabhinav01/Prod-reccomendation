"""Unit tests for rescore.py — §16's `rescore` (build order step 14),
including all three refusal/warning conditions. `run_scoring`/`run_synthesis`
are exercised through fakes, the same pattern `test_orchestrator.py` uses
for `run_pipeline`."""

import asyncio

import pytest

from product_scout import config
from product_scout.orchestrator import _REQUIRED_SKILLS
from product_scout.phases.scoring import RawProductScore, RawScoring
from product_scout.phases.synthesis import RawSynthesis
from product_scout.pricing import PriceOverrideError
from product_scout.rescore import RescoreRefused, run_rescore
from product_scout.settings import LocationSettings, save as save_settings
from product_scout.skills import hash_required_skills, hash_skill
from product_scout.store.runs import RunStore
from tests.conftest import make_location, make_product, make_run_record, make_settings


def run(coro):
    return asyncio.run(coro)


class FakeScorer:
    def __init__(self, raw: RawScoring):
        self._raw = raw
        self.calls: list[tuple] = []

    async def propose_scores(self, products, survey, intake, topics, low_evidence_mode, feedback):
        self.calls.append((products, low_evidence_mode, feedback))
        return self._raw


class FakeSynthesizer:
    def __init__(self, raw: RawSynthesis):
        self._raw = raw
        self.calls: list[tuple] = []

    async def synthesize(self, products, scores, survey, intake, timing, low_evidence_mode, top_picks):
        self.calls.append((products, scores, low_evidence_mode))
        return self._raw


def _happy_scoring(price_score=8.5) -> RawScoring:
    return RawScoring(
        scores=[
            RawProductScore(
                product_name="Widget Pro",
                score=price_score,
                rationale="Even better at the new price.",
                role="recommendation",
                strength_archetype="value",
                score_at_minus_10pct=8.7,
                score_at_minus_20pct=8.9,
                score_at_minus_30pct=9.1,
            )
        ]
    )


def _happy_synthesis() -> RawSynthesis:
    return RawSynthesis(verdict_action="BUY", verdict_reasoning="Great value now.", verdict_timing_note=None)


def _settings_path(tmp_path, **location_overrides):
    path = tmp_path / "config.toml"
    location = LocationSettings(country="US", currency="USD")
    location = location.model_copy(update=location_overrides) if location_overrides else location
    save_settings(make_settings(location=location), path)
    return path


def _seeded_store(tmp_path, record):
    store = RunStore(root=tmp_path / ".product-scout")
    store.save(record)
    return store


def _fakes():
    return dict(scorer=FakeScorer(_happy_scoring()), synthesizer=FakeSynthesizer(_happy_synthesis()))


def _matching_hashes() -> dict[str, str]:
    """Real, current hashes for every required skill — used by tests that
    aren't specifically about the skill-hash-mismatch warning, so that
    warning doesn't fire as an incidental side effect of the fixture
    default (`make_run_record()`'s `skill_hashes={}` mismatches by
    construction)."""
    return hash_required_skills(_REQUIRED_SKILLS)


# -- happy path --------------------------------------------------------------


def test_rescore_happy_path_writes_a_linked_new_run(tmp_path):
    record = make_run_record(skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    new_record = result.record
    assert new_record.run_id != record.run_id
    assert new_record.rescored_from == record.run_id
    assert store.exists(new_record.run_id)
    assert store.report_path(new_record.run_id).exists()

    product = next(p for p in new_record.products if p.name == "Widget Pro")
    assert product.pricing.upfront_amount == 149.0
    assert product.pricing.price_overridden is True
    assert result.warnings == []


def test_rescore_writes_scoring_and_synthesis_checkpoints_for_the_new_run(tmp_path):
    """§16's storage diagram (`runs/<run_id>/phases/<n>.json`) is general
    to every run directory, not just ones `scout research` produced —
    rescored runs should get the same `phases/` shape for the two phases
    that actually ran."""
    record = make_run_record(skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    assert store.load_checkpoint(result.record.run_id, "scoring") is not None
    assert store.load_checkpoint(result.record.run_id, "synthesis") is not None


def test_rescore_with_no_overrides_still_reruns_scoring_and_synthesis(tmp_path):
    """§16: 'Not a refusal — re-scoring against improved logic is often
    the point.' A rescore with zero price changes is a legitimate way to
    pick up an updated recommendation-logic skill against unchanged
    prices — `run_rescore` must accept an empty `overrides` dict."""
    record = make_run_record(skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(run_rescore(record.run_id, {}, store, settings_path=settings_path, **_fakes()))

    new_record = result.record
    assert new_record.rescored_from == record.run_id
    product = next(p for p in new_record.products if p.name == "Widget Pro")
    assert product.pricing.upfront_amount == record.products[0].pricing.upfront_amount
    assert product.pricing.price_overridden is False
    assert new_record.verdict.action == "BUY"  # came from the fresh synthesis call


def test_rescore_preserves_the_original_record_unchanged(tmp_path):
    record = make_run_record(skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    run(run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes()))

    original = store.load(record.run_id)
    assert original.products[0].pricing.upfront_amount == 199.0
    assert original.products[0].pricing.price_overridden is False


def test_rescore_model_ids_keeps_haiku_and_refreshes_opus(tmp_path):
    record = make_run_record(model_ids={"haiku": "old-haiku-id", "opus": "old-opus-id"})
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    assert result.record.model_ids["haiku"] == "old-haiku-id"
    assert result.record.model_ids["opus"] == config.MODEL_OPUS


def test_rescore_updates_only_recommendation_logic_skill_hash(tmp_path):
    stale_hashes = {name: "stale" for name in _REQUIRED_SKILLS}
    record = make_run_record(skill_hashes=stale_hashes)
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    new_hashes = result.record.skill_hashes
    assert new_hashes[config.RECOMMENDATION_LOGIC_SKILL] == hash_skill(config.RECOMMENDATION_LOGIC_SKILL)
    for name in _REQUIRED_SKILLS:
        if name != config.RECOMMENDATION_LOGIC_SKILL:
            assert new_hashes[name] == "stale"


# -- location mismatch -> refuse ---------------------------------------------


def test_rescore_refuses_on_location_mismatch(tmp_path):
    record = make_run_record(location=make_location(country="US", currency="USD"))
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path, country="DE", currency="EUR")

    with pytest.raises(RescoreRefused):
        run(run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes()))

    assert store.index.list_entries() and len(store.index.list_entries()) == 1  # no new run saved


def test_rescore_refuses_when_location_not_configured(tmp_path):
    record = make_run_record()
    store = _seeded_store(tmp_path, record)
    settings_path = tmp_path / "no-config-here.toml"  # nothing saved -> unconfigured

    with pytest.raises(RescoreRefused):
        run(run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes()))


# -- truncated_at_phase -> warn -----------------------------------------------


def test_rescore_warns_on_truncated_at_phase_but_still_succeeds(tmp_path):
    record = make_run_record(truncated_at_phase=3, skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    assert any("truncated" in w.lower() for w in result.warnings)
    assert result.record.truncated_at_phase == 3


# -- skill-hash mismatch -> warn ----------------------------------------------


def test_rescore_warns_on_skill_hash_mismatch_but_still_succeeds(tmp_path):
    record = make_run_record(skill_hashes={name: "deliberately-stale" for name in _REQUIRED_SKILLS})
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    assert len(result.warnings) == 1
    for name in _REQUIRED_SKILLS:
        assert name in result.warnings[0]


def test_rescore_no_skill_hash_warning_when_hashes_match(tmp_path):
    record = make_run_record(skill_hashes=_matching_hashes())
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    result = run(
        run_rescore(record.run_id, {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes())
    )

    assert result.warnings == []


# -- input validation (not one of the three §16 conditions, but must not
# silently proceed) -----------------------------------------------------------


def test_rescore_raises_on_unknown_product_name(tmp_path):
    record = make_run_record()
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    with pytest.raises(PriceOverrideError):
        run(
            run_rescore(
                record.run_id, {"Nonexistent Product": 149.0}, store, settings_path=settings_path, **_fakes()
            )
        )

    assert len(store.index.list_entries()) == 1  # no new run saved


def test_rescore_raises_on_disallowed_model_type(tmp_path):
    from tests.conftest import make_pricing_model

    subscription_product = make_product(
        name="Sub Product", pricing=make_pricing_model(model_type="subscription_only", upfront_amount=None)
    )
    record = make_run_record(products=[subscription_product])
    store = _seeded_store(tmp_path, record)
    settings_path = _settings_path(tmp_path)

    with pytest.raises(PriceOverrideError):
        run(
            run_rescore(
                record.run_id, {"Sub Product": 19.0}, store, settings_path=settings_path, **_fakes()
            )
        )


def test_rescore_raises_file_not_found_for_unknown_run_id(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")
    settings_path = _settings_path(tmp_path)

    with pytest.raises(FileNotFoundError):
        run(
            run_rescore(
                "no-such-run", {"Widget Pro": 149.0}, store, settings_path=settings_path, **_fakes()
            )
        )
