"""Catalog search and offers against the real data: critical price and availability cases."""

from __future__ import annotations

import pytest

from emporio import catalog
from emporio.catalog import ToolInputError


def _ids(result):
    return {item["product_id"] for item in result["products"]}


def test_takamine_pix_ignores_inactive_promotion(conn):
    offer = catalog.calculate_offer(conn, 95, "pix")
    assert offer["price"] == "R$ 2.089,05"
    assert offer["list_price"] == "R$ 2.199,00"
    assert "promotion" not in offer


def test_takamine_inactive_promotion_is_not_exposed(conn):
    details = catalog.get_product_details(conn, 95)
    assert details["active_promotion"] is None
    assert details["price"] == "R$ 2.199,00"


def test_kalani_pix_does_not_stack_on_promotion(conn):
    offer = catalog.calculate_offer(conn, 127, "pix")
    assert offer["price"] == "R$ 323,10"
    assert offer["list_price"] == "R$ 359,00"
    assert offer["promotion"]["discount"] == "10%"
    assert any("não é cumulativo" in condition for condition in offer["conditions"])


def test_budget_search_uses_promotional_price(conn):
    result = catalog.search_products(conn, query="Ohana CK-20", max_price=500)
    ohana = result["products"][0]
    assert ohana["product_id"] == 121
    assert ohana["price"] == "R$ 439,20"
    assert ohana["list_price"] == "R$ 549,00"
    assert ohana["promotion"]["discount"] == "20%"


def test_pix_widens_budget_search_only_when_requested(conn):
    # The Yamaha C40 costs R$ 599,90, or R$ 569,91 with PIX.
    assert 81 not in _ids(catalog.search_products(conn, category="violao", max_price=570, limit=10))
    assert 81 in _ids(
        catalog.search_products(conn, category="violao", max_price=570, payment="pix", limit=10)
    )


@pytest.mark.parametrize("query", ["violões", "violão", "violao"])
def test_acoustic_guitars_up_to_1000(conn, query):
    result = catalog.search_products(conn, query=query, max_price=1000, limit=10)
    assert result["total_matches"] == 12
    assert result["returned"] == 10
    assert {item["category"] for item in result["products"]} == {"Violões"}


def test_ukulele_mentioning_a_violao_is_not_a_violao(conn):
    everything = catalog.search_products(conn, query="violão", only_available=False, limit=10)
    assert everything["total_matches"] == 35
    assert "Kala KA-B Barítono Mogno" not in [item["name"] for item in everything["products"]]


@pytest.mark.parametrize("query", ["viola", "violas", "violino"])
def test_instruments_outside_the_data_do_not_match_by_prefix(conn, query):
    result = catalog.search_products(conn, query=query, only_available=False)
    assert result["total_matches"] == 0
    assert "note" in result


@pytest.mark.parametrize(
    "query, category",
    [
        ("violão barato", "Violões"),
        ("violão mais barato", "Violões"),
        ("violão bom", "Violões"),
        ("melhor opção de violão", "Violões"),
        ("ukulele bom", "Ukuleles"),
    ],
)
def test_preference_words_do_not_turn_into_partial_matches(conn, query, category):
    result = catalog.search_products(conn, query=query, limit=10)
    assert "match_quality" not in result
    assert result["total_matches"] > 0
    assert {item["category"] for item in result["products"]} == {category}


def test_product_id_beyond_sqlite_range_is_just_not_found(conn):
    huge = 10**20
    assert catalog.get_product_details(conn, huge)["error"] == "product_not_found"
    assert catalog.calculate_offer(conn, huge, "pix")["error"] == "product_not_found"


def test_filter_happens_before_limit_and_sort_is_by_effective_price(conn):
    result = catalog.search_products(conn, category="violão", max_price=1000, limit=3)
    prices = [item["price_cents"] for item in result["products"]]
    assert result["total_matches"] == 12
    assert prices == sorted(prices)


