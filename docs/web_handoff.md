# Product Scout — web port

The research pipeline is unchanged. Phases, models, prompts, skills, confidence
mathematics, the report renderer — none of it is touched. If anything here appears
to contradict `handoff.md`, `handoff.md` wins and this document has a bug.

This is a separate, independently-numbered build order (`W1`–`W7`) layered on top
of the pipeline `handoff.md` §17 already specifies. It does not gate on, and is not
gated by, that build order's own checklist in `CLAUDE.md`.

## 0. Decisions locked

| Decision | Choice |
|---|---|
| Deployment | Local, single user. Bound to `127.0.0.1`, no auth |
| Concurrency | One active run at a time, enforced |
| Report | Serve the existing self-contained HTML unchanged. No renderer changes |
| History | Bare list, newest first, linking to each report |
| Suspend model | In-memory hold — the run blocks on a future; checkpointing is the crash backstop, not the primary mechanism |
| CLI | Stays. The web port is additive; `cli_port.py` is not replaced |
| Stack | FastAPI + server-rendered HTML with htmx (§4) |

## 1. The one hard problem

HTTP is request/response. A research run is a long-lived process that pauses
mid-flight for user input during REFINE. Reconciling those is the whole difficulty
of this port; everything else is plumbing.

Because deployment is local and single-user, the run can simply live in server
memory and block. `WebPort.ask_topic()` publishes the question, then awaits an
`asyncio.Future` that the answer endpoint resolves. The orchestrator never learns
it is running under a web server — it calls `QuestionPort` exactly as the CLI does,
which is what the seam was built for.

A hosted multi-user version could not do this and would need every question to be
a genuine suspend-to-disk resume point. That is the main reason the deployment
decision gates everything else, and the main thing that would have to be rebuilt if
the deployment model ever changes. Note that constraint here so it is not
discovered later.

Checkpointing (`handoff.md` §16.1) remains the backstop: if the server process dies
while a run is parked, the run is recovered from its last completed phase with
`--resume`, exactly as the CLI does. The in-memory future is an optimization for
the common case, not the durability story.

## 2. WebPort

Implements `QuestionPort` (`handoff.md` §3.4) — same three methods, same types
(`TopicPrompt`, `TopicAnswer`, `AxisSpec`). No phase code changes.

```python
class WebPort:
    """QuestionPort implementation backed by HTTP. One active run."""

    def __init__(self):
        self._pending: PendingQuestion | None = None   # what the UI should show
        self._answer: asyncio.Future | None = None     # resolved by POST /answer

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer:
        self._answer = asyncio.get_running_loop().create_future()
        self._pending = PendingQuestion(kind="topic", payload=topic)
        await self._emit_progress("awaiting_input")
        raw = await self._answer                        # blocks until POST /answer
        self._pending = None
        return TopicAnswer.model_validate(raw)

    async def ask_choice(self, question, options, escape_hatch) -> str: ...
    async def offer_bailout(self) -> bool: ...
```

> **Implementation note (build order W2):** the actual `QuestionPort` Protocol has
> five methods, not three — the sketch above omits `ask_text` and
> `report_progress` for brevity. `ask_text` matters especially: Phase 0 INTAKE
> calls it before SURVEY ever runs, so a real implementation's pending-question
> mechanism has to handle a bare text prompt from the very first moment of any run,
> not just during REFINE. The shipped class is `WebQuestionPort`
> (`src/product_scout/io/web_port.py`), matching `CLIQuestionPort`'s naming, not
> `WebPort`.

Validation happens server-side, in the port. The browser is untrusted input even
when it is your own browser: an axis value must be a float in `[0, 1]`, a gate
answer must be one of the four `GateAnswer` literals, free text is bounded. A
malformed POST rejects with 422 and leaves the future unresolved — the question is
simply re-asked.

### 2.1 What the composition finally buys

`handoff.md` §3.4 notes that `ask_topic`'s composition of gate + axis + free text
is "a presentation improvement, not an interaction-count reduction" in the CLI,
because a terminal collects three inputs sequentially regardless.

