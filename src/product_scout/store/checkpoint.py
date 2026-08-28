"""Per-phase checkpointing for resumable runs (spec docs/handoff.md §16.1).

    ~/.product-scout/runs/<run_id>/phases/<n>.json

Each phase writes a checkpoint on completion; `scout research --resume
<run_id>` picks up from the last completed phase (§16.1: "Phase 3 can be
dozens of fetches; losing it to a transient failure is unacceptable.").

**Naming choice, flagged**: §16's storage layout literally shows
`phases/<n>.json`, suggesting an integer. But §1's phase sequence numbers
phase 6 as a split, `6a`/`6b` (SCORING / SYNTHESIS) — not an integer — and
§3's `PHASES` dict (the orchestrator's own source of truth for what a phase
*is*) keys phases by name ("survey", "refine", "extraction", ...), not by
number. Keying checkpoints by that same name string sidesteps the 6a/6b
ambiguity entirely and stays consistent with the one place phase identity is
already unambiguous. `RunRecord.truncated_at_phase` (an `int`) is a separate,
narrower concern — the truncation point in the fixed phase *sequence*, not a
checkpoint filename — and is assigned by the orchestrator (build order step
13), not derived here.

A checkpoint holds a phase's raw output (whatever JSON-serializable shape
that phase produces) — it is deliberately NOT a validated `RunRecord`. The
record itself is only assembled and saved once, at the end, by
`RunStore.save()`. This keeps `checkpoint.py` from needing to know anything
about the data model.
"""

from __future__ import annotations

import json
from pathlib import Path

# §1 — the fixed phase sequence, by name. "scoring" and "synthesis" are the
# two halves of Phase 6 (§1: "Why Phase 6 is split"); each checkpoints
# separately, since SYNTHESIS reading a scored set is a distinct completed
# step from SCORING producing one.
PHASE_NAMES: tuple[str, ...] = (
    "intake",
    "survey",
    "refine",
    "extraction",
    "timing",
    "prior_gen",
    "scoring",
    "synthesis",
    "render",
)


def _validate_phase(phase: str) -> str:
    if phase not in PHASE_NAMES:
        raise ValueError(f"Unknown phase {phase!r}; expected one of {PHASE_NAMES}")
    return phase


def phases_dir(run_dir: Path) -> Path:
    return run_dir / "phases"


def checkpoint_path(run_dir: Path, phase: str) -> Path:
    _validate_phase(phase)
    return phases_dir(run_dir) / f"{phase}.json"


def save_checkpoint(run_dir: Path, phase: str, data: dict) -> Path:
    """Write phases/<phase>.json atomically."""
    d = phases_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(run_dir, phase)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)  # atomic on POSIX
    return path


def load_checkpoint(run_dir: Path, phase: str) -> dict | None:
    """Returns None when the phase hasn't checkpointed yet — not an error;
    callers use this to decide where `--resume` picks up."""
    path = checkpoint_path(run_dir, phase)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def completed_phases(run_dir: Path) -> list[str]:
    """Phase names with a checkpoint on disk, in §1 sequence order (not
    filesystem iteration order, which is unspecified)."""
    d = phases_dir(run_dir)
    if not d.exists():
        return []
    present = {p.stem for p in d.glob("*.json")}
    return [name for name in PHASE_NAMES if name in present]


def latest_completed_phase(run_dir: Path) -> str | None:
    """The furthest point `--resume` can pick up from. None means no phase
    has checkpointed yet — resume from the start."""
    completed = completed_phases(run_dir)
    return completed[-1] if completed else None
