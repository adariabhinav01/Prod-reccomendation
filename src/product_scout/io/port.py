"""QuestionPort protocol — the swappability seam (spec docs/handoff.md §3,
build order step 2).

"Terminal CLI now, web later — question-asking and rendering must sit
behind swappable interfaces" (§0). Phase code, and later the `ask_user`
SDK tool (§3), depend only on this Protocol. `cli_port.py` is the terminal
implementation built now; `web_port.py` is the HTTP implementation stubbed
for later. "Building web_port.py later means implementing QuestionPort
against HTTP and swapping the injection. No phase code changes." (§3)

### The two-attempt escape-hatch swap (§9)

The "not sure" loop is a state machine, but the *port* holds none of its
state — §9 is explicit that "the port owns which hatch is rendered, and no
phase code needs to track attempt state." Concretely:

- Attempt 1: phase code calls `ask(..., escape_hatch="not_sure")`. The port
  appends "Not sure — explain what this changes" as an extra choice.
- If the user picks it, phase code gets Opus to generate a
  candidate-grounded explanation (§9), then calls `ask()` again for the
  SAME question with `escape_hatch="no_preference"`. The port now appends
  "No preference — pick a sensible default for me" instead — "not sure" is
  never offered a second time.
- If the user picks that, phase code records `no_preference` and applies
  the safer default.

The loop is bounded by *which hatch value the caller passes*, not by a
counter either side maintains — the `escape_hatch` argument to `ask()` IS
the attempt state. Phase code (phases/refine.py, step 6) still has to make
two calls with two different `escape_hatch` values, but it never needs its
own `attempt = 1; attempt += 1` bookkeeping to know which hatch to offer.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

EscapeHatch = Literal["not_sure", "no_preference", "none"]

# Display label for each hatch, owned entirely by the port (never by phase
# code — see module docstring). Keyed only by the two hatches that actually
# render a choice; "none" renders nothing.
ESCAPE_HATCH_LABELS: dict[Literal["not_sure", "no_preference"], str] = {
    "not_sure": "Not sure — explain what this changes",
    "no_preference": "No preference — pick a sensible default for me",
}


@runtime_checkable
class QuestionPort(Protocol):
    """§3's exact interface.

    Implementations must:

    - Present `options` to the user (numbered menu, HTML form, etc.).
    - When `escape_hatch != "none"`, append
      `ESCAPE_HATCH_LABELS[escape_hatch]` as one more choice, on top of
      `options`.
    - Return the exact string from `options` the user picked, OR the
      literal `escape_hatch` value (`"not_sure"` / `"no_preference"`) if
      the user picked the appended hatch instead. Never return the hatch's
      display label — callers match on the sentinel value, not on text
      that's meant to change if the label copy changes.
    """

    async def ask(
        self,
        question: str,
        options: list[str],
        escape_hatch: EscapeHatch = "not_sure",
    ) -> str: ...
