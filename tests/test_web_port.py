"""WebQuestionPort is a stub (spec docs/handoff.md §0, §3) — these tests
pin that it satisfies the QuestionPort shape and fails loudly rather than
silently, not that it does anything yet."""

import asyncio

import pytest

from product_scout.io.port import QuestionPort
from product_scout.io.web_port import WebQuestionPort


def test_web_port_satisfies_question_port():
    assert isinstance(WebQuestionPort(), QuestionPort)


def test_web_port_ask_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        asyncio.run(WebQuestionPort().ask("Q?", ["a", "b"]))
