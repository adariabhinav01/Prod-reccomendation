"""HTTP implementation of QuestionPort (spec `docs/web_handoff.md` §1/§2,
web build order step W2).

`WebQuestionPort` is the web analog of `io/cli_port.py`'s `CLIQuestionPort` —
same `QuestionPort` contract (`io/port.py`), same five methods, same
`TopicPrompt`/`TopicAnswer`/`AxisSpec` types, no phase code changes. Where
`CLIQuestionPort` blocks on `input()` via `asyncio.to_thread`, this port
blocks on an `asyncio.Future` that a web endpoint resolves later —
`docs/web_handoff.md` §1's "the one hard problem": HTTP is request/response,
a research run is not, and the reconciliation is this future-based hold.
The orchestrator never learns it is running under a web server; it calls
`QuestionPort` exactly as `run_pipeline` always has.

### PendingQuestion — one shape for all four answerable methods

`ask_topic`/`ask_choice`/`offer_bailout`/`ask_text` each publish a
`PendingQuestion` (a `kind`-discriminated union) before awaiting the answer,
so a caller (a web endpoint rendering the run's current state, later a
`GET /runs/{id}`) has one shape to branch on rather than four unrelated
ones. `report_progress` does not publish a `PendingQuestion` — it has no
answer to wait for; see `_classify_progress` below.

### Validation lives in the port, not the endpoint (§2)

"The browser is untrusted input even when it is your own browser." Every
`submit_*` call normalizes and validates its raw dict before ever calling
`Future.set_result` — a malformed payload raises `WebPortValidationError`
(callers map this to HTTP 422) and leaves the future untouched, so the
question is effectively re-asked with nothing lost. The one deliberately
asymmetric case is the §2.1 axis skip: a raw range-input value always
carries *some* number, so `axis_skipped` must arrive as its own explicit
flag — see `_normalize_axis_answer`'s docstring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from product_scout.io.port import (
    GATE_ANSWER_LABELS,
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
    TopicPrompt,
)
from product_scout.models import GateAnswer, TopicAnswer

# Exact strings `orchestrator.py` passes to `report_progress` at each phase
# boundary (module docstring's phase table; `orchestrator.py` lines calling
# `port.report_progress("SURVEY")` etc.) — the only structured signal this
# port can classify without changing orchestrator.py or hooks/progress.py
# (§3.1: "subscribes to the same source rather than instrumenting a second
# time"; invariant 9: no phase code changes).
_PHASE_LABELS: frozenset[str] = frozenset(
    {
        "INTAKE",
        "SURVEY",
        "REFINE",
        "EXTRACTION",
        "TIMING",
        "PRIOR-GEN",
        "SCORING",
        "SYNTHESIS",
        "RENDER",
    }
)

# Web-layer-only guard, not a spec-mandated number — generous enough that
# no genuine free-text answer should ever hit it; exists so an oversized
# POST body is rejected loudly (422) rather than silently truncated, which
# could quietly drop meaningful user intent.
_MAX_FREE_TEXT_LENGTH: int = 2000


class WebPortValidationError(ValueError):
    """A raw POST body failed §2's server-side validation. Callers (a
    future web endpoint) map this to HTTP 422; the port's own future is
    left untouched either way — see module docstring."""


class PendingQuestion(BaseModel):
    """The one shape `ask_topic`/`ask_choice`/`offer_bailout`/`ask_text`
    each publish while parked. Exactly one payload group is populated,
    selected by `kind` — a caller renders one of four form variants from
    this without needing four separate types."""

    kind: Literal["topic", "choice", "bailout", "text"]

    # kind == "topic"
    topic: TopicPrompt | None = None

    # kind == "choice"
    question: str | None = None
    options: list[str] | None = None
    escape_hatch: str | None = None

    # kind == "text"
    prompt: str | None = None

    # kind == "bailout" carries no extra payload — the fixed
    # "skip the rest and use your best judgment?" question (§9.7a).


def _require_str(raw: dict, key: str, *, max_length: int | None = None) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise WebPortValidationError(f"{key!r} must be a string.")
    if max_length is not None and len(value) > max_length:
        raise WebPortValidationError(f"{key!r} exceeds the {max_length}-character limit.")
    return value


def _normalize_gate_answer(raw: dict) -> tuple[GateAnswer, str | None]:
    """§9.1's fixed 4-way vocabulary. A missing/absent gate answer is
    treated as a skip — same semantics and the same logged note as the
    CLI's own skip branch (`cli_port.py:_ask_gate_sync`) — never silently
    defaulted to something else."""
    value = raw.get("gate_answer")
    if value is None or value == "":
        return "persuadable", (
            "You skipped whether this is a requirement, so I treated it as "
            "persuadable rather than assuming either way."
        )
    if value not in GATE_ANSWER_LABELS:
        raise WebPortValidationError(
            f"gate_answer must be one of {sorted(GATE_ANSWER_LABELS)}; got {value!r}."
        )
    return value, None  # type: ignore[return-value]


def _normalize_axis_answer(
    raw: dict, topic: TopicPrompt
) -> tuple[str | None, float | None, bool, str | None]:
    """§2.1 — the mandatory skip-vs-explicit-value distinction.

    A bare `<input type=range>` always reports *some* number (browsers
    default it to the midpoint of min/max), so "the user didn't touch
    this" can never be inferred from the numeric value alone — the form
    must send a separate `axis_skipped` boolean, and this function trusts
    only that flag, never the presence/absence or the value of
    `axis_value`, to decide which branch applies. Getting this backwards
    is exactly the failure mode CLAUDE.md's spec calls out: it would
    silently convert every skipped IMPORTANCE axis from 0.2 to 0.5.

    When `topic.axis is None` (no coherent axis exists for this topic),
    the client's claim about axis fields is ignored entirely — the port
    decides from the `TopicPrompt` it itself issued, never from what the
    browser asserts about which topic it's answering.
    """
    axis = topic.axis
    if axis is None:
        return None, None, True, None

    skipped = bool(raw.get("axis_skipped", False))
    if skipped:
        if axis.kind == "position":
            default = POSITION_SKIP_DEFAULT
            note = f"You skipped {topic.topic}; treated it as genuinely balanced."
        else:
            default = IMPORTANCE_SKIP_DEFAULT
            note = (
                f"You didn't say how much {topic.topic} matters, so I weighted "
                "it lightly. If it's actually important to you, this may move "
                "other options up."
            )
        return axis.kind, default, True, note

    raw_value = raw.get("axis_value")
    try:
        value = float(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise WebPortValidationError(
            "axis_value must be a number from 0 to 10 when axis_skipped is false."
        ) from None
    if not 0.0 <= value <= 10.0:
        raise WebPortValidationError("axis_value must be between 0 and 10.")
    # CLI parity (`cli_port.py:_ask_axis_sync`): displayed 0-10, stored /10.
    return axis.kind, value / 10.0, False, None


def _combine_notes(*notes: str | None) -> str | None:
    present = [n for n in notes if n]
    if not present:
        return None
    return " ".join(present)


def _normalize_topic_answer(raw: dict, topic: TopicPrompt) -> TopicAnswer:
    gate_answer, gate_note = _normalize_gate_answer(raw)

    if gate_answer in ("must_have", "must_avoid"):
        # §9.1: becomes a FILTER, stage 2 (axis + free text) is never asked
        # — mirrors CLIQuestionPort's identical short-circuit exactly.
        return TopicAnswer(
            topic=topic.topic,
            dimension_name=topic.dimension_name,
            gate_answer=gate_answer,
            axis_kind=None,
            axis_value=None,
            axis_skipped=True,
            free_text="",
            became_filter=True,
            assumption_logged=gate_note,
        )

    axis_kind, axis_value, axis_skipped, axis_note = _normalize_axis_answer(raw, topic)
    free_text = _require_str(raw, "free_text", max_length=_MAX_FREE_TEXT_LENGTH) if "free_text" in raw else ""

    return TopicAnswer(
        topic=topic.topic,
        dimension_name=topic.dimension_name,
        gate_answer=gate_answer,
        axis_kind=axis_kind,
        axis_value=axis_value,
        axis_skipped=axis_skipped,
        free_text=free_text,
        became_filter=False,
        assumption_logged=_combine_notes(gate_note, axis_note),
    )


def _normalize_choice_answer(raw: dict, options: list[str], escape_hatch: str) -> str:
    value = _require_str(raw, "answer")
    menu = [*options, escape_hatch]
    if value not in menu:
        raise WebPortValidationError(
            f"answer must be one of the offered options; got {value!r}."
        )
    return value


def _normalize_bailout_answer(raw: dict) -> bool:
    value = raw.get("accept")
    if not isinstance(value, bool):
        raise WebPortValidationError("accept must be a boolean.")
    return value


class WebQuestionPort:
    """QuestionPort implementation backed by HTTP — one pending question
    at a time, held on an `asyncio.Future` (§1). One instance per active
    run; the run lifecycle (`web/registry.py`, W3) owns that scoping.

    `on_pending_changed`, if given, is called synchronously every time
    `pending` transitions — to the new `PendingQuestion` when one is
    published, and to `None` right after it's resolved. This is deliberately
    a plain callback, not a queue/event bus: it's the minimum W3 needs so
    `RunRegistry` can track `RUNNING` vs `AWAITING_INPUT` accurately without
    building the SSE fan-out that's W4's job. `report_progress` has no
    equivalent hook yet in this stage — see `progress_log`/`_classify_progress`.

    Validation happens in `submit_answer`/`submit_bailout`, BEFORE the
    future resolves — not in the `ask_*` coroutine after it wakes up. This
    matters: if a bad payload resolved the future and only failed once the
    parked `ask_*` call resumed, the exception would blow up mid-phase
    instead of leaving the question re-askable, which is exactly what §2
    forbids ("leaves the future unresolved — the question is simply
    re-asked"). So the future's result is always already the correctly
    normalized/typed answer (a `TopicAnswer`, a `str`, or a `bool`), never
    a raw dict the `ask_*` method still has to validate.
    """

    def __init__(
        self, *, on_pending_changed: Callable[[PendingQuestion | None], None] | None = None
    ) -> None:
        self._pending: PendingQuestion | None = None
        self._answer_future: asyncio.Future | None = None
        self._on_pending_changed = on_pending_changed
        self.progress_log: list[str] = []  # in-memory only in this stage; SSE is W4

    @property
    def pending(self) -> PendingQuestion | None:
        return self._pending

    async def _ask(self, pending: PendingQuestion):
        self._answer_future = asyncio.get_running_loop().create_future()
        self._pending = pending
        if self._on_pending_changed is not None:
            self._on_pending_changed(pending)
        result = await self._answer_future  # blocks until submit_answer()/submit_bailout()
        self._pending = None
        self._answer_future = None
        if self._on_pending_changed is not None:
            self._on_pending_changed(None)
        return result

    # -- QuestionPort's five methods ---------------------------------------

    async def ask_choice(self, question: str, options: list[str], escape_hatch: str) -> str:
        if len(options) < 2:
            raise ValueError(
                f"QuestionPort.ask_choice needs at least 2 options; got {len(options)}."
            )
        return await self._ask(
            PendingQuestion(
                kind="choice", question=question, options=options, escape_hatch=escape_hatch
            )
        )

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer:
        return await self._ask(PendingQuestion(kind="topic", topic=topic))

    async def offer_bailout(self) -> bool:
        return await self._ask(PendingQuestion(kind="bailout"))

    async def ask_text(self, prompt: str) -> str:
        return await self._ask(PendingQuestion(kind="text", prompt=prompt))

    async def report_progress(self, message: str) -> None:
        self.progress_log.append(message)

    # -- resolving a pending question, called by a web endpoint (W3) -------

    def submit_answer(self, raw: dict) -> None:
        """Validates `raw` against whichever question is currently pending
        (dispatched on `self._pending.kind`) and, only on success, resolves
        the future with the already-normalized/typed answer. Raises
        `WebPortValidationError` on either "nothing is pending" or a
        malformed payload — in both cases the future is left exactly as it
        was, so the caller (a web endpoint) maps this to 404/422 and the
        question is simply re-asked, per §2."""
        if self._pending is None or self._answer_future is None or self._answer_future.done():
            raise WebPortValidationError("No pending question to answer.")

        pending = self._pending
        if pending.kind == "topic":
            result: TopicAnswer | str | bool = _normalize_topic_answer(raw, pending.topic)
        elif pending.kind == "choice":
            result = _normalize_choice_answer(raw, pending.options, pending.escape_hatch)
        elif pending.kind == "bailout":
            result = _normalize_bailout_answer(raw)
        elif pending.kind == "text":
            result = _require_str(raw, "answer", max_length=_MAX_FREE_TEXT_LENGTH)
        else:  # pragma: no cover - exhaustive over PendingQuestion.kind's Literal
            raise AssertionError(f"Unreachable pending kind: {pending.kind!r}")

        self._answer_future.set_result(result)

    def submit_bailout(self) -> None:
        """`POST /runs/{id}/bailout` — resolves `offer_bailout()` with
        `True` specifically. Only valid while the pending question is
        `kind == "bailout"`; anything else is a caller error (the run
        isn't currently offering a bailout)."""
        if self._pending is None or self._pending.kind != "bailout":
            raise WebPortValidationError("No bailout offer is currently pending.")
        self.submit_answer({"accept": True})


def _classify_progress(message: str) -> dict:
    """Classifies one `report_progress` string into a typed event shape a
    future SSE stream (W4) can emit without orchestrator.py or
    hooks/progress.py ever being touched — see module docstring. Exposed
    at module scope (not a `WebQuestionPort` method) since it's a pure
    function of the message text alone.

    There is deliberately no live "cost cap about to trip" classification
    here: `orchestrator.py`'s §13.1 skip-caveat text (e.g. "EXTRACTION
    skipped — this run's §13 cost cap was already reached...") is appended
    to a phase's own caveat list, never passed through `report_progress` —
    confirmed by reading every `report_progress` call site in
    orchestrator.py. The only honest live cost-cap signal is the terminal
    `RunRecord.truncated_at_phase`, read once the run completes; this
    function does not pretend otherwise.
    """
    if message in _PHASE_LABELS:
        return {"type": "phase_entered", "phase": message}
    if message.startswith("  fetched ") or message.startswith("  searched:"):
        return {"type": "tick", "text": message.strip()}
    if message.startswith("  ["):
        return {"type": "phase_result", "text": message.strip()}
    return {"type": "tick", "text": message}


__all__ = [
    "PendingQuestion",
    "WebPortValidationError",
    "WebQuestionPort",
]
