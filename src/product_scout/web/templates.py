"""Hand-rolled HTML for the app shell (spec `docs/web_handoff.md` §4, web
build order W5). Same style as `render/report.py`: plain f-strings,
`html.escape` on every interpolated value, no templating dependency.

Deliberately a different, plainer `<style>` block from `render/template.html`
— §4: "No design system, no component library. The report already carries
its own styling and is the only thing anyone looks at twice." This module
never needs the report's table/SVG/caveat-tiering rules.

Every function here takes plain primitives (strings, `PendingQuestion`,
`IndexEntry`), never a `RunSession` — keeps this module testable in
isolation (`tests/test_web_templates.py`) without constructing a
`WebQuestionPort`/registry, and keeps the "merge three sources of truth"
logic (web build order W7: live / completed-from-disk / orphaned) entirely
in the caller (`web/runs.py`/`web/views.py`), which is the only place that
actually knows which source a given run came from.

htmx (+ its SSE and `json-enc` extensions) is loaded from CDN — this app
isn't a Claude Artifact, so there's no CDN allowlist constraint, and the
pipeline itself already requires internet access for `WebFetch`/`WebSearch`.
If `scout serve` is ever expected to run fully offline, vendoring these into
`web/static/` + a `StaticFiles` mount is a follow-up, not attempted here.
"""

from __future__ import annotations

from html import escape as _esc

from product_scout.io.port import GATE_ANSWER_LABELS
from product_scout.io.web_port import PendingQuestion
from product_scout.store.index import IndexEntry

_HTMX_SCRIPTS = """\
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<script src="https://unpkg.com/htmx.org@2.0.4/dist/ext/sse.js"></script>
<script src="https://unpkg.com/htmx.org@2.0.4/dist/ext/json-enc.js"></script>
"""

_STYLE = """\
<style>
  :root {
    color-scheme: light dark;
    --fg: #111; --bg: #fff; --muted: #666; --border: #ddd;
    --card-bg: #f7f7f8; --done: #2f6f4f; --truncated: #b98a2f;
    --error: #b6432c; --link: #2a5db0;
  }
  body {
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    color: var(--fg); background: var(--bg);
    max-width: 720px; margin: 0 auto; padding: 24px 16px 64px; line-height: 1.5;
  }
  h1 { font-size: 1.4rem; margin-bottom: 0.3em; }
  a { color: var(--link); }
  .phase-badge {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    background: var(--card-bg); border: 1px solid var(--border);
    font-size: 0.85rem; margin: 0.4em 0 1em;
  }
  .progress-log {
    font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.82rem;
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; max-height: 260px; overflow-y: auto; white-space: pre-wrap;
  }
  .card {
    border: 1px solid var(--border); border-radius: 8px; padding: 16px;
    background: var(--card-bg); margin: 1em 0;
  }
  .status-done { color: var(--done); font-weight: 600; }
  .status-truncated { color: var(--truncated); font-weight: 600; }
  .status-error, .status-interrupted { color: var(--error); font-weight: 600; }
  fieldset { border: 1px solid var(--border); border-radius: 6px; margin: 0.8em 0; }
  label { display: block; margin: 0.4em 0; }
  textarea, input[type=text] { width: 100%; box-sizing: border-box; }
  button { padding: 6px 14px; margin-top: 0.6em; }
  .muted { color: var(--muted); font-size: 0.9em; }
  form + form { margin-top: 0.5em; }
  @media (prefers-color-scheme: dark) {
    :root {
      --fg: #eee; --bg: #16171a; --muted: #9aa0a6; --border: #33363b;
      --card-bg: #1f2023; --done: #5fae82; --truncated: #d6a44e;
      --error: #d97a63; --link: #7aa7e8;
    }
  }
</style>
"""


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f"{_STYLE}{_HTMX_SCRIPTS}"
        f"</head><body>{body}</body></html>"
    )


# -- Start (§4, GET /) -------------------------------------------------------


def render_start_page(*, active_run_id: str | None) -> str:
    if active_run_id is not None:
        body = (
            "<h1>Product Scout</h1>"
            '<div class="card">A run is already active. '
            f'<a href="/runs/{_esc(active_run_id)}">View it</a>.</div>'
        )
        return page("Product Scout", body)

    body = (
        "<h1>Product Scout</h1>"
        '<form method="post" action="/runs">'
        "<label>Product type<br>"
        '<input type="text" name="product_type" required autofocus></label>'
        "<label>Location override (ISO country code, optional)<br>"
        '<input type="text" name="location" placeholder="e.g. DE"></label>'
        "<button type=\"submit\">Start research</button>"
        "</form>"
    )
    return page("Product Scout", body)


