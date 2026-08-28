"""Phase 2 — REFINE (spec docs/handoff.md §1/§9, build order step 7).

One Opus pass authors the secondary interview grounded in SURVEY's
`clusters`/`dimensions`; a pure-Python state machine then runs that
interview against the §9.7 stopping condition, the §9.7a bail-out, and the
rule that only `Dimension`-mapped topics can shrink `surviving_clusters`.
This module owns three things, mirroring `phases/survey.py`'s own shape
exactly:

1. `Refiner` — the seam through which the actual question-authoring
   happens (mirrors `phases/survey.py`'s `Surveyor`).
2. `_apply_gate` / `_established_lean` / `_is_downweight` — the runtime
   logic §9.7's stopping condition needs.
3. `run_refine` — the floor / stop-(a) / stop-(b) / bail-out / CAP state
   machine, built on top of the seam.

### Three scope calls made explicitly here (see also `docs/handoff.md` §9.5,
### §9.6, §9.8) — approved for this build step

**1. Every topic uses the `ask_topic` gate+axis+text composite.** §9.5's
"floor-then-indifference → multiple choice" pattern — presenting a
threshold-natured topic as a standalone `ask_choice` call with concrete
option text instead of the composite — is not wired here. Nothing below
assumes every topic must be a composite; a later step can extend
`RawTopicPrompt` with a topic-shape field without touching any locked
public schema.

**2. §9.6's two-attempt escape-hatch swap is not implemented here**, even
though `io/port.py`'s own module docstring attributes it to
"phases/refine.py, step 7." That loop is specifically for `ask_choice`-
shaped categorical questions per §9.6's own text ("the `ask_choice`
two-attempt loop is retained for categorical questions") — it has no
caller once (1) above is deferred. `ask_choice` stays fully available on
`QuestionPort` for whichever step adds that topic shape.

**3. §9.8's mid-run requirement discovery / re-clustering is not attempted
here.** "Filter first, re-cluster only on collapse" needs a real product
set to filter, which doesn't exist until EXTRACTION (build order step 8)
runs. `run_refine` only asks about topics `propose_topics` returns from the
survey's own dimension landscape.

### §3's illustrative `PHASES` sample and `tools/server.py`'s stale comment

§3's sample code lists `tools=["mcp__scout__ask_topic", "mcp__scout__ask_choice"]`
for the refine phase, and `tools/server.py`'s docstring calls wiring those
as SDK tools "Phase 6/7's refine loop." `SdkRefiner` below deliberately does
NOT do this — it calls `query()` with `allowed_tools=[]` and gets a single
JSON array of authored topics back, then a plain Python loop (`run_refine`)
calls `QuestionPort.ask_topic`/`offer_bailout` directly for the live
interview. Two reasons: (a) §9.7's stopping condition has to be "computable
... in Python" per its own text, which is a much weaker guarantee if the
model itself is the one deciding, mid-conversation, whether to keep calling
an `ask_topic` tool; (b) §9.7a's bail-out has to backfill *every remaining
topic* with a default `TopicAnswer` the moment it fires, which only works
cleanly if the full topic set is already known in Python before the
interview starts — an MCP-tool-mediated turn-by-turn interview wouldn't
have that. This mirrors `survey.py`'s own precedent of calling out where it
deliberately diverges from `discovery.py`/`extraction.py`'s pre-v7 patterns.

### The one real design gap: `RawTopicPrompt.satisfies_must_have`

No locked schema (`Dimension`, `TopicPrompt`, `TopicAnswer`, `AxisSpec`)
records *which* of a `Dimension.splits` position-label strings a
`must_have` answer keeps — without that link, `surviving_clusters` can't be
shrunk in pure Python at all. `RawTopicPrompt` fixes this exactly the way
`survey.py`'s `RawSurvey` bundles `SurveyReport` with `evidence_pool`: a
phase-private wrapper around the public `TopicPrompt` that never crosses
the `QuestionPort` boundary. `satisfies_must_have` is decided by the same
Opus call that authors `gate_question`/`gate_description` — it already has
to understand the dimension's actual split values to write coherent gate
copy about them, so this isn't extra judgment burden.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from pydantic import BaseModel, ValidationError

from product_scout import config
from product_scout.io.port import (
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
    QuestionPort,
    TopicPrompt,
)
from product_scout.models import Dimension, IntakeAnswers, SurveyReport, TopicAnswer
from product_scout.skills import assert_skill_loaded

# §9.7's CAP — a phase constant, not a config.py cost knob (mirrors
# survey.py's KEEP_SCOPE_OPTION/STOP_OPTION living here rather than there).
REFINE_TOPIC_CAP: int = 8

# §9.7: "A 'lean' means a prior axis answer at <=0.35 or >=0.65 that was
# explicitly given." Named constants per io/port.py's own convention for
# spec-mandated numbers.
LEAN_LOW: float = 0.35
LEAN_HIGH: float = 0.65


class RawTopicPrompt(BaseModel):
    """One `TopicPrompt` plus the phase-private link that makes §9.7's
    stopping condition computable without a further model call. See module
    docstring's "one real design gap" section."""

    topic: TopicPrompt  # crosses the QuestionPort boundary unchanged
    satisfies_must_have: list[str] = []
    # Dimension.splits position-label strings (verbatim) this topic's
    # "must have" sense keeps. Empty when topic.dimension_name is None, or
    # when no coherent subset exists — the topic then stays informational
    # for cluster-shrinking purposes even though became_filter may still
    # end up True on the resulting TopicAnswer (product-level filtering is
    # EXTRACTION's later concern, §9.8).


