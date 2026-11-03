"""Order access: ownership enforcement and fidelity to what was recorded."""

from __future__ import annotations

import json
from datetime import date

import pytest

from emporio import orders

TODAY = date(2026, 11, 3)


def _get(conn, customer_id, order_id):
    return orders.get_order_status(conn, customer_id, order_id, TODAY)


def test_order_9_has_no_invented_tracking(conn):
    order = _get(conn, 6, 9)
    assert order["status"] == "confirmed"
    assert order["tracking_code"] is None
    assert order["estimated_delivery"] is None
    assert order["missing_fields"] == ["tracking_code", "estimated_delivery"]


@pytest.mark.parametrize(
    "customer_id, order_id, total", [(12, 3, "R$ 3.450,00"), (7, 20, "R$ 1.400,00")]
)
def test_historical_totals_are_preserved(conn, customer_id, order_id, total):
    assert _get(conn, customer_id, order_id)["total_recorded"] == total


def test_foreign_and_missing_orders_get_identical_replies(conn):
    foreign = _get(conn, 6, 1)
    missing = _get(conn, 6, 99999)
    assert foreign == missing
    assert foreign["error"] == "not_found_or_not_authorized"


def test_no_session_customer_means_no_access(conn):
    assert _get(conn, None, 9)["error"] == "identification_required"
    assert _get(conn, None, None)["error"] == "identification_required"


def test_listing_returns_only_session_orders(conn):
    listed = orders.get_order_status(conn, 7, None, TODAY)["orders"]
    assert {item["order_id"] for item in listed} == {2, 20}


def test_customer_without_orders_gets_an_empty_list(conn):
    assert orders.get_order_status(conn, 50, None, TODAY) == {"orders": []}


def test_order_payload_has_no_personal_or_catalog_fields(conn):
    text = json.dumps(_get(conn, 1, 4), ensure_ascii=False)
    for forbidden in ("lucas", "@", "99812", "customer_id", "price"):
        assert forbidden not in text.casefold()


def test_passed_forecast_is_flagged_only_for_shipped_orders(conn):
    shipped = _get(conn, 2, 8)
    assert shipped["estimated_delivery_passed"] is True
    delivered = _get(conn, 1, 4)
    assert "estimated_delivery_passed" not in delivered


def test_forecast_not_yet_passed_is_not_flagged(conn):
    order = orders.get_order_status(conn, 2, 8, date(2026, 2, 28))
    assert "estimated_delivery_passed" not in order


def test_delivered_order_does_not_confirm_a_receipt_date(conn):
    assert _get(conn, 1, 4)["actual_delivery_date_recorded"] is False


def test_historical_items_do_not_imply_availability(conn):
    order = _get(conn, 12, 3)
    assert order["items"] == [{"name": "Kala KA-ABP-CTG Barítono Cedar", "quantity": 2}]


def test_cancelled_order_shows_recorded_reason(conn):
    assert "Endereço de entrega inválido" in _get(conn, 7, 20)["notes"]


def test_payment_method_is_described(conn):
    assert _get(conn, 6, 9)["payment_method"] == "cartão de crédito em 6x"
    assert _get(conn, 1, 4)["payment_method"] == "boleto bancário"


def test_session_customer_exposes_only_the_first_name(conn):
    customer = orders.find_customer(conn, 1)
    assert customer == orders.Customer(1, "Lucas")
    assert orders.find_customer(conn, 99999) is None
