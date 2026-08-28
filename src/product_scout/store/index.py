"""Cross-category run history index (spec docs/handoff.md §16, v7).

'index.json is a derived artifact so a corrupted index is never data loss.'

§16's storage layout doesn't re-specify the on-disk value shape the way v3's
did (`run_id -> {category, created_at, verdict, top_pick, run_path}`); this
module carries that shape forward unchanged since nothing in v7 contradicts
it — flagged in the step 1 plan rather than silently assumed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from product_scout.models import RunRecord


class IndexEntry(BaseModel):
    """One row of run history.

    Carried forward from v3's §13, which documented the on-disk shape as
    `run_id -> {category, created_at, verdict, top_pick, run_path}` — a
    5-field value object, with run_id living only as the dict key, not
    duplicated inside it. v7's §16 doesn't re-specify this; see the module
    docstring. `run_id` is kept
    on this Python model anyway, so a flattened `list[IndexEntry]` (as
    returned by `list_entries()`/`rebuild()`) stays self-describing without
    callers having to drag the dict key along separately. It's populated
    from the dict key on read and stripped again before writing — see
    `_entry_to_disk_value`/`_entry_from_disk_value` — so the persisted file
    matches §13 literally even though the in-memory object carries more.
    """

    run_id: str
    category: str
    created_at: datetime
    verdict: str  # Verdict.action
    top_pick: str | None  # highest-scoring Scored.product_name, or None
    run_path: str


class RunIndex:
    """Reads/writes <root>/index.json; always rebuildable from <root>/runs/."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else (Path.home() / ".product-scout")
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.json"

    # -- internal I/O --------------------------------------------------

    def _load_raw(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupted index.json is never data loss (§13) — treat as
            # empty; callers can rebuild() to repair it from runs/.
            return {}
        return data if isinstance(data, dict) else {}

    def _write_raw(self, data: dict[str, dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.index_path)

    @staticmethod
    def _entry_from_record(record: RunRecord, run_path: Path) -> IndexEntry:
        top_pick = None
        if record.scores:
            top_pick = max(record.scores, key=lambda s: s.score).product_name
        return IndexEntry(
            run_id=record.run_id,
            category=record.product_type,
            created_at=record.created_at,
            verdict=record.verdict.action,
            top_pick=top_pick,
            run_path=str(run_path),
        )

    @staticmethod
    def _entry_to_disk_value(entry: IndexEntry) -> dict:
        """§13's on-disk value has no run_id field — it's the dict key."""
        value = json.loads(entry.model_dump_json())
        value.pop("run_id", None)
        return value

    @staticmethod
    def _entry_from_disk_value(run_id: str, value: dict) -> IndexEntry:
        return IndexEntry.model_validate({**value, "run_id": run_id})

    # -- public API -------------------------------------------------------

    def add_entry(self, record: RunRecord, run_path: Path) -> None:
        data = self._load_raw()
        entry = self._entry_from_record(record, run_path)
        data[record.run_id] = self._entry_to_disk_value(entry)
        self._write_raw(data)

    def rebuild(self) -> list[IndexEntry]:
        """Rebuild index.json from runs/ on disk, skipping corrupt records."""
        data: dict[str, dict] = {}
        entries: list[IndexEntry] = []
        if self.runs_dir.exists():
            for run_dir in sorted(self.runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                record_path = run_dir / "record.json"
                if not record_path.exists():
                    continue
                try:
                    raw = json.loads(record_path.read_text(encoding="utf-8"))
                    record = RunRecord.model_validate(raw)
                except Exception:
                    # Never let one bad record fail the whole rebuild.
                    continue
                entry = self._entry_from_record(record, run_dir)
                data[record.run_id] = self._entry_to_disk_value(entry)
                entries.append(entry)
        self._write_raw(data)
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    def list_entries(self, category: str | None = None) -> list[IndexEntry]:
        data = self._load_raw()
        entries = [
            self._entry_from_disk_value(run_id, value)
            for run_id, value in data.items()
        ]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        if category is not None:
            entries = [e for e in entries if e.category == category]
        return entries
