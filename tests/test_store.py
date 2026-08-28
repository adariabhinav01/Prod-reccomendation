"""Round-trip tests for RunStore and RunIndex."""

import json

import pytest

from product_scout.store.runs import RunStore, generate_run_id


@pytest.fixture
def store(tmp_path):
    return RunStore(root=tmp_path / ".product-scout")


def test_generate_run_id_is_unique():
    assert generate_run_id() != generate_run_id()


def test_save_creates_record_json(store, run_record_factory):
    record = run_record_factory(run_id="run-a")
    path = store.save(record)
    assert path.exists()
    assert path == store.record_path("run-a")


def test_save_then_load_round_trips(store, run_record_factory):
    record = run_record_factory(run_id="run-b")
    store.save(record)
    loaded = store.load("run-b")
    assert loaded == record


def test_load_missing_run_id_raises(store):
    with pytest.raises(FileNotFoundError):
        store.load("does-not-exist")


def test_list_run_ids(store, run_record_factory):
    store.save(run_record_factory(run_id="run-1"))
    store.save(run_record_factory(run_id="run-2"))
    assert store.list_run_ids() == ["run-1", "run-2"]


def test_save_adds_index_entry(store, run_record_factory):
    record = run_record_factory(run_id="run-c", product_type="standing desks")
    store.save(record)
    entries = store.index.list_entries()
    assert len(entries) == 1
    assert entries[0].run_id == "run-c"
    assert entries[0].category == "standing desks"
    assert entries[0].verdict == "BUY"
    assert entries[0].top_pick == "Widget Pro"


def test_index_rebuild_matches_saved_entries(store, run_record_factory):
    store.save(run_record_factory(run_id="run-d"))
    store.save(run_record_factory(run_id="run-e"))
    store.index.index_path.unlink()
    rebuilt = store.index.rebuild()
    assert {e.run_id for e in rebuilt} == {"run-d", "run-e"}
    assert store.index.index_path.exists()


def test_index_rebuild_skips_corrupt_record(store, run_record_factory):
    store.save(run_record_factory(run_id="run-good"))
    bad_dir = store.runs_dir / "run-bad"
    bad_dir.mkdir()
    (bad_dir / "record.json").write_text("{not valid json", encoding="utf-8")
    rebuilt = store.index.rebuild()
    ids = {e.run_id for e in rebuilt}
    assert "run-good" in ids
    assert "run-bad" not in ids


def test_index_survives_corrupted_index_json(store, run_record_factory):
    store.save(run_record_factory(run_id="run-f"))
    store.index.index_path.write_text("not json at all", encoding="utf-8")
    assert store.index.list_entries() == []  # never raises
    rebuilt = store.index.rebuild()
    assert {e.run_id for e in rebuilt} == {"run-f"}


def test_list_entries_filter_by_category(store, run_record_factory):
    store.save(run_record_factory(run_id="run-g", product_type="standing desks"))
    store.save(run_record_factory(run_id="run-h", product_type="paddleboards"))
    filtered = store.index.list_entries(category="paddleboards")
    assert [e.run_id for e in filtered] == ["run-h"]


def test_index_json_on_disk_shape_matches_spec(store, run_record_factory):
    # §13: `index.json  # run_id -> {category, created_at, verdict, top_pick,
    # run_path}` — the value object has 5 keys, run_id is not duplicated
    # inside it (it's already the dict key).
    store.save(run_record_factory(run_id="run-j"))
    raw = json.loads(store.index.index_path.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"run-j"}
    assert set(raw["run-j"].keys()) == {
        "category", "created_at", "verdict", "top_pick", "run_path",
    }
    # But the in-memory API stays self-describing — run_id comes back on
    # the IndexEntry, reconstructed from the dict key.
    entries = store.index.list_entries()
    assert entries[0].run_id == "run-j"


def test_store_uses_injected_root_not_default_home(tmp_path, run_record_factory):
    isolated_root = tmp_path / "isolated"
    store = RunStore(root=isolated_root)
    store.save(run_record_factory(run_id="run-i"))
    assert (isolated_root / "runs" / "run-i" / "record.json").exists()
    assert (isolated_root / "index.json").exists()


# -- checkpointing delegation (§16.1) -----------------------------------------


def test_store_save_and_load_checkpoint(store):
    store.save_checkpoint("run-k", "survey", {"coverage": "rich"})
    assert store.load_checkpoint("run-k", "survey") == {"coverage": "rich"}


def test_store_latest_completed_phase(store):
    assert store.latest_completed_phase("run-l") is None
    store.save_checkpoint("run-l", "intake", {})
    store.save_checkpoint("run-l", "survey", {})
    assert store.latest_completed_phase("run-l") == "survey"


def test_store_checkpoint_path_lives_under_run_dir(store):
    store.save_checkpoint("run-m", "extraction", {"products": []})
    assert (store.run_dir("run-m") / "phases" / "extraction.json").exists()
