"""SSE progress stream (spec `docs/web_handoff.md` §3.1, web build order W4).

`GET /runs/{id}/events` is a live-tail + trigger channel, nothing more —
`GET /runs/{id}` (web build order W5) is the source of truth, read fresh
from the registry on every request. This module exists so a browser tab
doesn't have to poll for that truth: it tells a connected client "something
changed, go re-fetch" (and, for the two chattiest event kinds, carries the
text to append directly, saving a round trip). A client that never opens
this stream at all — or drops it and reloads the page instead — sees
exactly the same state either way; nothing here is load-bearing for
correctness, which is what makes reconnect (W7) free.

### Wire format: plain text for the chatty events, JSON for triggers

`phase_entered`/`tick`/`phase_result` are written to the wire as a single
escaped text line, not JSON — htmx's default `sse-swap` drops an event's
`data:` payload straight into the DOM, so keeping these three human-
readable means the Run view (W5) needs zero custom JS to display them.
`awaiting_input`/`resumed`/`terminal` stay JSON — they're never swapped
into the page directly, only used as `hx-trigger="sse:<name>"` signals that
make htmx re-`GET` the actual panel, so their payload shape only has to be
convenient for `web/runs.py`'s own callbacks and tests, not for raw display.

### No cost-cap-warning event

Confirmed (again, here, since it's the one thing every SSE consumer will
be tempted to assume exists): no live mid-phase cost-cap signal is emitted
anywhere in the pipeline — `orchestrator.py`'s §13.1 skip-caveat text is
appended to a phase's own caveat list, never passed through
`report_progress`. The only honest live signal is the `terminal` event's
`status`, which is `"truncated"` exactly when `RunRecord.truncated_at_phase`
was set — known for certain only once the run ends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from html import escape as _esc

from product_scout.web.registry import RunSession, RunStatus

# How long to wait for a new event before sending a keepalive comment.
# SCORING/SYNTHESIS/REFINE's Opus calls tick nothing via report_progress
# for however long the model takes to respond — without this, an idle
# proxy or browser could time the connection out mid-phase.
_KEEPALIVE_SECONDS = 15.0

# Event kinds whose payload is written as plain escaped text (a bare
# string), not JSON — see module docstring. Every other event is JSON.
_TEXT_EVENTS = frozenset({"phase_entered", "tick", "phase_result"})

_TERMINAL_STATUSES = frozenset(
    {
        RunStatus.DONE,
        RunStatus.TRUNCATED,
        RunStatus.STOPPED,
        RunStatus.ERROR,
        RunStatus.ABANDONED,
    }
)


def _format(event: str, payload: object) -> bytes:
    if event in _TEXT_EVENTS:
        data = _esc(str(payload))
    else:
        data = json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n".encode()


def _terminal_payload(session: RunSession) -> dict:
    return {"status": session.status.value}


async def event_stream(session: RunSession) -> AsyncIterator[bytes]:
    """Formats one run's published events as SSE wire bytes. Subscribes on
    entry, unsubscribes unconditionally on exit (including a client
    disconnect, which Starlette surfaces as this generator being closed /
    a `CancelledError` propagating through the `finally`)."""
    # Already finished by the time this client connected (e.g. a page
    # reopened after the run completed) — one terminal event, then close.
    # No queue/keepalive loop needed; there is nothing left to tail.
    if session.status in _TERMINAL_STATUSES:
        yield _format("terminal", _terminal_payload(session))
        return

    queue = session.subscribe()
    try:
        # On-connect resync: a client that connects mid-phase shouldn't
        # sit blank until the next natural event. Best-effort only — a
        # page reload already shows the truth regardless (module
        # docstring) — so this is a nicety, not a correctness requirement.
        if session.port.pending is not None:
            yield _format("awaiting_input", {"kind": session.port.pending.kind})
        elif session.current_phase is not None:
            yield _format("phase_entered", session.current_phase)

        while True:
            try:
                event, payload = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield b": keepalive\n\n"
                continue
            yield _format(event, payload)
            if event == "terminal":
                return
    finally:
        session.unsubscribe(queue)
