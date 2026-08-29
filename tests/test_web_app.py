"""Endpoint/lifecycle tests for the web port (web build order W1/W3), via
FastAPI's `TestClient`. Every scenario runs against a fake `run_pipeline`-
shaped coroutine (never a real model call) — same "testable with a fake
QuestionPort and no model calls" discipline as the pipeline's own first six
build steps. `check_api_key=False` is used throughout except in the
dedicated startup-check tests, so these tests never need a real
`ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from product_scout.cli import main as cli_main
from product_scout.store.runs import RunStore
from product_scout.web.app import MissingApiKeyError, assert_api_key_present, create_app
from tests.conftest import make_run_record


def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> None:
    """Polls `predicate()` until it's truthy or `timeout` elapses. The
    fake pipeline's task runs on the TestClient's own background event
    loop, scheduled but not necessarily executed by the time a `POST`
    call returns — this closes that timing gap without hardcoding a
    sleep duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


async def _fake_pipeline_one_text_question(product_type, port, run_store, *, run_id=None, **_kwargs):
    await port.ask_text("What model do you currently own?")
    return make_run_record(run_id=run_id, product_type=product_type)


async def _fake_pipeline_no_questions(product_type, port, run_store, *, run_id=None, **_kwargs):
    return make_run_record(run_id=run_id, product_type=product_type)


async def _fake_pipeline_truncated(product_type, port, run_store, *, run_id=None, **_kwargs):
    return make_run_record(run_id=run_id, product_type=product_type, truncated_at_phase=3)


async def _fake_pipeline_stopped(product_type, port, run_store, *, run_id=None, **_kwargs):
    return None  # §8.2 — user declined at SURVEY


async def _fake_pipeline_raises(product_type, port, run_store, *, run_id=None, **_kwargs):
    raise RuntimeError("simulated phase failure")


def make_client(tmp_path, pipeline_runner) -> TestClient:
    store = RunStore(root=tmp_path / ".product-scout")
    app = create_app(run_store=store, pipeline_runner=pipeline_runner, check_api_key=False)
    return TestClient(app)


# -- /health + startup key check --------------------------------------------


def test_health_ok_without_api_key_when_check_disabled(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_assert_api_key_present_raises_when_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        assert_api_key_present()


def test_assert_api_key_present_passes_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert_api_key_present() is None


def test_app_startup_fails_when_api_key_missing_and_check_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = RunStore(root=tmp_path / ".product-scout")
    app = create_app(run_store=store, pipeline_runner=_fake_pipeline_no_questions, check_api_key=True)
    with pytest.raises(Exception):  # starlette may wrap the original error
        with TestClient(app):
            pass


# -- scout serve (cli.py) ----------------------------------------------------


def test_serve_rejects_a_non_loopback_host(capsys):
    exit_code = cli_main(["serve", "--host", "0.0.0.0"])
    assert exit_code == 1
    assert "127.0.0.1" in capsys.readouterr().err


def test_serve_fails_fast_when_api_key_missing(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    exit_code = cli_main(["serve"])
    assert exit_code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


# -- run lifecycle ------------------------------------------------------------


def test_post_runs_redirects_to_the_new_run(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        assert response.status_code == 303
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        assert run_id


def test_run_becomes_awaiting_input_with_a_text_pending_question(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]

        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "awaiting_input")
        data = client.get(f"/runs/{run_id}").json()
        assert data["pending_question"]["kind"] == "text"
        assert data["product_type"] == "standing desks"


def test_answering_the_pending_question_lets_the_run_complete(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "awaiting_input")

        answer_response = client.post(f"/runs/{run_id}/answer", json={"answer": "Widget Pro"})
        assert answer_response.status_code == 200

        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] != "awaiting_input")
        data = client.get(f"/runs/{run_id}").json()
        assert data["status"] == "done"


def test_run_completes_immediately_when_the_pipeline_asks_nothing(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "done")


def test_run_status_is_truncated_when_the_record_says_so(tmp_path):
    with make_client(tmp_path, _fake_pipeline_truncated) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "truncated")


def test_run_status_is_stopped_when_the_user_declined_at_survey(tmp_path):
    with make_client(tmp_path, _fake_pipeline_stopped) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "stopped")


def test_run_status_is_error_when_the_pipeline_raises(tmp_path):
    with make_client(tmp_path, _fake_pipeline_raises) as client:
        response = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "error")
        data = client.get(f"/runs/{run_id}").json()
        assert "simulated phase failure" in data["error_message"]


def test_get_unknown_run_id_is_404(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.get("/runs/does-not-exist")
        assert response.status_code == 404


def test_second_post_runs_returns_409_while_one_is_active(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        first = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        assert first.status_code == 303
        second = client.post(
            "/runs", data={"product_type": "espresso machines"}, follow_redirects=False
        )
        assert second.status_code == 409


def test_abandon_releases_the_slot_so_a_new_run_can_start(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        first = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = first.headers["location"].rsplit("/", 1)[-1]

        abandon_response = client.post(f"/runs/{run_id}/abandon")
        assert abandon_response.status_code == 200
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "abandoned")

        second = client.post(
            "/runs", data={"product_type": "espresso machines"}, follow_redirects=False
        )
        assert second.status_code == 303


def test_abandon_unknown_run_id_is_404(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.post("/runs/does-not-exist/abandon")
        assert response.status_code == 404


def test_answer_unknown_run_id_is_404(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.post("/runs/does-not-exist/answer", json={"answer": "x"})
        assert response.status_code == 404


def test_answer_with_malformed_body_is_422_and_question_stays_answerable(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        first = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = first.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "awaiting_input")

        bad_response = client.post(f"/runs/{run_id}/answer", json={"answer": 12345})
        assert bad_response.status_code == 422
        # Still pending — a bad POST leaves the question re-askable (§2).
        assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_input"

        good_response = client.post(f"/runs/{run_id}/answer", json={"answer": "Widget Pro"})
        assert good_response.status_code == 200


def test_bailout_when_no_bailout_is_pending_is_409(tmp_path):
    with make_client(tmp_path, _fake_pipeline_one_text_question) as client:
        first = client.post(
            "/runs", data={"product_type": "standing desks"}, follow_redirects=False
        )
        run_id = first.headers["location"].rsplit("/", 1)[-1]
        _wait_until(lambda: client.get(f"/runs/{run_id}").json()["status"] == "awaiting_input")

        response = client.post(f"/runs/{run_id}/bailout")
        assert response.status_code == 409


def test_bailout_unknown_run_id_is_404(tmp_path):
    with make_client(tmp_path, _fake_pipeline_no_questions) as client:
        response = client.post("/runs/does-not-exist/bailout")
        assert response.status_code == 404
