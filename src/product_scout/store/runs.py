"""Run record persistence (spec docs/handoff.md §16's on-disk layout, v7).

    ~/.product-scout/
    ├── config.toml
    ├── index.json                 # derived; rebuildable by scanning runs/
    └── runs/<run_id>/
        ├── record.json
        ├── report.html            # written later, by render/report.py (step 6)
        └── phases/<n>.json        # checkpoints; §16.1 — see store/checkpoint.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from product_scout.models import RunRecord
from product_scout.store import checkpoint
from product_scout.store.index import RunIndex

DEFAULT_ROOT = Path.home() / ".product-scout"


def generate_run_id() -> str:
    """Sortable, collision-resistant run id: '<UTC timestamp>-<8 hex>'.

    Not specified verbatim in the spec; the run store needs some id
    strategy and callers (later phases) shouldn't each invent their own.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


class RunStore:
    """Load/save RunRecords under an injectable root directory.

    Defaults to ~/.product-scout, but always accepts an override so tests
    never touch the real home directory.
    """

    def __init__(self, root: Path | str | None = None, *, index: RunIndex | None = None):
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.index = index if index is not None else RunIndex(self.root)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def record_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "record.json"

    def exists(self, run_id: str) -> bool:
        return self.record_path(run_id).exists()

    def save(self, record: RunRecord) -> Path:
        """Write record.json atomically, then update the index."""
        run_dir = self.run_dir(record.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = self.record_path(record.run_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        tmp_path.replace(path)  # atomic on POSIX
        self.index.add_entry(record, run_path=run_dir)
        return path

    def load(self, run_id: str) -> RunRecord:
        path = self.record_path(run_id)
        if not path.exists():
            raise FileNotFoundError(
                f"No run record found for run_id={run_id!r} at {path}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunRecord.model_validate(data)

    def list_run_ids(self) -> list[str]:
        if not self.runs_dir.exists():
            return []
        return sorted(
            p.name
            for p in self.runs_dir.iterdir()
            if p.is_dir() and (p / "record.json").exists()
        )

    # -- checkpointing (§16.1) -------------------------------------------
    #
    # Thin delegation to store/checkpoint.py, which operates on a bare
    # run_dir Path and knows nothing about RunStore. Kept here only for
    # caller ergonomics — phase code already has a RunStore, not a raw path.

    def save_checkpoint(self, run_id: str, phase: str, data: dict) -> Path:
        return checkpoint.save_checkpoint(self.run_dir(run_id), phase, data)

    def load_checkpoint(self, run_id: str, phase: str) -> dict | None:
        return checkpoint.load_checkpoint(self.run_dir(run_id), phase)

    def latest_completed_phase(self, run_id: str) -> str | None:
        return checkpoint.latest_completed_phase(self.run_dir(run_id))
