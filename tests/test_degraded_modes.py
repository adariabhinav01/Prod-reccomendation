"""Unit tests for degraded_modes.py — §8.4 commodity-category classification
(build order step 12) and §13.1's unresearched-cluster diff (build order
step 13)."""

import pytest

from product_scout.degraded_modes import (
    COMMODITY_CATALOG_FLOOR,
    is_commodity_category,
    unresearched_cluster_labels,
)
from tests.conftest import make_cluster, make_product, make_survey_report


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


# -- unresearched_cluster_labels: §13.1 --------------------------------------


def test_no_unresearched_clusters_when_every_cluster_has_a_product():
    survey = make_survey_report(clusters=[make_cluster(key="mid-tier")])
    products = [make_product(cluster_key="mid-tier")]
    assert unresearched_cluster_labels(survey, products) == []


def test_names_clusters_with_no_matching_product():
    survey = make_survey_report(
        clusters=[
            make_cluster(key="mid-tier", label="Mid-tier"),
            make_cluster(key="budget", label="Budget"),
        ]
    )
    products = [make_product(cluster_key="mid-tier")]
    assert unresearched_cluster_labels(survey, products) == ["Budget"]


def test_every_cluster_unresearched_when_products_empty():
    survey = make_survey_report(
        clusters=[
            make_cluster(key="mid-tier", label="Mid-tier"),
            make_cluster(key="budget", label="Budget"),
        ]
    )
    assert unresearched_cluster_labels(survey, []) == ["Mid-tier", "Budget"]


def test_preserves_survey_cluster_order():
    survey = make_survey_report(
        clusters=[
            make_cluster(key="z", label="Z-tier"),
            make_cluster(key="a", label="A-tier"),
        ]
    )
    assert unresearched_cluster_labels(survey, []) == ["Z-tier", "A-tier"]
