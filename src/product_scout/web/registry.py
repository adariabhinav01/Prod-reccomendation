"""In-memory run registry (spec `docs/web_handoff.md` §1/§5, web build
order W3).

No "live run" state model exists anywhere in the pipeline today —
`RunRecord` (`models.py`) only exists once RENDER completes, and
`store/checkpoint.py` can only report the *last completed* phase from
disk, never "a phase is currently in flight." This module is that missing
piece, built from scratch and held entirely in server memory (§0: "in-
memory hold — checkpointing is the crash backstop, not the primary
mechanism"). One `RunRegistry` instance is the server's single source of
truth for both "is a run currently active" (§5's one-active-run-at-a-time
enforcement) and "what should `GET /runs/{id}` show right now."
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from product_scout.io.web_port import WebQuestionPort
from product_scout.models import RunRecord


class RunStatus(str, Enum):
    RUNNING = "running"  # a phase is actively executing, or between phases
    AWAITING_INPUT = "awaiting_input"  # a PendingQuestion is parked
    DONE = "done"  # run_pipeline returned a RunRecord, truncated_at_phase is None
    TRUNCATED = "truncated"  # run_pipeline returned a RunRecord, truncated_at_phase is set (§13.1)
    STOPPED = "stopped"  # run_pipeline returned None — user declined at SURVEY (§8.2)
    ERROR = "error"  # the pipeline task raised
    ABANDONED = "abandoned"  # POST /runs/{id}/abandon cancelled it


@dataclass
class RunSession:
    """Everything the web layer knows about one run that a bare
    `store/checkpoint.py` phase-name lookup can't tell it: whether it's
    currently running vs. parked on a question, which phase it's in, and
    the `WebQuestionPort` a web endpoint answers questions through.

    `subscribers` backs the SSE fan-out (web build order W4): each open
    `GET /runs/{id}/events` connection owns one `asyncio.Queue` here, and
    `publish` pushes onto every queue in this list. This lives on the
    session (per-run), not the registry (cross-run), since fan-out is
    always scoped to one run's own subscribers."""

    run_id: str
    product_type: str
    location_override: str | None
    port: WebQuestionPort
    task: asyncio.Task | None = None
    status: RunStatus = RunStatus.RUNNING
    current_phase: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        # A queue may already be gone (double-unsubscribe on a racing
        # disconnect) — tolerate that rather than raising.
        try:
            self.subscribers.remove(queue)
        except ValueError:
            pass

    def publish(self, event: str, payload: object) -> None:
        """Fans `(event, payload)` out to every current subscriber.
        `put_nowait` (never `await put`) — queues are unbounded, and this
        must never block the pipeline task on a slow or dead browser tab.
        Iterates a copy so a queue removed mid-fanout (by a concurrently
        unsubscribing reader) can't raise here."""
        for queue in list(self.subscribers):
            queue.put_nowait((event, payload))

    def set_phase(self, phase: str) -> None:
        """The one place `current_phase` is ever set — called from
        `web/runs.py`'s `on_progress` callback when a `phase_entered`
        event is classified. Also publishes the event, so callers don't
        need to remember to do both."""
        self.current_phase = phase
        self.publish("phase_entered", phase)


class RunRegistry:
    """Holds every `RunSession` this server process has started, plus
    which one (if any) currently occupies the single active-run slot.
    Not persisted — a server restart loses this entirely; that's §5's
    explicit design ("the in-memory future is an optimization for the
    common case, not the durability story"), reconciled on the read side
    by a startup orphan-scan (W7), not by this registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, RunSession] = {}
        self._active_run_id: str | None = None

    def has_active_run(self) -> bool:
        return self._active_run_id is not None

    def active_run_id(self) -> str | None:
        return self._active_run_id

    def register(self, session: RunSession) -> None:
        if session.run_id in self._sessions:
            raise ValueError(f"run_id {session.run_id!r} is already registered.")
        self._sessions[session.run_id] = session

    def get(self, run_id: str) -> RunSession | None:
        return self._sessions.get(run_id)

    def mark_active(self, run_id: str) -> None:
        """Claims the single active-run slot. Raises if one is already
        claimed — callers must check `has_active_run()` first (the 409
        check); this is the enforcement point, not just a convenience."""
        if self._active_run_id is not None and self._active_run_id != run_id:
            raise ValueError(
                f"Cannot mark {run_id!r} active — {self._active_run_id!r} is already active."
            )
        self._active_run_id = run_id

    def release_active(self, run_id: str) -> None:
        """Idempotent: releasing a slot that isn't held (already released,
        or held by a different run) is a no-op, not an error — the done-
        callback and `abandon` can both call this safely regardless of
        ordering."""
        if self._active_run_id == run_id:
            self._active_run_id = None

    def mark_awaiting_input(self, run_id: str) -> None:
        session = self.get(run_id)
        if session is not None:
            session.status = RunStatus.AWAITING_INPUT

    def mark_running(self, run_id: str) -> None:
        session = self.get(run_id)
        if session is not None:
            session.status = RunStatus.RUNNING

    def finish(self, run_id: str, *, record: RunRecord | None, error: BaseException | None) -> None:
        """Called once, from the pipeline task's done-callback. Exactly
        one of `record`/`error` is meaningful:
        - `error` set -> ERROR, message captured.
        - `record is None` (and no error) -> STOPPED (§8.2 user declined).
        - `record.truncated_at_phase is not None` -> TRUNCATED.
        - otherwise -> DONE.
        Unconditionally releases the active slot — a run that ends, any
        way, must free the 409 slot for the next one."""
        session = self.get(run_id)
        if session is not None:
            if error is not None:
                session.status = RunStatus.ERROR
                session.error_message = str(error)
            elif record is None:
                session.status = RunStatus.STOPPED
            elif record.truncated_at_phase is not None:
                session.status = RunStatus.TRUNCATED
            else:
                session.status = RunStatus.DONE
        self.release_active(run_id)

    def abandon(self, run_id: str) -> None:
        session = self.get(run_id)
        if session is None:
            raise KeyError(f"No such run_id {run_id!r}.")
        if session.task is not None:
            session.task.cancel()
        session.status = RunStatus.ABANDONED
        self.release_active(run_id)
