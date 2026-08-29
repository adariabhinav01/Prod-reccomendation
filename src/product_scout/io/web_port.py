"""HTTP implementation of QuestionPort — stub (spec docs/handoff.md §0:
"Terminal CLI now, web later"; §2: project layout lists this as a stub).

Not implemented. Exists so the seam is visible in the tree, and so whoever
builds the web interface has the exact contract to satisfy: `io/port.py`'s
`QuestionPort` Protocol, unchanged. Per §3.4: "Building web_port.py later
means implementing QuestionPort against HTTP and swapping the injection. No
phase code changes."
"""

from __future__ import annotations

from product_scout.io.port import TopicPrompt
from product_scout.models import TopicAnswer

_NOT_IMPLEMENTED = (
    "WebQuestionPort is a stub (§0: 'terminal CLI now, web later'). "
    "Implement against HTTP when the web interface is built; QuestionPort's "
    "contract (io/port.py) does not change."
)


class WebQuestionPort:
    """Placeholder QuestionPort implementation for the future web interface.

    Likely shape when built: publish the pending question (`ask_topic`'s
    composed gate/axis/free-text form, in particular — §3.4 notes this is
    exactly the case the composition exists for) to a per-session endpoint,
    then await the matching answer submitted by the browser client. That
    design is deferred, not decided, per §0's "web later."
    """

    async def ask_choice(self, question: str, options: list[str], escape_hatch: str) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def ask_topic(self, topic: TopicPrompt) -> TopicAnswer:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def offer_bailout(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def ask_text(self, prompt: str) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def report_progress(self, message: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)
