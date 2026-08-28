"""Unit tests for degraded_modes.py — §8.4 commodity-category classification
(build order step 12)."""

import pytest

from product_scout.degraded_modes import COMMODITY_CATALOG_FLOOR, is_commodity_category
from tests.conftest import make_survey_report


def _report(**overrides):
    defaults = dict(differentiation="low", estimated_product_count=COMMODITY_CATALOG_FLOOR)
    defaults.update(overrides)
    return make_survey_report(**defaults)


def test_low_differentiation_large_catalog_is_commodity():
    assert is_commodity_category(_report()) is True


def test_low_differentiation_small_catalog_is_not_commodity():
    """Both conjuncts required — §8.4 names 'against a large catalog'
    explicitly, not differentiation alone."""
    report = _report(estimated_product_count=COMMODITY_CATALOG_FLOOR - 1)
    assert is_commodity_category(report) is False


@pytest.mark.parametrize("differentiation", ["moderate", "high"])
def test_higher_differentiation_large_catalog_is_not_commodity(differentiation):
    report = _report(differentiation=differentiation)
    assert is_commodity_category(report) is False


def test_floor_is_inclusive():
    at_floor = _report(estimated_product_count=COMMODITY_CATALOG_FLOOR)
    below_floor = _report(estimated_product_count=COMMODITY_CATALOG_FLOOR - 1)
    assert is_commodity_category(at_floor) is True
    assert is_commodity_category(below_floor) is False