@runtime_checkable
class Refiner(Protocol):
    """Seam for Phase 2's actual question-authoring call."""

    async def propose_topics(
        self, survey: SurveyReport, intake: IntakeAnswers
    ) -> list[RawTopicPrompt]: ...


class RefineOutcome(BaseModel):
    """Phase 2's fully-resolved result. Mirrors `phases/survey.py`'s
    `SurveyOutcome`: `caveats` is raw strings, not `Caveat` objects —
    wrapping them into typed, tiered `Caveat`s for `RunRecord.caveats` is
    the (not-yet-built) orchestrator's job when it assembles the run
    record from every phase's output, exactly the same precedent
    `SurveyOutcome.caveats` already sets."""

    topics: list[TopicAnswer]
    surviving_clusters: set[str]
    caveats: list[str] = []


# ---------------------------------------------------------------------------
# §9.7's stopping-condition runtime logic — pure functions, no I/O.
# ---------------------------------------------------------------------------


def _dimension_for(
    raw: RawTopicPrompt, dim_by_name: dict[str, Dimension]
) -> Dimension | None:
    if raw.topic.dimension_name is None:
        return None
    return dim_by_name.get(raw.topic.dimension_name)


def _power_now(
    raw: RawTopicPrompt, dim_by_name: dict[str, Dimension], surviving: set[str]
) -> int:
    """Ranking key: `separating_power(surviving)` for a Dimension-mapped
    topic, 0 for a free-text-only one (or one whose dimension_name doesn't
    resolve) — so those sort last but are never dropped from `remaining`."""
    dim = _dimension_for(raw, dim_by_name)
    return dim.separating_power(surviving) if dim is not None else 0


def _any_separating_topic_remaining(
    remaining: list[RawTopicPrompt],
    dim_by_name: dict[str, Dimension],
    surviving: set[str],
) -> bool:
    """§9.7 condition (a): "no remaining Dimension has >=2 distinct values
    across surviving clusters." Read literally against *unasked* topics —
    a dimension already asked and answered doesn't need re-asking, so it
    shouldn't block termination just by continuing to have power on paper.
    """
    return any(_power_now(raw, dim_by_name, surviving) >= 2 for raw in remaining)


