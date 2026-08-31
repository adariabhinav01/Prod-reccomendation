"""Run lifecycle endpoints (spec `docs/web_handoff.md` §3/§5, web build
orders W3-W5): start a run, look up its live state (as JSON or, by
default, the full HTML Run page), stream its live progress over SSE,
resolve its pending question, offer/accept a bailout, and abandon it.

`GET /runs/{id}` is content-negotiated via `?format=json` (see `get_run`'s
own docstring) — its HTML branch and `GET /runs/{id}/panel`
(`web/views.py`) both render from `resolve_run_view` (`web/views.py`),
which is also where W7's three-source merge (live registry / completed
record / orphaned run) lives. `GET /runs/{id}/events` (W4) is the SSE
stream: `web/events.py` formats a session's published events; the three
closures below (`_on_pending_changed`, `_on_progress`, and the
`_run()`/`abandon_run` terminal publishes) are what actually populate that
stream — nothing in `orchestrator.py` or `hooks/progress.py` changes to
make this happen (§3.1: "subscribes to the same source rather than
instrumenting a second time").
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from product_scout.io.web_port import PendingQuestion, WebPortValidationError, WebQuestionPort
from product_scout.models import RunRecord
from product_scout.settings import SettingsError
from product_scout.settings import load as load_settings
from product_scout.settings import save as save_settings
from product_scout.settings import set_value as set_settings_value
from product_scout.store.runs import RunStore, generate_run_id
from product_scout.web.events import event_stream
from product_scout.web.registry import RunRegistry, RunSession
from product_scout.web.templates import render_action_panel, render_run_page
from product_scout.web.views import resolve_run_view

PipelineRunner = Callable[..., Awaitable[RunRecord | None]]

router = APIRouter()


def _registry(request: Request) -> RunRegistry:
    return request.app.state.registry


def _run_store(request: Request) -> RunStore:
    return request.app.state.run_store


def _pipeline_runner(request: Request) -> PipelineRunner:
    return request.app.state.pipeline_runner


@router.post("/runs")
async def start_run(
    request: Request,
    product_type: str = Form(...),
    location: str | None = Form(None),
) -> RedirectResponse:
    registry = _registry(request)
    if registry.has_active_run():
        raise HTTPException(status_code=409, detail="A run is already active.")

    if location:
        # Same one-off sugar as `cli.py`'s `--location`: identical effect
        # to `scout config set location.country XX`, reusing that
        # machinery rather than inventing a per-run-only location path.
        try:
            settings = load_settings()
            settings = set_settings_value(settings, "location.country", location)
            save_settings(settings)
        except SettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_store = _run_store(request)
    pipeline_runner = _pipeline_runner(request)
    run_id = generate_run_id()

    port = WebQuestionPort(
        on_pending_changed=lambda pending: _on_pending_changed(registry, run_id, pending),
        on_progress=lambda event: _on_progress(registry, run_id, event),
    )
    session = RunSession(run_id=run_id, product_type=product_type, location_override=location, port=port)
    # Registered — and the active slot claimed — before the task is even
    # created: closes the race where a client's redirect could otherwise
    # reach `GET /runs/{id}` before the pipeline's first checkpoint write.
    registry.register(session)
    registry.mark_active(run_id)

    async def _run() -> None:
        record: RunRecord | None = None
        error: BaseException | None = None
        try:
            record = await pipeline_runner(product_type, port, run_store, run_id=run_id)
        except asyncio.CancelledError:
            raise  # abandon() cancelled us — registry.abandon() already set ABANDONED
        except BaseException as exc:  # noqa: BLE001 - captured as this run's own status, not swallowed
            error = exc
        registry.finish(run_id, record=record, error=error)
        session.publish("terminal", {"status": session.status.value})

    session.task = asyncio.create_task(_run())
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


def _on_pending_changed(registry: RunRegistry, run_id: str, pending: PendingQuestion | None) -> None:
    """Wired into `WebQuestionPort(on_pending_changed=...)`. Keeps the
    registry's `RunStatus` accurate (W3) and, new this stage, publishes the
    matching SSE event (W4) — `awaiting_input` when a question is
    published, `resumed` right after it's answered."""
    session = registry.get(run_id)
    if pending is not None:
        registry.mark_awaiting_input(run_id)
        if session is not None:
            session.publish("awaiting_input", {"kind": pending.kind})
    else:
        registry.mark_running(run_id)
        if session is not None:
            session.publish("resumed", {"status": "running"})