# -- History (§4/W6, GET /history) -------------------------------------------


def render_history_page(entries: list[IndexEntry]) -> str:
    if not entries:
        body = "<h1>History</h1><p>No runs yet.</p>"
        return page("History — Product Scout", body)

    rows = []
    for entry in entries:
        top_pick = _esc(entry.top_pick) if entry.top_pick else "—"
        rows.append(
            "<li>"
            f"{entry.created_at:%Y-%m-%d %H:%M} — {_esc(entry.category)} — "
            f"{_esc(entry.verdict)} — top pick: {top_pick} — "
            f'<a href="/runs/{_esc(entry.run_id)}/report">report</a>'
            "</li>"
        )
    body = f"<h1>History</h1><ul>{''.join(rows)}</ul>"
    return page("History — Product Scout", body)


# -- Run page + action panel (GET /runs/{id}, GET /runs/{id}/panel) --------


def render_run_page(*, run_id: str, product_type: str, current_phase: str | None, panel_html: str) -> str:
    phase_label = _esc(current_phase) if current_phase else "starting…"
    body = (
        f'<body hx-ext="sse" sse-connect="/runs/{_esc(run_id)}/events">'
        f"<h1>{_esc(product_type)}</h1>"
        f'<div id="phase-badge" class="phase-badge" sse-swap="phase_entered" hx-swap="innerHTML">{phase_label}</div>'
        f'<div id="progress-log" class="progress-log" sse-swap="tick,phase_result" hx-swap="beforeend"></div>'
        f'<div id="action-panel" hx-get="/runs/{_esc(run_id)}/panel" '
        f'hx-trigger="sse:awaiting_input from:body, sse:resumed from:body, sse:terminal from:body">'
        f"{panel_html}"
        "</div>"
        "</body>"
    )
    # `page()` wraps its own <body> already — strip that wrapper here since
    # the SSE attributes need to live on <body> itself, not a nested div.
    return (
        "<!doctype html><html lang=\"en\"><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(product_type)} — Product Scout</title>"
        f"{_STYLE}{_HTMX_SCRIPTS}"
        f"</head>{body}</html>"
    )


_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "done": ("status-done", "Done"),
    "truncated": ("status-truncated", "Truncated — cost cap reached"),
    "stopped": ("muted", "Stopped — declined at SURVEY"),
    "error": ("status-error", "Failed"),
    "abandoned": ("muted", "Abandoned"),
    "interrupted": ("status-interrupted", "Interrupted"),
}


def render_action_panel(
    *,
    run_id: str,
    status: str,
    pending: PendingQuestion | None = None,
    error_message: str | None = None,
    interrupted_last_phase: str | None = None,
) -> str:
    """The one panel-rendering function `GET /runs/{id}` (first load) and
    `GET /runs/{id}/panel` (htmx re-fetch on SSE trigger) both call — see
    module docstring on why the three-source merge (live/completed/
    orphaned, W7) stays in the caller, not here."""
    if status == "awaiting_input" and pending is not None:
        return _render_pending_form(run_id, pending)
    if status == "running":
        return (
            "<p>Working…</p>"
            f'<button hx-post="/runs/{_esc(run_id)}/abandon" hx-confirm="Abandon this run?">Abandon</button>'
        )
    if status == "interrupted":
        phase = _esc(interrupted_last_phase) if interrupted_last_phase else "none"
        return (
            '<div class="card status-interrupted">'
            f"<p>This run was interrupted (last completed phase: <strong>{phase}</strong>).</p>"
            f"<p>Run <code>scout research --resume {_esc(run_id)}</code> from the terminal to continue it.</p>"
            "</div>"
        )
    if status == "error":
        message = _esc(error_message) if error_message else "unknown error"
        return f'<div class="card status-error"><p>Failed: {message}</p></div>'

    css_class, label = _STATUS_LABELS.get(status, ("", status))
    parts = [f'<div class="card"><p class="{css_class}">{_esc(label)}</p>']
    if status in ("done", "truncated"):
        parts.append(f'<p><a href="/runs/{_esc(run_id)}/report">View report</a></p>')
    parts.append("</div>")
    return "".join(parts)