def _apply_gate(
    raw: RawTopicPrompt,
    dim_by_name: dict[str, Dimension],
    answer: TopicAnswer,
    surviving: set[str],
) -> tuple[set[str], bool]:
    """Returns (new_surviving, eliminated_something). Only Dimension-mapped
    topics can shrink `surviving_clusters` — a topic with `dimension_name`
    is None, or names a dimension not present in `dim_by_name`, never
    touches it, whatever `gate_answer` says.

    A cluster missing from `dim.splits` is never eliminated by either
    must_have or must_avoid — SURVEY gives no completeness guarantee on
    `splits`, and eliminating on absence of data would be exactly the kind
    of silent option-loss the rest of the spec goes out of its way to
    avoid (§8.1a, §5.5).

    If applying the filter would zero out `surviving` entirely, the
    elimination is refused for ranking/stopping purposes — §9.8's real
    halt-and-ask needs EXTRACTION's product set (not yet built, build
    order step 8) to mean anything here. The `TopicAnswer` itself still
    records `became_filter=True`, so EXTRACTION can act on it later.
    """
    if answer.gate_answer not in ("must_have", "must_avoid"):
        return surviving, False

    dim = _dimension_for(raw, dim_by_name)
    if dim is None:
        return surviving, False

    if answer.gate_answer == "must_have":
        candidate = {
            key
            for key in surviving
            if key not in dim.splits or dim.splits[key] in raw.satisfies_must_have
        }
    else:  # must_avoid
        candidate = {
            key
            for key in surviving
            if key not in dim.splits or dim.splits[key] not in raw.satisfies_must_have
        }

    if not candidate:
        return surviving, False
    return candidate, candidate != surviving


def _established_lean(answer: TopicAnswer) -> bool:
    """§9.7: "A 'lean' means a prior axis answer at <=0.35 or >=0.65 that
    was explicitly given. `axis_skipped == True` never establishes a lean
    — a logged default of 0.2 on a skipped importance axis is a
    non-answer, and reading it as progress would repeat the exact
    confusion (a) was written to fix." Checked directly here: without the
    `axis_skipped` guard, `IMPORTANCE_SKIP_DEFAULT = 0.2` would numerically
    satisfy `<= LEAN_LOW` even though nothing was actually answered.
    """
    if answer.axis_skipped or answer.axis_value is None:
        return False
    return answer.axis_value <= LEAN_LOW or answer.axis_value >= LEAN_HIGH


def _is_downweight(
    raw: RawTopicPrompt, answer: TopicAnswer, dim_by_name: dict[str, Dimension]
) -> bool:
    """§6.4: "no_preference ... down-weights the dimension." Read here as
    §9.7's "down-weighted a separating dimension" — a `no_preference`
    answer on a topic that IS Dimension-mapped. (This is the plan's one
    interpretive call where §9.7's prose doesn't fully pin down mechanics
    across topics each asked exactly once; kept deliberately simple,
    consistent with §9.7's own instruction not to over-engineer the
    backstop — "it will rarely fire ... do not assert it fires.")
    """
    return _dimension_for(raw, dim_by_name) is not None and answer.gate_answer == "no_preference"


def _default_topic_answer(raw: RawTopicPrompt) -> TopicAnswer:
    """§9.7a: every topic left unasked when the bail-out fires becomes a
    `TopicAnswer` with `gate_answer="no_preference"`, a skipped axis at
    the §9.6 default for its kind, and its own `assumption_logged` line —
    on top of (not instead of) the one class-collapsed caveat `run_refine`
    appends separately."""
    prompt = raw.topic
    axis = prompt.axis
    if axis is None:
        axis_kind, axis_value = None, None
    elif axis.kind == "position":
        axis_kind, axis_value = "position", POSITION_SKIP_DEFAULT
    else:
        axis_kind, axis_value = "importance", IMPORTANCE_SKIP_DEFAULT

    return TopicAnswer(
        topic=prompt.topic,
        dimension_name=prompt.dimension_name,
        gate_answer="no_preference",
        axis_kind=axis_kind,
        axis_value=axis_value,
        axis_skipped=True,
        free_text="",
        became_filter=False,
        assumption_logged=(
            "You asked me to use my judgment for the rest, so I assumed "
            f"no preference on {prompt.topic}."
        ),
    )


