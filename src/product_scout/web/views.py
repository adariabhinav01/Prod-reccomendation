"""Page-level GET routes (spec `docs/web_handoff.md` §3/§4, web build order
W5/W6): Start, the Run-view action-panel fragment, and History.

Kept separate from `web/runs.py` on purpose — that module is the lifecycle/
action-endpoint surface (POST-heavy, JSON-returning, tightly coupled to
`RunRegistry` lookups); this one is GET-HTML-page rendering, built on top
of `web/templates.py`. `GET /runs/{id}` itself stays in `runs.py` (it's
already there and tightly coupled to the run-lookup/content-negotiation
logic that lives there), but calls `render_action_panel` (this module's
sibling in `templates.py`) for its embedded first-load panel, same as
`GET /runs/{id}/panel` here — one render function, two callers, per
`templates.py`'s own docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from product_scout.io.web_port import PendingQuestion
from product_scout.store.runs import RunStore
from product_scout.web.reconcile import OrphanedRun
from product_scout.web.registry import RunRegistry
from product_scout.web.templates import render_action_panel, render_history_page, render_start_page

router = APIRouter()


@dataclass(frozen=True)
class RunView:
    """The one shape both `GET /runs/{id}` (`web/runs.py`) and
    `GET /runs/{id}/panel` (this module) render from — the result of
    merging the three sources of truth W7 requires: a live `RunSession`,
    a completed `record.json` from a *previous* server process, or an
    orphaned in-flight run from one. `resolve_run_view` below is where
    that merge actually happens; nothing else in this codebase should
    re-derive it."""

    status: str
    product_type: str | None
    current_phase: str | None
    pending: PendingQuestion | None
    error_message: str | None
    interrupted_last_phase: str | None
    created_at: datetime | None


def resolve_run_view(request: Request, run_id: str) -> RunView | None:
    """Precedence: live registry entry (authoritative if present) ->
    completed record on disk (finished under a previous process — DONE/
    TRUNCATED per `truncated_at_phase`, not "interrupted") -> orphaned
    checkpoint-but-no-record (interrupted by a previous process's
    restart) -> `None` (caller 404s)."""
    registry = _registry(request)
    session = registry.get(run_id)
    if session is not None:
        return RunView(
            status=session.status.value,
            product_type=session.product_type,
            current_phase=session.current_phase,
            pending=session.port.pending,
            error_message=session.error_message,
            interrupted_last_phase=None,
            created_at=session.created_at,
        )

    run_store = _run_store(request)
    if run_store.exists(run_id):
        record = run_store.load(run_id)
        status = "truncated" if record.truncated_at_phase is not None else "done"
        return RunView(
            status=status,
            product_type=record.product_type,
            current_phase=None,
            pending=None,
            error_message=None,
            interrupted_last_phase=None,
            created_at=record.created_at,
        )

    orphan = _orphaned_runs(request).get(run_id)
    if orphan is not None:
        return RunView(
            status="interrupted",
            product_type=None,
            current_phase=None,
            pending=None,
            error_message=None,
            interrupted_last_phase=orphan.last_completed_phase,
            created_at=None,
        )

    return None


def _registry(request: Request) -> RunRegistry:
    return request.app.state.registry


def _run_store(request: Request) -> RunStore:
    return request.app.state.run_store


def _orphaned_runs(request: Request) -> dict[str, OrphanedRun]:
    return getattr(request.app.state, "orphaned_runs", {})


@router.get("/", response_class=HTMLResponse)
async def start_page(request: Request) -> HTMLResponse:
    return HTMLResponse(render_start_page(active_run_id=_registry(request).active_run_id()))


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request) -> HTMLResponse:
    entries = _run_store(request).index.list_entries()
    return HTMLResponse(render_history_page(entries))


@router.get("/runs/{run_id}/panel", response_class=HTMLResponse)
async def run_panel(run_id: str, request: Request) -> HTMLResponse:
    """The fragment `GET /runs/{id}`'s full page embeds on first load, and
    that htmx re-fetches on `awaiting_input`/`resumed`/`terminal` SSE
    triggers — see `resolve_run_view` for the three-source merge (W7)
    both this and `GET /runs/{id}` (`web/runs.py`) render from."""
    view = resolve_run_view(request, run_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such run.")
    return HTMLResponse(
        render_action_panel(
            run_id=run_id,
            status=view.status,
            pending=view.pending,
            error_message=view.error_message,
            interrupted_last_phase=view.interrupted_last_phase,
        )
    )


__all__ = ["router", "RunView", "resolve_run_view"]
