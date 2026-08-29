"""Pure HTML-fragment unit tests (web build order W5) — no HTTP, no app,
just direct calls into `web/templates.py`, mirroring `test_web_registry.py`'s
"pure unit" discipline. These exist specifically to guard the §2.1
skip-vs-explicit-value UI and the bailout no-path wiring against a future
markup regression re-introducing the exact bugs those sections warn about.
"""

from __future__ import annotations

from product_scout.io.port import GATE_ANSWER_LABELS
from product_scout.io.web_port import PendingQuestion
from product_scout.store.index import IndexEntry
from product_scout.web.templates import (
    render_action_panel,
    render_history_page,
    render_run_page,
    render_start_page,
)
from tests.conftest import NOW, make_axis_spec, make_topic_prompt


# -- start page ---------------------------------------------------------------


def test_start_page_renders_the_form_when_no_run_is_active():
    html = render_start_page(active_run_id=None)
    assert '<form method="post" action="/runs">' in html
    assert 'name="product_type"' in html
    assert 'name="location"' in html


def test_start_page_shows_a_banner_when_a_run_is_active():
    html = render_start_page(active_run_id="run-123")
    assert "already active" in html
    assert '<a href="/runs/run-123">' in html
    assert '<form method="post" action="/runs">' not in html


# -- history page ---------------------------------------------------------------


def test_history_page_lists_entries():
    entries = [
        IndexEntry(
            run_id="run-1", category="standing desks", created_at=NOW,
            verdict="BUY", top_pick="Widget Pro", run_path="/tmp/run-1",
        ),
    ]
    html = render_history_page(entries)
    assert "standing desks" in html
    assert "Widget Pro" in html
    assert '<a href="/runs/run-1/report">report</a>' in html


def test_history_page_handles_no_top_pick():
    entries = [
        IndexEntry(
            run_id="run-1", category="standing desks", created_at=NOW,
            verdict="INSUFFICIENT_EVIDENCE", top_pick=None, run_path="/tmp/run-1",
        ),
    ]
    html = render_history_page(entries)
    assert "—" in html


def test_history_page_empty():
    html = render_history_page([])
    assert "No runs yet." in html


# -- run page -------------------------------------------------------------------


def test_run_page_wires_sse_attributes_on_body():
    html = render_run_page(
        run_id="run-1", product_type="standing desks", current_phase="SURVEY", panel_html="<p>x</p>"
    )
    assert 'hx-ext="sse"' in html
    assert 'sse-connect="/runs/run-1/events"' in html
    assert 'hx-get="/runs/run-1/panel"' in html
    assert "SURVEY" in html


def test_run_page_shows_starting_when_no_phase_yet():
    html = render_run_page(run_id="run-1", product_type="standing desks", current_phase=None, panel_html="")
    assert "starting" in html


# -- action panel: terminal statuses ---------------------------------------------


def test_action_panel_done_links_to_report():
    html = render_action_panel(run_id="run-1", status="done")
    assert "Done" in html
    assert '<a href="/runs/run-1/report">' in html


def test_action_panel_truncated_is_labeled_distinctly_from_done():
    html = render_action_panel(run_id="run-1", status="truncated")
    assert "Truncated" in html
    assert "cost cap" in html
    assert '<a href="/runs/run-1/report">' in html


def test_action_panel_error_shows_the_message():
    html = render_action_panel(run_id="run-1", status="error", error_message="simulated failure")
    assert "simulated failure" in html


def test_action_panel_stopped_and_abandoned_have_no_report_link():
    for status in ("stopped", "abandoned"):
        html = render_action_panel(run_id="run-1", status=status)
        assert "/report" not in html


def test_action_panel_interrupted_shows_resume_command_and_no_form():
    html = render_action_panel(run_id="run-1", status="interrupted", interrupted_last_phase="survey")
    assert "scout research --resume run-1" in html
    assert "survey" in html
    assert "<form" not in html  # no in-browser resume affordance, per §5


def test_action_panel_running_shows_abandon_button():
    html = render_action_panel(run_id="run-1", status="running")
    assert "Working" in html
    assert 'hx-post="/runs/run-1/abandon"' in html


# -- action panel: the four pending-question form variants ----------------------


def test_text_form_renders_prompt_and_textarea():
    pq = PendingQuestion(kind="text", prompt="What model do you own?")
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert "What model do you own?" in html
    assert 'name="answer"' in html
    assert 'hx-post="/runs/run-1/answer"' in html


def test_choice_form_renders_every_option_plus_escape_hatch():
    pq = PendingQuestion(kind="choice", question="Which one?", options=["a", "b"], escape_hatch="not sure")
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert html.count('name="answer"') == 3  # a, b, escape hatch
    assert 'value="not sure"' in html


def test_bailout_form_no_path_posts_to_answer_not_bailout():
    """§3: `submit_bailout` only ever resolves True — the 'no' control must
    hit /answer directly with an explicit false, never /bailout."""
    pq = PendingQuestion(kind="bailout")
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert 'hx-post="/runs/run-1/bailout"' in html  # the "yes" form
    assert 'hx-post="/runs/run-1/answer"' in html  # the "no" form
    assert '"accept": false' in html


def test_gate_skip_radio_has_empty_value():
    """Matches `_normalize_gate_answer`'s explicit `""`-or-`None` skip
    branch in `io/web_port.py` exactly."""
    pq = PendingQuestion(kind="topic", topic=make_topic_prompt())
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert 'name="gate_answer" value="" checked' in html
    for key in GATE_ANSWER_LABELS:
        assert f'value="{key}"' in html


def test_axis_skip_checkbox_defaults_checked_and_range_defaults_disabled():
    """§2.1's mandated default: a user who never touches the fieldset must
    skip, not silently submit the range input's untouched midpoint."""
    pq = PendingQuestion(kind="topic", topic=make_topic_prompt(axis=make_axis_spec(kind="position")))
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert 'id="axis-skip" checked' in html
    assert 'id="axis-range"' in html and "disabled" in html
    assert 'name="axis_skipped" id="axis-skipped-mirror" value="true"' in html


def test_topic_form_with_no_axis_omits_the_fieldset():
    pq = PendingQuestion(kind="topic", topic=make_topic_prompt(axis=None))
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert "axis-fieldset" not in html
    assert 'name="axis_value"' not in html


def test_topic_form_always_has_free_text():
    pq = PendingQuestion(kind="topic", topic=make_topic_prompt())
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert 'name="free_text"' in html


# -- escaping ---------------------------------------------------------------------


def test_topic_prompt_containing_markup_is_escaped():
    malicious = "<script>alert(1)</script>"
    pq = PendingQuestion(kind="topic", topic=make_topic_prompt(gate_question=malicious, axis=None))
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_text_prompt_containing_markup_is_escaped():
    pq = PendingQuestion(kind="text", prompt="<img src=x onerror=alert(1)>")
    html = render_action_panel(run_id="run-1", status="awaiting_input", pending=pq)
    assert "<img src=x" not in html