def _on_progress(registry: RunRegistry, run_id: str, event: dict) -> None:
    """Wired into `WebQuestionPort(on_progress=...)`. `event` is already
    `_classify_progress`'s typed shape (`io/web_port.py`): `{"type":
    "phase_entered", "phase": ...}` or `{"type": "tick"|"phase_result",
    "text": ...}`. `phase_entered` sets `RunSession.current_phase` via
    `set_phase` (the one place that field is ever written); `tick`/
    `phase_result` publish just their `text` — `web/events.py` writes
    these three event kinds to the wire as a bare escaped string (so
    htmx's default `sse-swap` can drop them straight into the DOM), so the
    published payload must be that bare string, not the whole classified
    dict."""
    session = registry.get(run_id)
    if session is None:
        return
    if event["type"] == "phase_entered":
        session.set_phase(event["phase"])
    else:
        session.publish(event["type"], event["text"])


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, format: str | None = None):
    """Content-negotiated via `?format=json` (not the `Accept` header — a
    query param is simpler for both callers and tests to be explicit
    about): `?format=json` returns the JSON shape this route has always
    returned; anything else (a browser's default navigation) returns the
    full HTML Run page (web build order W5). Both branches read from
    `resolve_run_view`, which is also what `GET /runs/{id}/panel` uses —
    see that function's docstring for the three-source merge (W7) this
    route benefits from for free: a `run_id` that belongs to a completed
    run from a previous server process, or one orphaned by a restart,
    renders correctly here too, not just on the panel fragment."""
    view = resolve_run_view(request, run_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such run.")

    if format == "json":
        body: dict = {
            "run_id": run_id,
            "product_type": view.product_type,
            "status": view.status,
            "current_phase": view.current_phase,
        }
        if view.created_at is not None:
            body["created_at"] = view.created_at.isoformat()
        if view.pending is not None:
            body["pending_question"] = view.pending.model_dump(mode="json")
        if view.error_message is not None:
            body["error_message"] = view.error_message
        if view.interrupted_last_phase is not None:
            body["last_completed_phase"] = view.interrupted_last_phase
        return JSONResponse(body)

    panel_html = render_action_panel(
        run_id=run_id,
        status=view.status,
        pending=view.pending,
        error_message=view.error_message,
        interrupted_last_phase=view.interrupted_last_phase,
    )
    html = render_run_page(
        run_id=run_id,
        product_type=view.product_type or run_id,
        current_phase=view.current_phase,
        panel_html=panel_html,
    )
    return HTMLResponse(html)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    session = _registry(request).get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such run.")
    return StreamingResponse(event_stream(session), media_type="text/event-stream")


@router.post("/runs/{run_id}/answer")
async def answer_run(run_id: str, request: Request) -> JSONResponse:
    session = _registry(request).get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such run.")
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 - malformed body, not a server error
        raise HTTPException(status_code=422, detail="Request body must be JSON.") from exc
    try:
        session.port.submit_answer(raw)
    except WebPortValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse({"status": "accepted"})


@router.post("/runs/{run_id}/bailout")
async def bailout_run(run_id: str, request: Request) -> JSONResponse:
    session = _registry(request).get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such run.")
    try:
        session.port.submit_bailout()
    except WebPortValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse({"status": "accepted"})


@router.post("/runs/{run_id}/abandon")
async def abandon_run(run_id: str, request: Request) -> JSONResponse:
    registry = _registry(request)
    try:
        registry.abandon(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No such run.") from exc
    session = registry.get(run_id)
    if session is not None:
        session.publish("terminal", {"status": session.status.value})
    return JSONResponse({"status": "abandoned"})


__all__ = ["router", "PipelineRunner"]
