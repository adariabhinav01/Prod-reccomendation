"""Degraded modes: commodity-category classification (spec docs/handoff.md
§8.4, build order step 12).

`RunRecord.low_evidence_mode` is genuinely stateful — whether it's set
depends on the §8.2 broadening interrupt and what the user chose, which
`phases/survey.py`'s `run_survey` decides once and carries forward as an
explicit `SurveyOutcome` field, because nothing later could recompute it
from `SurveyReport` alone.

`commodity_category` is different: §8.4's own condition ("`differentiation
== 'low'` against a large catalog") is a pure function of two `SurveyReport`
fields that never change after SURVEY runs. There is nothing to carry
statefully — any phase that already has `survey: SurveyReport` in scope
(`phases/scoring.py`, `phases/synthesis.py` both do) can call
`is_commodity_category` directly and get the exact answer
`SurveyOutcome.commodity_category` would have stored, with no risk of the
two disagreeing (a single pure function cannot disagree with itself). This
mirrors how `confidence_band()` is recomputed at every render call site
from a stored `confidence` rather than persisted redundantly as its own
field — recomputing a pure function from already-available data is this
codebase's established pattern for this kind of derived flag, not a
deviation from "compute once, store."

This lives in its own module rather than inside `phases/survey.py` because
`scoring.py`/`synthesis.py` need to call it too, and no phase module in
this codebase imports from another phase module (each is deliberately
self-contained — see `phases/synthesis.py`'s own docstring on why phases
don't import from `render` either, for the identical reasoning). A shared,
non-phase module is this codebase's existing answer for that: `confidence.py`
for §4.1, `location.py` for §10, this one for §8.4.
"""

from __future__ import annotations

from product_scout.models import SurveyReport

# §8.4 gives no number for "large catalog" — a genuinely underspecified
# corner, resolved here rather than left to silently mean "any commodity-
# differentiated set." Anchored above §8.1's `rich`-coverage floor (>= 8
# products): a catalog merely well-covered isn't automatically "large" in
# the sense that manufacturing archetype distinctions among it would be
# dishonest — that argument gets stronger specifically once a catalog is
# bigger than what "well covered" already requires. Chosen independently of
# `phases/scoring.py`'s `ROW_CAP` (12) — that constant bounds table WIDTH,
# a rendering concern; this one classifies the CATALOG the run is drawing
# from, a different question that happens to want a similar order of
# magnitude. A tuning candidate once the golden set (§17.1) can judge it,
# same as §19.4's other calibration questions.
COMMODITY_CATALOG_FLOOR: int = 10


def is_commodity_category(report: SurveyReport) -> bool:
    """§8.4: "When `differentiation == 'low'` against a large catalog, the
    archetype-diversity requirement relaxes rather than manufacturing
    distinctions." Both conjuncts required — a small set of only six
    low-differentiation products doesn't need this relaxation; §5.1's
    ordinary constraint machinery can still ask a legitimate question of a
    set that size.

    Pure function of `SurveyReport` alone — see module docstring for why
    that's what makes it safe to call from more than one phase without a
    stateful `commodity_category` parameter threaded everywhere.
    """
    return report.differentiation == "low" and report.estimated_product_count >= COMMODITY_CATALOG_FLOOR
