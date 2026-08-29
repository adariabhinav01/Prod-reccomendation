"""QuestionPort protocol — the swappability seam (spec docs/handoff.md §3.4,
build order step 2).

"Terminal CLI now, web later — all interaction behind QuestionPort" (§0).
Phase code (REFINE, step 7) depends only on this Protocol; `cli_port.py` is
the terminal implementation built now, `web_port.py` the HTTP implementation
stubbed for later. Invariant 9: "All user interaction routes through
QuestionPort. No phase calls terminal input directly."

### Five primitives, five different jobs

`ask_choice` — a bare categorical question, N options plus one caller-supplied
escape hatch string. Used for the §9.6 two-attempt "not sure" loop.

`ask_topic` — the composed §9.1 two-stage question (gate, then optional axis
+ always-present free text), taking a `TopicPrompt` and returning one
`TopicAnswer`. This is REFINE's main loop primitive.

`offer_bailout` — "skip the rest and use your best judgment" (§9.7a).

`ask_text` — a bare free-text prompt, no escape hatch, no gate/axis
structure. §9 opens with "Read by Opus in Phase 2," and §9.8 is the one
sentence distinguishing Phase 0 from that machinery: "Phase 0 only catches
requirements the user knows to name." §1 confirms Phase 0 is "Python ──
fixed questions, no model calls." None of the other three primitives fit:
`ask_choice` forces a real escape-hatch menu item Phase 0's plain questions
don't have (§9.6's escape hatches are REFINE-specific), and `ask_topic`
forces the full gate+axis+free-text composite onto a phase that runs before
any `Dimension`/`SurveyReport` exists to gate or axis against. `ask_text`
exists so `phases/intake.py` (build order step 4) has a primitive to ask
plain questions on, doing its own deterministic parsing (yes/no, budget
regex, CSV splitting) in Python — matching "no model calls" exactly.

`report_progress` — one-way status text, no answer expected (§16.2: "the
long research phases emit per-phase progress"; build order step 15's
`scout research` wiring). Kept on this Protocol rather than a bare
`print()` in `orchestrator.py` for the same reason every other method is
here: a future `WebQuestionPort` needs to push this as an SSE/websocket
event, not a terminal write, and invariant 9's "no phase calls terminal
I/O directly" applies to status output for the identical reason it applies
to interaction — the swap has to stay free.

### The two-attempt escape-hatch swap lives in the CALLER, not the port (§9.6)

`ask_choice`'s `escape_hatch` parameter is a raw string, not an enum with
built-in attempt tracking. The port is stateless per call: given the same
`question`/`options` twice with two different `escape_hatch` strings, it
renders two independent menus and returns whichever string (from `options`
or the literal `escape_hatch` argument) was picked each time. §9.6's
mechanics —

    attempt 1: escape_hatch = "Not sure — explain what this changes"
    (if picked) Opus explains, grounded in the actual candidates
    attempt 2: escape_hatch = "No preference — pick a sensible default"
    ("Not sure" is never offered a second time)

— are REFINE's (phases/refine.py, step 7) responsibility: call `ask_choice`
once, inspect whether the returned string equals the hatch it passed in, and
if so call Opus for the grounded explanation before calling `ask_choice`
again with the swapped hatch. The port never counts attempts; "the bound
lives in the UI [i.e. which hatch is offered], not a counter the model could
argue around" (§9.6). `ESCAPE_HATCH_LABELS` below is quoted verbatim from
§9.6's two exact hatch strings, so callers building the two-attempt loop
don't have to re-type them (and risk a caller/port copy mismatch that would
break the `answer == escape_hatch` comparison callers rely on).

### ask_topic's gate labels are the port's, not the caller's

Unlike `gate_question`/`gate_description` (topic-specific, authored by Opus
in `TopicPrompt`), the four `GateAnswer` values are a fixed vocabulary (§9.1)
— the same four choices for every topic. `GATE_ANSWER_LABELS` below is this
module's own display copy for them; §9 never quotes exact wording for the
gate menu the way it does for the two escape hatches, so this text is a
reasonable choice, not a spec transcription — flagged here rather than
presented as verbatim spec text.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from product_scout.models import GateAnswer, TopicAnswer

# §9.6, quoted verbatim — the only two hatches ask_choice's §9.6 loop uses.
# ask_choice itself accepts any string as escape_hatch (a bare gate question
# in Phase 0, "none"-style callers, etc. may use different copy entirely) —
# these two constants exist so REFINE's two-attempt loop (and this port's
# own tests) share one spelling instead of each re-typing it.
ESCAPE_HATCH_NOT_SURE: str = "Not sure — explain what this changes"
ESCAPE_HATCH_NO_PREFERENCE: str = "No preference — pick a sensible default"

# §9.1's fixed gate vocabulary — see module docstring on why this text lives
# here rather than being spec-quoted.
GATE_ANSWER_LABELS: dict[GateAnswer, str] = {
    "must_have": "Must-have — exclude anything without it",
    "must_avoid": "Must-avoid — exclude anything with it",
    "persuadable": "Leaning one way, but open to a strong option changing my mind",
    "no_preference": "No preference either way",
}

# §9.6 — asymmetric skip defaults. Position: genuinely balanced. Importance:
# low weight, never invented from silence. See confidence.py's module-level
# docstring convention: constants that encode a spec-mandated number get a
# named home rather than being inlined, so a future reader can find *why*.
POSITION_SKIP_DEFAULT: float = 0.5
IMPORTANCE_SKIP_DEFAULT: float = 0.2


class AxisSpec(BaseModel):
    kind: Literal["position", "importance"]  # from Dimension.axis_kind; §9.4
    low_label: str  # position: the opposing pole; importance: usually "doesn't matter"
    high_label: str  # position: the other pole; importance: the dimension itself
    why_this_matters: str  # one sentence, grounded in clusters


class TopicPrompt(BaseModel):
    topic: str
    dimension_name: str | None  # None = free-text-only; §9.7
    gate_question: str
    gate_description: str  # what "must have"/"must avoid" mean here
    axis: AxisSpec | None  # None when no coherent axis exists
    free_text_prompt: str  # ALWAYS present; §9.3


@runtime_checkable
class QuestionPort(Protocol):
    """§3.4's exact interface.

    Implementations must never call terminal/HTTP I/O outside this seam —
    invariant 9. See the module docstring for what each method owns.
    """

    async def ask_choice(
        self, question: str, options: list[str], escape_hatch: str
    ) -> str: ...

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer: ...

    async def offer_bailout(self) -> bool: ...

    async def ask_text(self, prompt: str) -> str: ...

    async def report_progress(self, message: str) -> None: ...