@pytest.mark.parametrize(
    "product_id, availability",
    [
        (96, "out_of_stock"),
        (113, "discontinued"),
        (130, "coming_soon"),
        (136, "discontinued"),
    ],
)
def test_products_not_on_sale(conn, product_id, availability):
    offer = catalog.calculate_offer(conn, product_id, "pix")
    assert offer["availability"] == availability
    assert offer["sale_available"] is False
    assert product_id not in _ids(
        catalog.search_products(conn, query=offer["name"], limit=10)
    )


def test_exact_search_for_unavailable_product_reports_it(conn):
    result = catalog.search_products(conn, query="Giannini GF-3D")
    assert result["products"] == []
    assert [item["product_id"] for item in result["unavailable_matches"]] == [96]


def test_including_unavailable_products_when_asked(conn):
    result = catalog.search_products(conn, query="Ibanez Bass 2X", only_available=False)
    assert 136 in _ids(result)


@pytest.mark.parametrize("query", ["KAL700T", "kal-700t", "Kalani KAL 700T", "kalani"])
def test_model_name_variations(conn, query):
    assert 127 in _ids(catalog.search_products(conn, query=query))


def test_search_ignores_accents_and_finds_attributes(conn):
    result = catalog.search_products(conn, query="violao nylon", limit=10)
    assert result["total_matches"] > 0
    assert "match_quality" not in result


def test_unknown_model_returns_flagged_near_matches(conn):
    result = catalog.search_products(conn, query="Yamaha XYZ999")
    assert result["match_quality"] == "partial"
    assert all("Yamaha" in item["name"] for item in result["products"])


def test_nothing_found_is_explicit(conn):
    result = catalog.search_products(conn, query="theremin")
    assert result["total_matches"] == 0
    assert "note" in result


def test_cheaper_alternatives_sorted(conn):
    result = catalog.search_products(conn, category="ukulele", sort="price_asc", limit=10)
    prices = [item["price_cents"] for item in result["products"]]
    assert prices == sorted(prices)


def test_search_requires_a_criterion(conn):
    with pytest.raises(ToolInputError):
        catalog.search_products(conn)
    with pytest.raises(ToolInputError):
        catalog.search_products(conn, query="o de para")


def test_inverted_price_range_is_rejected(conn):
    with pytest.raises(ToolInputError):
        catalog.search_products(conn, min_price=500, max_price=100)


def test_empty_category_is_explained(conn):
    with pytest.raises(ToolInputError, match="Categorias com produtos"):
        catalog.search_products(conn, category="trompete")


def test_credit_eligibility_in_an_offer(conn):
    denied = catalog.calculate_offer(conn, 83, "credit", 6)  # R$ 429,90
    assert denied["credit"]["eligible"] is False
    assert denied["credit"]["smallest_installment_if_split"] == "R$ 71,65"
    assert denied["credit"]["max_eligible_installments"] == 5

    allowed = catalog.calculate_offer(conn, 81, "credit", 6)  # R$ 599,90
    assert allowed["credit"]["eligible"] is True
    assert allowed["credit"]["schedule"] == [
        {"count": 2, "value": "R$ 99,99"},
        {"count": 4, "value": "R$ 99,98"},
    ]


def test_credit_is_computed_on_the_promotional_price(conn):
    offer = catalog.calculate_offer(conn, 127, "credit", 3)
    assert offer["price"] == "R$ 323,10"
    assert offer["credit"]["schedule"] == [{"count": 3, "value": "R$ 107,70"}]


def test_credit_without_installments_returns_the_best_plan(conn):
    offer = catalog.calculate_offer(conn, 95, "credit")
    assert offer["credit"]["max_eligible_installments"] == 12
    assert "requested_installments" not in offer["credit"]


def test_installments_only_apply_to_credit(conn):
    with pytest.raises(ToolInputError):
        catalog.calculate_offer(conn, 95, "pix", 3)


@pytest.mark.parametrize("count", [0, 13])
def test_out_of_range_installments_are_rejected(conn, count):
    with pytest.raises(ToolInputError):
        catalog.calculate_offer(conn, 95, "credit", count)


def test_unknown_product_is_explicit(conn):
    assert catalog.calculate_offer(conn, 99999, "pix")["error"] == "product_not_found"
    assert catalog.get_product_details(conn, 99999)["error"] == "product_not_found"