def _render_pending_form(run_id: str, pending: PendingQuestion) -> str:
    if pending.kind == "text":
        return _render_text_form(run_id, pending)
    if pending.kind == "choice":
        return _render_choice_form(run_id, pending)
    if pending.kind == "bailout":
        return _render_bailout_form(run_id)
    if pending.kind == "topic":
        return _render_topic_form(run_id, pending)
    raise AssertionError(f"Unreachable pending kind: {pending.kind!r}")  # pragma: no cover


def _render_text_form(run_id: str, pending: PendingQuestion) -> str:
    return (
        f'<form hx-post="/runs/{_esc(run_id)}/answer" hx-ext="json-enc" hx-target="#action-panel">'
        f"<p>{_esc(pending.prompt or '')}</p>"
        '<textarea name="answer" maxlength="2000"></textarea>'
        "<button type=\"submit\">Submit</button>"
        "</form>"
    )


def _render_choice_form(run_id: str, pending: PendingQuestion) -> str:
    options = [*(pending.options or []), pending.escape_hatch or ""]
    radios = "".join(
        f'<label><input type="radio" name="answer" value="{_esc(opt)}" required> {_esc(opt)}</label>'
        for opt in options
    )
    return (
        f'<form hx-post="/runs/{_esc(run_id)}/answer" hx-ext="json-enc" hx-target="#action-panel">'
        f"<p>{_esc(pending.question or '')}</p>"
        f"{radios}"
        "<button type=\"submit\">Submit</button>"
        "</form>"
    )


def _render_bailout_form(run_id: str) -> str:
    return (
        "<p>Skip the rest and use your best judgment for the remaining preferences?</p>"
        f'<form hx-post="/runs/{_esc(run_id)}/bailout" hx-target="#action-panel" style="display:inline">'
        '<button type="submit">Yes, skip ahead</button>'
        "</form>"
        # §3's bailout endpoint only ever resolves True (`submit_bailout`
        # hardcodes {"accept": True}) — "No" has to go through the plain
        # answer endpoint directly with an explicit false, not a second
        # call to /bailout.
        f'<form hx-post="/runs/{_esc(run_id)}/answer" hx-ext="json-enc" hx-vals=\'{{"accept": false}}\' '
        'hx-target="#action-panel" style="display:inline">'
        '<button type="submit">No, keep asking</button>'
        "</form>"
    )


def _render_topic_form(run_id: str, pending: PendingQuestion) -> str:
    topic = pending.topic
    assert topic is not None  # kind == "topic" always carries one

    gate_radios = "".join(
        f'<label><input type="radio" name="gate_answer" value="{key}"> {_esc(label)}</label>'
        for key, label in GATE_ANSWER_LABELS.items()
    )
    gate_radios += '<label><input type="radio" name="gate_answer" value="" checked> Skip</label>'

    axis_html = ""
    if topic.axis is not None:
        axis = topic.axis
        axis_html = (
            '<fieldset id="axis-fieldset">'
            f"<legend>{_esc(axis.why_this_matters)}</legend>"
            f"<span>{_esc(axis.low_label)}</span> "
            '<input type="range" name="axis_value" id="axis-range" min="0" max="10" '
            'step="0.5" value="5" disabled '
            "oninput=\"document.getElementById('axis-skip').checked=false;"
            "document.getElementById('axis-skipped-mirror').value='false';\"> "
            f"<span>{_esc(axis.high_label)}</span>"
            '<label><input type="checkbox" id="axis-skip" checked '
            "onchange=\"document.getElementById('axis-range').disabled=this.checked;"
            "document.getElementById('axis-skipped-mirror').value=this.checked?'true':'false';\">"
            " No preference on this</label>"
            # `json-enc`'s checkbox serialization is not robust across
            # extension versions (unchecked -> omitted key, checked ->
            # `"on"`, neither a real boolean) — mirror into an explicit
            # hidden true/false input instead of depending on that.
            '<input type="hidden" name="axis_skipped" id="axis-skipped-mirror" value="true">'
            "</fieldset>"
        )

    return (
        f'<form hx-post="/runs/{_esc(run_id)}/answer" hx-ext="json-enc" hx-target="#action-panel">'
        f"<p>{_esc(topic.gate_question)}</p>"
        f'<p class="muted">{_esc(topic.gate_description)}</p>'
        f"{gate_radios}"
        f"{axis_html}"
        f'<label>{_esc(topic.free_text_prompt)}</label>'
        '<textarea name="free_text" maxlength="2000"></textarea>'
        "<button type=\"submit\">Submit</button>"
        "</form>"
    )
