"""Unit tests for phases/extraction.py — Phase 3 EXTRACTION (build order
step 5). `SdkExtractor` (the real SDK-calling adapter) is intentionally not
exercised here — see the module docstring; `record_product`'s own validation
(the step's explicit deliverable) is covered directly in
tests/test_record_product.py without needing any SDK call. Only
`run_extraction`'s pass-through/short-circuit logic (against a fake) is
tested here.
"""

import asyncio

from product_scout.models import Product
from product_scout.phases.extraction import Extractor, run_extraction
from tests.conftest import make_product


def run(coro):
    return asyncio.run(coro)


class FakeExtractor:
    """Extractor test double: returns a fixed product list, regardless of
    input, and records how many times it was actually called."""

    def __init__(self, products: list[Product]):
        self._products = products
        self.calls = 0

    async def extract(
        self, product_type: str, candidates: list[str]
    ) -> list[Product]:
        self.calls += 1
        return self._products


def test_fake_extractor_satisfies_extractor():
    assert isinstance(FakeExtractor([]), Extractor)


def test_run_extraction_passes_through():
    products = [make_product(name="Widget Pro")]
    extractor = FakeExtractor(products)
    result = run(run_extraction("widgets", ["Widget Pro"], extractor))
    assert result == products
    assert extractor.calls == 1


def test_empty_candidates_short_circuits_without_calling_extractor():
    extractor = FakeExtractor([make_product()])
    result = run(run_extraction("widgets", [], extractor))
    assert result == []
    assert extractor.calls == 0
