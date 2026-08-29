"""Startup orphan scan (spec `docs/web_handoff.md` §5, web build order W7).

No in-memory registry survives a server restart (§0/§1: "the in-memory
future is an optimization for the common case, not the durability story").
A run that was parked mid-question when the process died leaves behind
exactly what `store/checkpoint.py` already wrote — some completed phase
checkpoints, no `record.json` — and nothing else remembers it was ever
running. This module finds those runs once, at server startup, so
`GET /runs/{id}` can render "interrupted, resume via the CLI" instead of a
bare 404 or (worse) pretending to resume it in-browser, which §5 explicitly
forbids.
"""

from __future__ import annotations

from dataclasses import dataclass

from product_scout.store import checkpoint
from product_scout.store.runs import RunStore


@dataclass(frozen=True)
class OrphanedRun:
    run_id: str
    last_completed_phase: str | None  # snake_case, from store/checkpoint.PHASE_NAMES


def scan_orphaned_runs(run_store: RunStore) -> dict[str, OrphanedRun]:
    """Iterates `run_store.runs_dir` directly — NOT `RunStore.list_run_ids()`,
    which only returns directories that already have a `record.json` (i.e.
    completed runs) and would therefore never surface an in-flight one.
    A directory counts as orphaned when it has at least one phase
    checkpoint but no `record.json`; an empty directory (no checkpoint at
    all — e.g. one this very process is about to write into for a brand
    new run) is not orphaned, it's just not a run yet."""
    orphans: dict[str, OrphanedRun] = {}
    if not run_store.runs_dir.exists():
        return orphans
    for run_dir in run_store.runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        if run_store.exists(run_id):
            continue  # has record.json — completed, not orphaned
        completed = checkpoint.completed_phases(run_dir)
        if not completed:
            continue  # no checkpoint at all — not a real in-flight run
        orphans[run_id] = OrphanedRun(run_id=run_id, last_completed_phase=completed[-1])
    return orphans


__all__ = ["OrphanedRun", "scan_orphaned_runs"]