This is where that changes. A single form renders all three fields at once: gate
as radio buttons, axis as a range input labeled with its poles, free text as a
textarea. One screen, one submit, one round trip. The claim §3.4 declined to make
for the CLI is true here.

The asymmetric skip defaults (`handoff.md` §9.6) must survive: leaving the axis
untouched is a skip, not a 0.5 answer. Track it explicitly — a range input has a
value whether or not the user moved it, so the form needs a separate "no
preference on this" affordance or an untouched-state flag. Getting this wrong
silently converts every skipped importance axis from 0.2 to 0.5, which is exactly
the failure §9.6 was written to prevent.

> **Implementation note (build order W2):** the CLI displays this axis on a 0–10
> scale and divides by 10 before storing (`cli_port.py:_ask_axis_sync`) — the web
> form mirrors that display convention rather than exposing a raw 0–1 slider, and
> validates/stores the same way. The skip flag is a separate boolean field
> (`axis_skipped`), never inferred from the numeric value, since a bare
> `<input type=range>` always reports *some* number (browsers default it to the
> midpoint).

## 3. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Start form: product type, optional `--location` override |
| POST | `/runs` | Start a run. 409 if one is active |
| GET | `/runs/{id}` | Live view: current phase, progress, pending question |
| GET | `/runs/{id}/events` | SSE stream: phase transitions, progress, question ready, done |
| POST | `/runs/{id}/answer` | Resolve the pending question's future |
| POST | `/runs/{id}/bailout` | Resolve `offer_bailout()` with `true` |
| POST | `/runs/{id}/abandon` | Cancel; checkpoint and release the active slot |
| GET | `/runs/{id}/report` | Serve `runs/<id>/report.html` verbatim |
| GET | `/history` | Bare list from `index.json`, newest first |

Not exposed to the web: `scout rescore`, `scout config`, `scout eval`. These stay
CLI-only — `eval` in particular is a developer tool, and `config` writes the same
file the server reads at startup. Revisit only if there is a real reason.

### 3.1 Progress and SSE

`handoff.md` §16.2 requires per-phase progress because a CLI that goes silent
after asking questions is the worst available experience. The same applies with
more force in a browser, where a spinner with no detail is indistinguishable from
a hang.

The SSE stream carries: phase entered, phase completed, per-fetch progress during
EXTRACTION, `awaiting_input`, cost-cap warnings, terminal states. The orchestrator
already emits these for the CLI; the web port subscribes to the same source rather
than instrumenting a second time.

> **Implementation note (build order W2/W4):** there is no live mid-phase
> cost-cap event in the pipeline today — `RunBudget.tripped`-triggered skip
> caveats are appended to a phase's own caveat list, never passed through
> `report_progress`. The only honest live signal remains
> `RunRecord.truncated_at_phase`, read once the run completes; `_classify_progress`
> in `io/web_port.py` documents this rather than pretending otherwise. SSE itself
> is deferred past this stage — see the build order below.

## 4. Stack and frontend

FastAPI, because the orchestrator is already async and the future-based hold in
§2 needs a real event loop. Flask would require threading gymnastics for no
benefit.

Server-rendered HTML with htmx. For four views and one form, a build step and a
client framework are pure overhead. htmx handles the SSE subscription and the form
post without a bundler. This keeps the whole port inside the existing Python
project.

Four views total:

- **Start** — product type, optional location override.
- **Run** — phase, progress log, and either the pending question form or a
  working indicator.
- **Report** — the static HTML, served as-is in a full-page frame or direct link.
- **History** — a list: date, category, verdict, top pick, link.

No design system, no component library. The report already carries its own
styling and is the only thing anyone looks at twice.

## 5. Lifecycle and edge cases

One active run, enforced. `POST /runs` returns 409 while one is in flight. This
avoids per-run cost-cap counters entirely — `handoff.md` §13's counter lives in
orchestrator state, and a single active run means a single counter. Concurrency
would require making that per-run, which is not worth it for a personal tool.

**Browser closed mid-question.** The run parks: the future stays unresolved, the
server holds the phase open, and reopening `/runs/{id}` shows the pending question
again. Parking is indefinite by default — a personal tool should not discard work
because you went to lunch.

