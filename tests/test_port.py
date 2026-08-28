"""Schema-level tests for io/port.py's AxisSpec/TopicPrompt (spec §3.4)."""

from product_scout.io.port import (
    ESCAPE_HATCH_NOT_SURE,
    ESCAPE_HATCH_NO_PREFERENCE,
    GATE_ANSWER_LABELS,
    IMPORTANCE_SKIP_DEFAULT,
    POSITION_SKIP_DEFAULT,
)
from tests.conftest import make_axis_spec, make_topic_prompt


def test_axis_spec_accepts_position_and_importance_kinds():
    assert make_axis_spec(kind="position").kind == "position"
    assert make_axis_spec(kind="importance").kind == "importance"


def test_topic_prompt_axis_is_optional():
    tp = make_topic_prompt(axis=None)
    assert tp.axis is None


def test_escape_hatch_labels_match_spec_verbatim():
    # §9.6, quoted directly: "Not sure — explain what this changes" / then
    # re-asks with the hatch swapped to "No preference — pick a sensible
    # default." A wording drift here would silently break every
    # `answer == escape_hatch` comparison a caller makes.
    assert ESCAPE_HATCH_NOT_SURE == "Not sure — explain what this changes"
    assert ESCAPE_HATCH_NO_PREFERENCE == "No preference — pick a sensible default"


def test_gate_answer_labels_cover_all_four_gate_answers():
    assert set(GATE_ANSWER_LABELS.keys()) == {
        "must_have",
        "must_avoid",
        "persuadable",
        "no_preference",
    }


def test_skip_defaults_are_asymmetric_per_9_6():
    assert POSITION_SKIP_DEFAULT == 0.5
    assert IMPORTANCE_SKIP_DEFAULT == 0.2
    assert POSITION_SKIP_DEFAULT != IMPORTANCE_SKIP_DEFAULT
