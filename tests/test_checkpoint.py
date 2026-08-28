"""Tests for store/checkpoint.py (spec docs/handoff.md §16.1)."""

import pytest

from product_scout.store import checkpoint


def test_save_then_load_round_trips(tmp_path):
    checkpoint.save_checkpoint(tmp_path, "survey", {"coverage": "rich"})
    assert checkpoint.load_checkpoint(tmp_path, "survey") == {"coverage": "rich"}


def test_load_missing_phase_returns_none(tmp_path):
    assert checkpoint.load_checkpoint(tmp_path, "survey") is None


def test_save_rejects_unknown_phase_name(tmp_path):
    with pytest.raises(ValueError):
        checkpoint.save_checkpoint(tmp_path, "not_a_real_phase", {})


def test_completed_phases_in_sequence_order_not_write_order(tmp_path):
    # Write out of order — completed_phases() must still return §1 sequence order.
    checkpoint.save_checkpoint(tmp_path, "extraction", {})
    checkpoint.save_checkpoint(tmp_path, "intake", {})
    checkpoint.save_checkpoint(tmp_path, "survey", {})
    assert checkpoint.completed_phases(tmp_path) == ["intake", "survey", "extraction"]


def test_completed_phases_empty_when_nothing_checkpointed(tmp_path):
    assert checkpoint.completed_phases(tmp_path) == []


def test_latest_completed_phase_is_furthest_in_sequence(tmp_path):
    checkpoint.save_checkpoint(tmp_path, "survey", {})
    checkpoint.save_checkpoint(tmp_path, "intake", {})
    assert checkpoint.latest_completed_phase(tmp_path) == "survey"


def test_latest_completed_phase_none_when_nothing_checkpointed(tmp_path):
    assert checkpoint.latest_completed_phase(tmp_path) is None


def test_scoring_and_synthesis_checkpoint_independently(tmp_path):
    """§1: Phase 6 is split (SCORING/6a, SYNTHESIS/6b) precisely so each has
    its own re-prompt/resume boundary."""
    checkpoint.save_checkpoint(tmp_path, "scoring", {"scores": []})
    assert checkpoint.latest_completed_phase(tmp_path) == "scoring"
    assert checkpoint.load_checkpoint(tmp_path, "synthesis") is None
    checkpoint.save_checkpoint(tmp_path, "synthesis", {"verdict": "BUY"})
    assert checkpoint.latest_completed_phase(tmp_path) == "synthesis"


def test_save_checkpoint_overwrites_previous_value(tmp_path):
    checkpoint.save_checkpoint(tmp_path, "timing", {"signal_found": False})
    checkpoint.save_checkpoint(tmp_path, "timing", {"signal_found": True})
    assert checkpoint.load_checkpoint(tmp_path, "timing") == {"signal_found": True}
