"""HTTP implementation of QuestionPort — stub (spec docs/handoff.md §0:
"Terminal CLI now, web later"; §3: project layout lists this as "stub,
build later").

Not implemented. Exists so the seam is visible in the tree, and so whoever
builds the web interface has the exact contract to satisfy: `io/port.py`'s
`QuestionPort` Protocol, unchanged. Per §3: "Building web_port.py later
means implementing QuestionPort against HTTP and swapping the injection.
No phase code changes."
"""

from __future__ import annotations

from product_scout.io.port import EscapeHatch


class WebQuestionPort:
    """Placeholder QuestionPort implementation for the future web interface.

    Likely shape when built: publish the pending question (with its
    escape-hatch label, per `io/port.py`) to a per-session endpoint, then
    await the matching answer submitted by the browser client — but that
    design is deferred, not decided, per §0's "web later."
    """

    async def ask(
        self,
        question: str,
        options: list[str],
        escape_hatch: EscapeHatch = "not_sure",
    ) -> str:
        raise NotImplementedError(
            "WebQuestionPort is a stub (§0: 'terminal CLI now, web later'). "
            "Implement against HTTP when the web interface is built; "
            "QuestionPort's contract (io/port.py) does not change."
        )

    async def ask_text(self, prompt: str) -> str:
        raise NotImplementedError(
            "WebQuestionPort is a stub (§0: 'terminal CLI now, web later'). "
            "Implement against HTTP when the web interface is built; "
            "QuestionPort's contract (io/port.py) does not change."
        )
