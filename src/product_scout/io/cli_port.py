"""Terminal implementation of QuestionPort (spec docs/handoff.md §3.4, §9;
build order step 2).

`input_fn`/`print_fn` are injectable so tests drive this without a real
terminal — carried forward from the v3 implementation this replaces. Every
blocking `input()` call happens inside a `_..._sync` helper run via
`asyncio.to_thread`, so each port method stays a normal awaitable in the
SDK's async loop without blocking it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from product_scout.io.port import (
    GATE_ANSWER_LABELS,
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
    TopicPrompt,
)
from product_scout.models import GateAnswer, TopicAnswer

_GATE_ORDER: tuple[GateAnswer, ...] = (
    "must_have",
    "must_avoid",
    "persuadable",
    "no_preference",
)
_GATE_LABEL_TO_ANSWER: dict[str, GateAnswer] = {
    GATE_ANSWER_LABELS[k]: k for k in _GATE_ORDER
}

_SKIP_COMMANDS = frozenset(("", "skip"))


class CLIQuestionPort:
    """QuestionPort over stdin/stdout."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[..., None] = print,
    ) -> None:
        self._input = input_fn
        self._print = print_fn

    # -- ask_choice: bare categorical question + one escape hatch ----------
    #
    # §9.6: the port renders whatever `escape_hatch` string it's given and
    # is stateless across calls — see io/port.py's module docstring for why
    # the two-attempt loop itself is REFINE's job, not this method's.

    async def ask_choice(
        self, question: str, options: list[str], escape_hatch: str
    ) -> str:
        if len(options) < 2:
            raise ValueError(
                f"QuestionPort.ask_choice needs at least 2 options; got {len(options)}."
            )
        return await asyncio.to_thread(self._ask_choice_sync, question, options, escape_hatch)

    def _ask_choice_sync(self, question: str, options: list[str], escape_hatch: str) -> str:
        menu = [*options, escape_hatch]
        self._print()
        self._print(question)
        for i, opt in enumerate(menu, start=1):
            self._print(f"  {i}. {opt}")

        while True:
            raw = self._input("> ").strip()
            choice = self._resolve(raw, menu)
            if choice is None:
                self._print(f"Please enter a number from 1 to {len(menu)}.")
                continue
            return choice

    @staticmethod
    def _resolve(raw: str, menu: list[str]) -> str | None:
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(menu):
                return menu[idx - 1]
            return None
        for opt in menu:
            if raw.lower() == opt.lower():
                return opt
        return None

    # -- ask_topic: §9.1's two-stage gate + axis + free-text question ------

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer:
        return await asyncio.to_thread(self._ask_topic_sync, topic)

    def _ask_topic_sync(self, topic: TopicPrompt) -> TopicAnswer:
        gate_answer, gate_note = self._ask_gate_sync(topic)

        if gate_answer in ("must_have", "must_avoid"):
            # §9.1: "becomes a FILTER, topic ends" — stage 2 is never asked.
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

        axis_kind, axis_value, axis_skipped, axis_note = self._ask_axis_sync(topic)
        free_text = self._ask_free_text_sync(topic.free_text_prompt)

        assumption_logged = _combine_notes(gate_note, axis_note)
        return TopicAnswer(
            topic=topic.topic,
            dimension_name=topic.dimension_name,
            gate_answer=gate_answer,
            axis_kind=axis_kind,
            axis_value=axis_value,
            axis_skipped=axis_skipped,
            free_text=free_text,
            became_filter=False,
            assumption_logged=assumption_logged,
        )

    def _ask_gate_sync(self, topic: TopicPrompt) -> tuple[GateAnswer, str | None]:
        self._print()
        self._print(topic.gate_question)
        self._print(topic.gate_description)
        for i, key in enumerate(_GATE_ORDER, start=1):
            self._print(f"  {i}. {GATE_ANSWER_LABELS[key]}")
        self._print("  (press Enter to skip)")

        while True:
            raw = self._input("> ").strip()
            if raw.lower() in _SKIP_COMMANDS:
                # §9.6: skipped gate defaults to persuadable, logged.
                note = (
                    f"You skipped whether {topic.topic} is a requirement, "
                    "so I treated it as persuadable rather than assuming either way."
                )
                return "persuadable", note
            label = self._resolve(raw, [GATE_ANSWER_LABELS[k] for k in _GATE_ORDER])
            if label is None:
                self._print(f"Please enter a number from 1 to {len(_GATE_ORDER)}, or Enter to skip.")
                continue
            return _GATE_LABEL_TO_ANSWER[label], None

    def _ask_axis_sync(
        self, topic: TopicPrompt
    ) -> tuple[str | None, float | None, bool, str | None]:
        axis = topic.axis
        if axis is None:
            return None, None, True, None

        self._print()
        self._print(axis.why_this_matters)
        self._print(
            f"On a scale of 0-10, where 0 = {axis.low_label} and 10 = {axis.high_label}:"
        )
        self._print("  (press Enter to skip)")

        while True:
            raw = self._input("> ").strip()
            if raw.lower() in _SKIP_COMMANDS:
                # §9.6 — deliberately asymmetric: a skipped POSITION axis
                # means genuinely balanced (0.5); a skipped IMPORTANCE axis
                # means the user declined to assert the dimension matters,
                # so it's weighted lightly (0.2), never invented as neutral.
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
            try:
                value = float(raw)
            except ValueError:
                self._print("Please enter a number from 0 to 10, or Enter to skip.")
                continue
            if not 0.0 <= value <= 10.0:
                self._print("Please enter a number from 0 to 10, or Enter to skip.")
                continue
            return axis.kind, value / 10.0, False, None

    def _ask_free_text_sync(self, prompt: str) -> str:
        self._print()
        self._print(prompt)
        self._print("  (press Enter to skip)")
        return self._input("> ").strip()

    # -- offer_bailout: §9.7a -------------------------------------------

    async def offer_bailout(self) -> bool:
        return await asyncio.to_thread(self._offer_bailout_sync)

    def _offer_bailout_sync(self) -> bool:
        self._print()
        self._print("Skip the rest and use your best judgment for the remaining preferences?")
        raw = self._input("[y/N] > ").strip().lower()
        return raw in ("y", "yes")

    # -- ask_text: bare free-text prompt, no escape hatch (build order step 4) --

    async def ask_text(self, prompt: str) -> str:
        return await asyncio.to_thread(self._ask_text_sync, prompt)

    def _ask_text_sync(self, prompt: str) -> str:
        self._print()
        self._print(prompt)
        return self._input("> ").strip()

    # -- report_progress: §16.2 per-phase status, no answer expected -------

    async def report_progress(self, message: str) -> None:
        self._print()
        self._print(message)


def _combine_notes(*notes: str | None) -> str | None:
    present = [n for n in notes if n]
    if not present:
        return None
    return " ".join(present)
