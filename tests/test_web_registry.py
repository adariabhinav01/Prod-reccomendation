"""Unit tests for `RunRegistry`/`RunSession` (web build order W3). Pure
in-memory state — no HTTP layer, no model calls, mirroring the discipline
`test_checkpoint.py`/`test_store.py` use for their own storage-layer units.
"""

import pytest

from product_scout.io.web_port import WebQuestionPort
from product_scout.web.registry import RunRegistry, RunSession, RunStatus
from tests.conftest import make_run_record


def make_session(run_id: str = "run-1", **overrides) -> RunSession:
    defaults = dict(
        run_id=run_id,
        product_type="standing desks",
        location_override=None,
        port=WebQuestionPort(),
    )
    defaults.update(overrides)
    return RunSession(**defaults)


def test_has_active_run_false_until_marked():
    registry = RunRegistry()
    assert registry.has_active_run() is False
    registry.register(make_session())
    assert registry.has_active_run() is False  # registering alone doesn't claim the slot
    registry.mark_active("run-1")
    assert registry.has_active_run() is True
    assert registry.active_run_id() == "run-1"


def test_mark_active_refuses_a_second_run_while_one_is_active():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.register(make_session("run-2"))
    registry.mark_active("run-1")
    with pytest.raises(ValueError):
        registry.mark_active("run-2")


def test_mark_active_is_idempotent_for_the_same_run_id():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    registry.mark_active("run-1")  # must not raise
    assert registry.has_active_run() is True


def test_release_active_frees_the_slot_for_the_next_run():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.register(make_session("run-2"))
    registry.mark_active("run-1")
    registry.release_active("run-1")
    assert registry.has_active_run() is False
    registry.mark_active("run-2")  # must not raise now that the slot is free
    assert registry.active_run_id() == "run-2"


def test_release_active_is_a_no_op_for_a_different_run_id():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    registry.release_active("run-2")  # not the active run — no-op
    assert registry.has_active_run() is True
    assert registry.active_run_id() == "run-1"


def test_register_rejects_a_duplicate_run_id():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    with pytest.raises(ValueError):
        registry.register(make_session("run-1"))


def test_get_returns_none_for_unknown_run_id():
    registry = RunRegistry()
    assert registry.get("nope") is None


def test_mark_awaiting_input_and_running_transitions():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_awaiting_input("run-1")
    assert registry.get("run-1").status is RunStatus.AWAITING_INPUT
    registry.mark_running("run-1")
    assert registry.get("run-1").status is RunStatus.RUNNING


def test_finish_sets_done_and_releases_the_slot():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    record = make_run_record(run_id="run-1", truncated_at_phase=None)
    registry.finish("run-1", record=record, error=None)
    assert registry.get("run-1").status is RunStatus.DONE
    assert registry.has_active_run() is False


def test_finish_sets_truncated_when_the_record_says_so():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    record = make_run_record(run_id="run-1", truncated_at_phase=3)
    registry.finish("run-1", record=record, error=None)
    assert registry.get("run-1").status is RunStatus.TRUNCATED


def test_finish_sets_stopped_when_the_pipeline_returned_none():
    """§8.2 — the user declined to proceed at SURVEY. Not an error, not a
    truncation."""
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    registry.finish("run-1", record=None, error=None)
    assert registry.get("run-1").status is RunStatus.STOPPED


def test_finish_sets_error_and_captures_the_message():
    registry = RunRegistry()
    registry.register(make_session("run-1"))
    registry.mark_active("run-1")
    registry.finish("run-1", record=None, error=RuntimeError("boom"))
    session = registry.get("run-1")
    assert session.status is RunStatus.ERROR
    assert "boom" in session.error_message


def test_abandon_cancels_the_task_and_releases_the_slot():
    class FakeTask:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    registry = RunRegistry()
    task = FakeTask()
    registry.register(make_session("run-1", task=task))
    registry.mark_active("run-1")
    registry.abandon("run-1")
    session = registry.get("run-1")
    assert session.status is RunStatus.ABANDONED
    assert task.cancelled is True
    assert registry.has_active_run() is False


def test_abandon_raises_for_an_unknown_run_id():
    registry = RunRegistry()
    with pytest.raises(KeyError):
        registry.abandon("nope")