def _join_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _bailout_caveat(remaining: list[RawTopicPrompt]) -> str:
    # §9.7a, quoted almost verbatim: "One class-collapsed caveat covers the
    # set: 'You asked me to use your judgment for the rest; I assumed no
    # preference on X, Y, and Z.'"
    topics = _join_and([raw.topic.topic for raw in remaining])
    return (
        "You asked me to use your judgment for the rest; I assumed no "
        f"preference on {topics}."
    )


async def run_refine(
    survey: SurveyReport,
    intake: IntakeAnswers,
    refiner: Refiner,
    port: QuestionPort,
) -> RefineOutcome:
    """Run Phase 2 end to end: author topics (one Opus call), then run the
    §9.7 floor / stop-(a) / stop-(b) / §9.7a bail-out / CAP state machine
    in pure Python — no further model call.

    Ordering per iteration: CAP check, re-rank `remaining` by current
    `separating_power`, then (once the floor is met) check stop (a), stop
    (b), and finally offer the bail-out — in that order, so a naturally-
    concluded or backstopped interview never also gets asked "want me to
    use my judgment for the rest?" on its way out; there's nothing left to
    judge. Only the bail-out branch manufactures default `TopicAnswer`s and
    a caveat — natural conclusion and the CAP assume nothing on the user's
    behalf and log nothing, because nothing was assumed.
    """
    raw_topics = await refiner.propose_topics(survey, intake)
    dim_by_name = {d.name: d for d in survey.dimensions}
    all_keys = {c.key for c in survey.clusters}

    # §9.7: "The floor is min(2, ...) so a category with one real dimension
    # doesn't get a manufactured second question." Fixed once, at the
    # start, against the full (not-yet-narrowed) cluster set.
    initial_separating = [d for d in survey.dimensions if d.separating_power(all_keys) >= 2]
    floor = min(2, len(initial_separating))

    surviving = set(all_keys)
    remaining = list(raw_topics)
    answers: list[TopicAnswer] = []
    recent_progress: list[bool] = []  # last-two-answers "advanced" flags
    caveats: list[str] = []

    while remaining:
        if len(answers) >= REFINE_TOPIC_CAP:
            break  # §9.7's runaway backstop — no defaults, nothing assumed

        remaining.sort(key=lambda raw: _power_now(raw, dim_by_name, surviving), reverse=True)
        floor_met = len(answers) >= floor

        if floor_met:
            if not _any_separating_topic_remaining(remaining, dim_by_name, surviving):
                break  # (a) PRIMARY
            if len(recent_progress) == 2 and not any(recent_progress):
                break  # (b) BACKSTOP

            # §9.7a: offered alongside every topic, only once the floor's met.
            if await port.offer_bailout():
                caveats.append(_bailout_caveat(remaining))
                answers.extend(_default_topic_answer(raw) for raw in remaining)
                remaining = []
                break

        raw = remaining.pop(0)
        answer = await port.ask_topic(raw.topic)
        answers.append(answer)

        surviving, eliminated = _apply_gate(raw, dim_by_name, answer, surviving)
        advanced = (
            eliminated
            or _established_lean(answer)
            or _is_downweight(raw, answer, dim_by_name)
        )
        recent_progress = (recent_progress + [advanced])[-2:]

    return RefineOutcome(topics=answers, surviving_clusters=surviving, caveats=caveats)


# ---------------------------------------------------------------------------
# SdkRefiner — real Refiner. Not unit tested here (no ANTHROPIC_API_KEY in
# this suite, consistent with SdkSurveyor/SdkDiscoverer/SdkExtractor).
# ---------------------------------------------------------------------------

