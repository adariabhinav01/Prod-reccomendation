"""Unit tests for phases/extraction.py — Phase 3 EXTRACTION (build order
step 8). `SdkExtractor` (the real SDK-calling adapter) is intentionally not
exercised here — see the module docstring; `record_product`'s own
validation, including the three §4.3 cases this build step names, is
covered directly in tests/test_record_product.py and tests/test_ledger.py
without needing any SDK call. Only `run_extraction`'s pass-through/short-
circuit logic (against a fake) is tested here.
"""

import asyncio

from product_scout.hooks.ledger import FetchLedger
from product_scout.models import Product, SurveyReport
from product_scout.phases.extraction import Extractor, run_extraction
from tests.conftest import make_product, make_survey_report


def run(coro):
    return asyncio.run(coro)


class FakeExtractor:
    """Extractor test double: returns a fixed product list, regardless of
    input, and records every (product_type, candidates, survey, ledger)
    call it actually received."""

    def __init__(self, products: list[Product]):
        self._products = products
        self.calls: list[tuple[str, list[str], SurveyReport, FetchLedger]] = []

    async def extract(
        self,
        product_type: str,
        candidates: list[str],
        survey: SurveyReport,
        ledger: FetchLedger,
    ) -> list[Product]:
        self.calls.append((product_type, candidates, survey, ledger))
        return self._products


def test_fake_extractor_satisfies_extractor():
    assert isinstance(FakeExtractor([]), Extractor)


def test_run_extraction_passes_through():
    products = [make_product(name="Widget Pro")]
    extractor = FakeExtractor(products)
    survey = make_survey_report()
    ledger = FetchLedger()

    result = run(run_extraction("widgets", ["Widget Pro"], survey, ledger, extractor))

    assert result == products
    assert len(extractor.calls) == 1
    assert extractor.calls[0] == ("widgets", ["Widget Pro"], survey, ledger)


def test_empty_candidates_short_circuits_without_calling_extractor():
    extractor = FakeExtractor([make_product()])
    result = run(
        run_extraction("widgets", [], make_survey_report(), FetchLedger(), extractor)
    )
    assert result == []
    assert extractor.calls == []
