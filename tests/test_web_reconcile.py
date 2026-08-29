"""Startup orphan-scan unit tests (web build order W7) — pure filesystem,
no HTTP, no model calls."""

from __future__ import annotations

from product_scout.store.runs import RunStore
from product_scout.web.reconcile import scan_orphaned_runs
from tests.conftest import make_run_record


def test_run_with_checkpoint_but_no_record_is_orphaned(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")
    store.save_checkpoint("run-1", "intake", {"product_type": "standing desks"})

    orphans = scan_orphaned_runs(store)

    assert "run-1" in orphans
    assert orphans["run-1"].last_completed_phase == "intake"


def test_run_with_record_is_not_orphaned_even_with_checkpoints(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")
    store.save_checkpoint("run-1", "intake", {})
    store.save(make_run_record(run_id="run-1"))

    orphans = scan_orphaned_runs(store)

    assert "run-1" not in orphans


def test_empty_run_directory_is_not_orphaned(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")
    (store.runs_dir / "run-1").mkdir(parents=True)

    orphans = scan_orphaned_runs(store)

    assert "run-1" not in orphans


def test_multiple_orphans_are_all_returned(tmp_path):
    store = RunStore(root=tmp_path / ".product-scout")
    store.save_checkpoint("run-1", "intake", {})
    store.save_checkpoint("run-2", "survey", {})
    store.save_checkpoint("run-2", "intake", {})
    store.save(make_run_record(run_id="run-3"))  # completed, excluded

    orphans = scan_orphaned_runs(store)

    assert set(orphans) == {"run-1", "run-2"}
    assert orphans["run-2"].last_completed_phase == "survey"  # latest, not first


def test_no_runs_directory_at_all_returns_empty(tmp_path):
    # RunStore's own __init__ creates runs_dir, so simulate a store whose
    # directory was removed after construction (or never existed) by
    # pointing at a path that doesn't exist and was never touched.
    store = RunStore(root=tmp_path / ".product-scout")
    import shutil

    shutil.rmtree(store.runs_dir)

    orphans = scan_orphaned_runs(store)

    assert orphans == {}