# Found live (build order step 15's golden-set capture): scout-refiner's raw
# JSON-array response is long enough (5-7 verbose topics, each with several
# paragraph-length free-text fields) that Opus occasionally corrupts the
# brace nesting partway through — not the same failure twice, so there's no
# single mechanical repair to write (unlike `_INVALID_APOSTROPHE_ESCAPE`
# below, which fixes one specific, deterministic mistake). A fresh,
# independent sample is a different stochastic draw, so retrying the
# unmodified query is the general fix — same "bounded retry, then fail
# loudly" shape as `phases/scoring.py`'s `SCORING_REPROMPT_MAX`, just
# without feedback text, since there's nothing content-specific to correct.
REFINE_JSON_RETRY_MAX: int = 2

_UNPARSED = object()

# Found live, running this phase for real (build order step 15's golden-set
# capture) — Opus sometimes backslash-escapes an apostrophe inside a JSON
# string ("don\'t"), a Python/JS string-literal habit. JSON strings are
# double-quoted, so an apostrophe never needs escaping in one, and `\'` isn't
# a legal JSON escape at all — a single stray one anywhere makes the whole
# array unparseable. Mechanical repair, not a re-prompt (same category as
# survey.py's `_repair_comparison_specs` — there's nothing to ask the model
# to reconsider). Unconditional and safe: text with no `\'` is unchanged.
_INVALID_APOSTROPHE_ESCAPE = re.compile(r"\\'")


