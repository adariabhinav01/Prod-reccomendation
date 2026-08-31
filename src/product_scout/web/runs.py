"""Run lifecycle endpoints (spec `docs/web_handoff.md` §3/§5, web build
order W3): start a run, look up its live state, resolve its pending
question, offer/accept a bailout, and abandon it.

`GET /runs/{id}` here is a minimal placeholder — enough to prove the
registry lookup, the 404 path, and (once a run completes) that it's no
longer "live." Its full rendering (progress log, the four pending-question
form variants, SSE wiring) is W5's job, not this stage's — see the plan's
follow-up section.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from product_scout.io.web_port import PendingQuestion, WebPortValidationError, WebQuestionPort
from product_scout.models import RunRecord
from product_scout.settings import SettingsError
from product_scout.settings import load as load_settings
from product_scout.settings import save as save_settings
from product_scout.settings import set_value as set_settings_value
from product_scout.store.runs import RunStore, generate_run_id
from product_scout.web.registry import RunRegistry, RunSession, RunStatus

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

    def _on_pending_changed(pending: PendingQuestion | None) -> None:
        if pending is not None:
            registry.mark_awaiting_input(run_id)
        else:
            registry.mark_running(run_id)

    port = WebQuestionPort(on_pending_changed=_on_pending_changed)
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

    session.task = asyncio.create_task(_run())
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> JSONResponse:
    session = _registry(request).get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such run.")
    body: dict = {
        "run_id": session.run_id,
        "product_type": session.product_type,
        "status": session.status.value,
        "current_phase": session.current_phase,
        "created_at": session.created_at.isoformat(),
    }
    if session.status is RunStatus.AWAITING_INPUT and session.port.pending is not None:
        body["pending_question"] = session.port.pending.model_dump(mode="json")
    if session.status is RunStatus.ERROR:
        body["error_message"] = session.error_message
    return JSONResponse(body)


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
    return JSONResponse({"status": "abandoned"})


__all__ = ["router", "PipelineRunner"]