**Server restarted while parked.** The in-memory future is gone. Recovery is
`scout research --resume <run_id>`, which restarts from the last completed phase
per §16.1. The web UI shows such runs as interrupted with the resume command,
rather than pretending they can be resumed in-browser.

**Cost cap trips mid-run.** `handoff.md` §13.1 defines this as termination, not a
skip: the run ships partial with `truncated_at_phase` set. The web UI surfaces the
truncation banner the report already renders — no new behavior, just make sure the
completion event distinguishes truncated from clean.

**API key missing.** Fail at server startup with a clear message, not at step 5 of
a run. The CLI already does this (`config.py`) — **correction (build order W1):**
it does not; `config.py`'s own docstring defers the check to "a future `cli.py`
entrypoint." `scout serve`/`create_app` is that entrypoint, and the check is
genuinely new code, not a refactor of an existing one.

## 6. Security

Local and single-user, but three rules that are cheap now and expensive to
retrofit:

- **Bind `127.0.0.1` only. Never `0.0.0.0`.** There is no auth, so binding
  publicly exposes an endpoint that spends money on API calls.
- **The API key never reaches the browser.** It is read server-side from env and
  used only inside the orchestrator. No endpoint returns it, no template renders
  it.
- **Treat POST bodies as untrusted.** Validate in `WebPort` per §2. "It's my own
  browser" stops being true the moment anything else on the machine can reach the
  port.

## 7. Build order

The port is additive and does not touch anything in `handoff.md` §17. Steps here
are numbered independently.

| Step | Content | Status |
|---|---|---|
| W1 | FastAPI app skeleton, startup config/key check, `127.0.0.1` binding, health route | done |
| W2 | `WebPort` implementing `QuestionPort` with the future-based hold and server-side validation. Unit-test the skip-vs-0.5 distinction (§2.1) before anything else | done |
| W3 | Run lifecycle: start, single-active enforcement, abandon, interrupted detection | done (interrupted detection deferred to W7) |
| W4 | SSE progress stream wired to the orchestrator's existing event source | not started |
| W5 | The four views in htmx | not started |
| W6 | History page over `index.json` | not started |
| W7 | Edge cases from §5 — park, restart, truncation, 409 | 409/park done in W3; restart/interrupted-detection and truncation-banner passthrough not started |

W1–W3 are testable with a fake `QuestionPort` and no model calls, same discipline
as `handoff.md` §17's first six steps. See `docs/NEXT_STEPS.md` for the session
that shipped W1–W3 and what's left before W4.

## 8. What this port must not do

Three temptations that would each undo a decision made deliberately upstream.

**Do not make the recommendation scale interactive with a price slider.** `score`
is an Opus judgment, not a function of price (`handoff.md` §5.2). A live slider
would require reducing it to a formula, which is the specific thing that section
forbids. Price sensitivity is already served by flip points, computed as bounded
interpolation between sampled re-judgments. If you want a discount answered, use
`scout rescore` — it re-runs phase 6a against stored evidence and costs one Opus
call.

**Do not fork the renderer.** The report is served verbatim. Any single-run
interactivity worth having (sorting the table, hovering a datapoint for
provenance) belongs inside the self-contained file, where it stays portable and
keeps one rendering path under the golden set's assertions.

**Do not let the web UI reach into phase code.** Everything goes through
`QuestionPort`. `handoff.md`'s invariant 9 exists for exactly this, and a web port
is where it is most tempting to violate — a "quick" direct call into REFINE to
peek at state is how the seam rots.

## 9. Open questions

Deferred until the port is running.

- Whether parking should ever time out. Indefinite is right for a personal tool;
  revisit if parked runs start accumulating.
- Whether `rescore` deserves a button on the report view. It is a natural web
  affordance, but it takes a price argument that is awkward to collect well, and
  the CLI form is unambiguous.
- Whether the history list needs filtering. Deliberately bare per §0. If it gets
  unusable, the fix is the `index.json` query, not a component rewrite.
