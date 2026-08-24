"""Terminal implementation of QuestionPort (spec docs/handoff.md §3, build
order step 2).

Renders `question` and a numbered menu of `options` (plus the escape hatch,
per `io/port.py`), reads a line from stdin, and loops on unrecognized input
rather than guessing. `input_fn`/`print_fn` are injectable so tests drive
this without a real terminal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from product_scout.io.port import ESCAPE_HATCH_LABELS, EscapeHatch

_VALID_HATCHES = frozenset(("not_sure", "no_preference", "none"))


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

    async def ask(
        self,
        question: str,
        options: list[str],
        escape_hatch: EscapeHatch = "not_sure",
    ) -> str:
        # §3's ask_user tool schema: 2-4 options. Enforced here too, since
        # phase code (and eventually the ask_user tool) both route through
        # this port and a malformed question should fail loudly, not render
        # a broken menu.
        if not 2 <= len(options) <= 4:
            raise ValueError(
                f"QuestionPort.ask requires 2-4 options (§3); got {len(options)}."
            )
        if escape_hatch not in _VALID_HATCHES:
            raise ValueError(
                f"escape_hatch must be one of {sorted(_VALID_HATCHES)}; "
                f"got {escape_hatch!r}."
            )

        menu = list(options)
        hatch_label = ESCAPE_HATCH_LABELS.get(escape_hatch)  # None for "none"
        if hatch_label is not None:
            menu.append(hatch_label)

        # input() is blocking; run it off the event loop thread so this
        # coroutine behaves like any other awaitable in the SDK's async loop.
        return await asyncio.to_thread(
            self._ask_sync, question, menu, escape_hatch, hatch_label
        )

    def _ask_sync(
        self,
        question: str,
        menu: list[str],
        escape_hatch: EscapeHatch,
        hatch_label: str | None,
    ) -> str:
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
            if hatch_label is not None and choice == hatch_label:
                # Return the sentinel ("not_sure"/"no_preference"), never
                # the display label — see io/port.py's contract.
                return escape_hatch
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
