"""SSE stream tests (web build order W4).

Two layers, deliberately split:

1. Direct `event_stream()` generator tests — no HTTP at all, `RunSession`
   built and driven straight from this file, matching the codebase's own
   convention of testing hook-shaped plumbing directly (`hooks/progress.py`
   and `hooks/budget.py`'s own tests call the hook coroutine with hand-built
   input, never through a live `query()`). This is also a hard *requirement*
   here, not just a style choice: neither `TestClient` nor
   `httpx.AsyncClient`+`ASGITransport` support genuinely concurrent access to
   a still-open ASGI stream from the same test process — both fully drive
   the app call to completion before returning a response/stream handle
   (confirmed empirically — see the plan). A test that needs to answer a
   pending question *while* a stream is still open (the `awaiting_input` ->
   `resumed` scenario) can only be written by driving `event_stream()`
   directly against `asyncio.create_task`s on one event loop, not through
   either HTTP test client.
2. HTTP-level tests via `TestClient`, for scenarios that don't need the test
   itself to interact mid-stream — a self-driving run's events (with small
   `asyncio.sleep`s in the fake pipeline so the stream genuinely opens
   while the run is still in flight, otherwise the run's own task races
   to completion before the second request even arrives), reconnect after
   completion, unknown run 404, and the truncated-status payload. These
   exercise `web/runs.py`'s real wiring end-to-end, not just the pure
   generator.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from product_scout.io.web_port import WebQuestionPort
from product_scout.store.runs import RunStore
from product_scout.web import events as events_module
from product_scout.web.app import create_app
from product_scout.web.events import event_stream
from product_scout.web.registry import RunSession, RunStatus
from tests.conftest import make_run_record
from tests.test_web_app import _wait_until


def run(coro):
    return asyncio.run(coro)


def make_session(run_id: str = "run-1", **overrides) -> RunSession:
    defaults = dict(
        run_id=run_id,
        product_type="standing desks",
        location_override=None,
        port=WebQuestionPort(),
    )
    defaults.update(overrides)
    return RunSession(**defaults)


# ============================================================================
# 1. Direct event_stream() generator tests — no HTTP.
# ============================================================================


def test_event_stream_yields_phase_entered_tick_and_terminal_in_order():
    session = make_session()

    async def scenario():
        stream = event_stream(session)

        first_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)  # let it subscribe and start awaiting the queue
        session.set_phase("SURVEY")
        first = await first_task

        second_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        session.publish("tick", "fetched https://example.com/product")
        second = await second_task

        third_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        session.status = RunStatus.DONE
        session.publish("terminal", {"status": "done"})
        third = await third_task

        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

        return first, second, third

    first, second, third = run(scenario())
    assert first == b"event: phase_entered\ndata: SURVEY\n\n"
    assert second == b"event: tick\ndata: fetched https://example.com/product\n\n"
    assert third == b'event: terminal\ndata: {"status": "done"}\n\n'


def test_event_stream_resync_yields_awaiting_input_immediately_on_connect():
    session = make_session()
    port = session.port

    async def scenario():
        ask_task = asyncio.ensure_future(port.ask_text("What model do you own?"))
        await asyncio.sleep(0)  # park the question
        stream = event_stream(session)
        first = await stream.__anext__()
        port.submit_answer({"answer": "Widget Pro"})
        await ask_task
        return first

    first = run(scenario())
    assert first == b'event: awaiting_input\ndata: {"kind": "text"}\n\n'


def test_event_stream_already_terminal_yields_once_and_closes():
    session = make_session(status=RunStatus.DONE)

    async def scenario():
        stream = event_stream(session)
        first = await stream.__anext__()
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        return first

    first = run(scenario())
    assert first == b'event: terminal\ndata: {"status": "done"}\n\n'


def test_event_stream_emits_keepalive_when_idle(monkeypatch):
    monkeypatch.setattr(events_module, "_KEEPALIVE_SECONDS", 0.01)
    session = make_session()

    async def scenario():
        stream = event_stream(session)
        return await stream.__anext__()

    first = run(scenario())
    assert first == b": keepalive\n\n"


def test_event_stream_two_subscribers_receive_the_same_event():
    session = make_session()

    async def scenario():
        stream_a = event_stream(session)
        stream_b = event_stream(session)
        task_a = asyncio.ensure_future(stream_a.__anext__())
        task_b = asyncio.ensure_future(stream_b.__anext__())
        await asyncio.sleep(0)  # let both subscribe
        session.set_phase("EXTRACTION")
        return await task_a, await task_b

    a, b = run(scenario())
    assert a == b == b"event: phase_entered\ndata: EXTRACTION\n\n"


def test_event_stream_unsubscribes_after_terminal():
    session = make_session()

    async def scenario():
        stream = event_stream(session)
        task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        assert len(session.subscribers) == 1
        session.status = RunStatus.DONE
        session.publish("terminal", {"status": "done"})
        await task
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert len(session.subscribers) == 0

    run(scenario())


def test_event_stream_unsubscribes_on_early_disconnect():
    """Simulates a client dropping mid-stream: the generator is suspended
    inside its `queue.get()` wait, then closed (as Starlette does when it
    detects a disconnect) rather than driven to a `terminal` event."""
    session = make_session()

    async def scenario():
        stream = event_stream(session)
        task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        assert len(session.subscribers) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
        assert len(session.subscribers) == 0

    run(scenario())


def test_event_stream_full_question_round_trip_via_real_port():
    """The realistic `awaiting_input` -> answer -> `resumed` -> `terminal`
    sequence, driven through an actual `WebQuestionPort` (not a hand-built
    session state) — this is the direct-generator equivalent of the
    HTTP-level scenario that can't be written against either test client
    (module docstring)."""
    session = make_session()
    port = session.port

    async def scenario():
        stream = event_stream(session)

        ask_task = asyncio.ensure_future(port.ask_text("What model do you own?"))
        await asyncio.sleep(0)
        first_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        first = await first_task  # resync: awaiting_input, since a question is already pending

        second_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        port.submit_answer({"answer": "Widget Pro"})
        # WebQuestionPort itself doesn't publish — that's web/runs.py's
        # `_on_pending_changed` closure's job in production. Here, publish
        # directly, matching what that closure does.
        session.publish("resumed", {"status": "running"})
        second = await second_task
        await ask_task

        third_task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        session.status = RunStatus.DONE
        session.publish("terminal", {"status": "done"})
        third = await third_task

        return first, second, third

    first, second, third = run(scenario())
    assert first == b'event: awaiting_input\ndata: {"kind": "text"}\n\n'
    assert second == b'event: resumed\ndata: {"status": "running"}\n\n'
    assert third == b'event: terminal\ndata: {"status": "done"}\n\n'


# ============================================================================
# 2. HTTP-level tests (TestClient) — self-driving flows only, no mid-stream
#    interaction required from the test.
# ============================================================================


async def _fake_pipeline_with_slow_ticks(product_type, port, run_store, *, run_id=None, **_kwargs):
    """Small real delays between ticks so a `client.stream()` call issued
    right after `POST /runs` genuinely catches the run still in flight —
    without these, a fake pipeline with no real awaits races to completion
    (on the TestClient's own background loop) before the second request
    even arrives, and the stream only ever sees the immediate terminal
    resync. See the module docstring."""
    await port.report_progress("SURVEY")
    await asyncio.sleep(0.05)
    await port.report_progress("  fetched https://example.com/product")
    await asyncio.sleep(0.05)
    await port.report_progress("  [ok] 4 turns, terminal_reason=completed")
    return make_run_record(run_id=run_id, product_type=product_type)


async def _fake_pipeline_no_questions(product_type, port, run_store, *, run_id=None, **_kwargs):
    return make_run_record(run_id=run_id, product_type=product_type)


async def _fake_pipeline_truncated(product_type, port, run_store, *, run_id=None, **_kwargs):
    return make_run_record(run_id=run_id, product_type=product_type, truncated_at_phase=3)


def make_client(tmp_path, pipeline_runner) -> TestClient:
    store = RunStore(root=tmp_path / ".product-scout")
    app = create_app(run_store=store, pipeline_runner=pipeline_runner, check_api_key=False)
    return TestClient(app)


def _start_run(client, product_type: str = "standing desks") -> str:
    response = client.post("/runs", data={"product_type": product_type}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _read_events(response, *, limit: int = 200) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event: str | None = None
    for i, line in enumerate(response.iter_lines()):
        if i >= limit:
            raise AssertionError(f"read {limit} lines without the stream closing")
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: ") and current_event is not None:
            events.append((current_event, line[len("data: ") :]))
            current_event = None
    return events


def test_sse_route_reflects_a_self_driving_run_end_to_end(tmp_path):
    with make_client(tmp_path, _fake_pipeline_with_slow_ticks) as client:
        run_id = _start_run(client)
        with client.stream("GET", f"/runs/{run_id}/events") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _read_events(response)

    names = [name for name, _ in events]
    assert names == ["phase_entered", "tick", "phase_result", "terminal"]
    assert events[0] == ("phase_entered", "SURVEY")
    assert events[1] == ("tick", "fetched https://example.com/product")
    assert json.loads(events[3][1]) == {"status": "done"}


def test_sse_unknown_run_id_is_404(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.get("/runs/does-not-exist/events")
        assert response.status_code == 404


def test_sse_reconnect_after_completion_gets_immediate_terminal_and_closes(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        run_id = _start_run(client)
        _wait_until(lambda: client.get(f"/runs/{run_id}?format=json").json()["status"] == "done")

        with client.stream("GET", f"/runs/{run_id}/events") as response:
            events = _read_events(response)

    assert [name for name, _ in events] == ["terminal"]


def test_sse_terminal_reports_truncated_status(tmp_path):
    with make_client(tmp_path, _fake_pipeline_truncated) as client:
        run_id = _start_run(client)
        with client.stream("GET", f"/runs/{run_id}/events") as response:
            events = _read_events(response)

    terminal = [data for name, data in events if name == "terminal"][0]
    assert json.loads(terminal) == {"status": "truncated"}