def _try_json_loads(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return _UNPARSED


def _parse_topic_list_json(text: str) -> list | None:
    """Extract a JSON array from the model's final message. Same defensive
    shape as `survey.py`'s `_parse_survey_json` (whole-message JSON first,
    then the outermost bracket span), kept as a local, self-contained copy
    rather than a cross-phase import — matching this codebase's existing
    convention (see `phases/intake.py`'s module docstring)."""
    text = text.strip()
    if not text:
        return None
    text = _INVALID_APOSTROPHE_ESCAPE.sub("'", text)

    parsed = _try_json_loads(text)
    if parsed is _UNPARSED:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return None
        parsed = _try_json_loads(text[start : end + 1])

    if parsed is _UNPARSED or not isinstance(parsed, list):
        return None
    return parsed


# `TopicPrompt`'s own `topic: str` field shares a name with the wrapping
# `RawTopicPrompt.topic: TopicPrompt` field around it — found live (build
# order step 15's golden-set capture, reproduced on independent samples,
# including all 3 retries in one attempt, so this is a systematic
# misreading of the schema, not stochastic noise a retry alone fixes):
# Opus repeatedly flattens `TopicPrompt`'s six fields onto the
# `RawTopicPrompt` level instead of nesting them under "topic". Every field
# `RawTopicPrompt` actually needs is still present, just one level too
# shallow — a mechanical repair (same category as `_INVALID_APOSTROPHE_ESCAPE`
# and survey.py's `_repair_comparison_specs`), not a guess.
_TOPIC_PROMPT_FIELDS = (
    "topic", "dimension_name", "gate_question", "gate_description", "axis", "free_text_prompt",
)


def _repair_flattened_topic(item: dict) -> dict:
    """If `item["topic"]` is a bare string, `TopicPrompt`'s fields were
    flattened onto `item` — reconstruct the nested shape. A dict-valued
    "topic" (the correct shape) passes through unchanged."""
    if not isinstance(item.get("topic"), str):
        return item
    topic = {k: item.get(k) for k in _TOPIC_PROMPT_FIELDS}
    return {"topic": topic, "satisfies_must_have": item.get("satisfies_must_have", [])}


REFINE_PROMPT_TEMPLATE = """You are scout-refiner. SURVEY has already run \
for a buyer shopping in this category and found the clusters and \
dimensions below — do not re-research anything; reason only from what's \
here.

INTAKE (already answered — do not ask about these again):
- Budget note: {budget_note}
- Required features (already filters): {required_features}

CLUSTERS (cluster-level language only when you refer to these — indicative \
prices, never a named product with an exact figure; §8.1a/§9.6):
{clusters_json}

DIMENSIONS (the spec axes that actually separate the clusters above):
{dimensions_json}

For EACH dimension above, author one topic: a gate_question with a \
gate_description naming that dimension's actual values (not an \
abstraction), an axis (omit — set axis to null — when the dimension's \
axis_kind is null; never invent one), and a free_text_prompt (always \
present). Apply your question-design skill's construction checks before \
finalizing each one.

For each topic, also report satisfies_must_have: the exact splits value \
string(s), copied verbatim from the DIMENSIONS block above, that this \
topic's "must-have" sense keeps. Never invent a value not already present \
in that dimension's splits.

When you are done, your FINAL message must be, and contain nothing except, \
a single JSON array, one object per dimension, each matching this schema:
{schema}
No prose before or after it, no markdown code fence."""


class SdkRefiner:
    """Real `Refiner` — calls Opus directly via the SDK, one batch call for
    the whole phase (see module docstring: no per-topic/per-answer calls,
    and no `mcp__scout__ask_topic`/`ask_choice` tools — this call reasons
    over the already-gathered `SurveyReport`/`IntakeAnswers`, doing no
    fresh research and no live user interaction from inside the model's
    own turn; the live interaction happens afterward, in `run_refine`, via
    `QuestionPort` directly). Not unit tested (see above)."""

    def __init__(self, model: str = config.MODEL_OPUS) -> None:
        self._model = model

    async def propose_topics(
        self, survey: SurveyReport, intake: IntakeAnswers
    ) -> list[RawTopicPrompt]:
        # §3: fail loudly before spending anything if the question-design
        # skill isn't there to be loaded.
        assert_skill_loaded(config.QUESTION_DESIGN_SKILL)

        prompt = REFINE_PROMPT_TEMPLATE.format(
            budget_note=intake.budget_note or "no note given",
            required_features=", ".join(intake.required_features) or "none",
            clusters_json=json.dumps(
                [
                    {
                        "key": cluster.key,
                        "label": cluster.label,
                        "indicative_price_range": list(cluster.price_range_native),
                        "approximate_member_count": cluster.approximate_member_count,
                    }
                    for cluster in survey.clusters
                ]
            ),
            dimensions_json=json.dumps(
                [
                    {"name": d.name, "splits": d.splits, "axis_kind": d.axis_kind}
                    for d in survey.dimensions
                ]
            ),
            schema=json.dumps(RawTopicPrompt.model_json_schema()),
        )
        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=[],
            permission_mode="default",  # no phase writes files; §3.2
            setting_sources=["project"],
            skills=[config.QUESTION_DESIGN_SKILL],
        )

        final_text = ""
        parsed = None
        for _attempt in range(REFINE_JSON_RETRY_MAX + 1):  # first try + retries
            final_text = ""
            async for message in query(prompt=prompt, options=options):
                if not isinstance(message, AssistantMessage):
                    continue
                content = message.content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, TextBlock):
                        final_text = block.text  # keep overwriting; last wins

            parsed = _parse_topic_list_json(final_text)
            if parsed is None:
                continue
            parsed = [_repair_flattened_topic(item) for item in parsed]
            try:
                # Validated inside the retry loop, not after it: a
                # structurally valid JSON array that still doesn't match
                # RawTopicPrompt's shape even after the flattening repair
                # is just as much "the wrong response" as a parse failure,
                # and a fresh sample is equally the right fix for either.
                return [RawTopicPrompt(**item) for item in parsed]
            except ValidationError:
                parsed = None
                continue

        raise RuntimeError(
            "scout-refiner's final message did not contain a parseable, "
            f"schema-valid JSON array of topics after {REFINE_JSON_RETRY_MAX + 1} "
            f"attempt(s): {final_text!r}"
        )
