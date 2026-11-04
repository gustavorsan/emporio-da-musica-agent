"""Tool layer: schemas compatible with strict mode, validation and identity isolation."""

from __future__ import annotations

import json
from datetime import date

import pytest

from emporio import tools


@pytest.fixture
def context(conn):
    return tools.ToolContext(conn=conn, customer_id=6, today=date(2026, 11, 3))


def _run(context, name, **arguments):
    return tools.execute(name, json.dumps(arguments), context)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_exposes_exactly_the_five_planned_tools():
    assert {schema["function"]["name"] for schema in tools.TOOL_SCHEMAS} == {
        "search_products",
        "get_product_details",
        "calculate_offer",
        "get_order_status",
        "search_knowledge",
    }


@pytest.mark.parametrize("schema", tools.TOOL_SCHEMAS, ids=lambda s: s["function"]["name"])
def test_schemas_are_strict(schema):
    function = schema["function"]
    assert function["strict"] is True
    parameters = function["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"])
    for node in _walk(parameters):
        assert node.get("default") is None


def test_no_schema_lets_the_model_choose_a_customer():
    assert "customer" not in json.dumps(tools.TOOL_SCHEMAS)


def test_model_supplied_customer_is_rejected(context):
    outcome = _run(context, "get_order_status", order_id=1, customer_id=3)
    assert outcome.ok is False
    assert outcome.error == "invalid_arguments"
    assert "order_id" not in outcome.payload


def test_session_customer_is_injected(context):
    assert _run(context, "get_order_status", order_id=9).payload["status"] == "confirmed"
    assert _run(context, "get_order_status", order_id=1).error == "not_found_or_not_authorized"


def test_strict_mode_nulls_are_accepted(context):
    outcome = _run(
        context,
        "search_products",
        query="violão",
        category=None,
        min_price=None,
        max_price=None,
        only_available=None,
        sort=None,
        payment=None,
        limit=None,
    )
    assert outcome.ok is True
    assert outcome.payload["total_matches"] > 0


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("search_products", {"query": "violão", "limit": 500}),
        ("search_products", {"max_price": -1}),
        ("calculate_offer", {"product_id": 95, "payment": "bitcoin"}),
        ("calculate_offer", {"payment": "pix"}),
        ("search_knowledge", {"question": "frete", "tags": ["inexistente"]}),
    ],
)
def test_invalid_arguments_return_a_readable_error(context, name, arguments):
    outcome = _run(context, name, **arguments)
    assert outcome.ok is False
    assert outcome.error == "invalid_arguments"
    json.dumps(outcome.payload)


def test_order_number_beyond_sqlite_range_gets_the_usual_denial(context):
    huge = _run(context, "get_order_status", order_id=99999999999999999999)
    missing = _run(context, "get_order_status", order_id=99999)
    assert huge.payload == missing.payload
    assert huge.error == "not_found_or_not_authorized"


@pytest.mark.parametrize(
    "raw",
    [
        '{"max_price": 1e30}',
        '{"max_price": 1e308}',
        '{"max_price": Infinity}',
        '{"max_price": NaN}',
        '{"min_price": -Infinity}',
    ],
)
def test_absurd_prices_are_rejected_without_raising(context, raw):
    outcome = tools.execute("search_products", raw, context)
    assert outcome.error == "invalid_arguments"
    json.dumps(outcome.payload)


def test_unexpected_tool_failure_does_not_escape(context, monkeypatch):
    def explode(*args):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(tools.catalog, "get_product_details", explode)
    outcome = _run(context, "get_product_details", product_id=95)
    assert outcome.ok is False
    assert outcome.error == "unexpected:RuntimeError"
    assert outcome.payload["error"] == "lookup_failed"
    assert "secret" not in json.dumps(outcome.payload)


def test_malformed_json_is_handled(context):
    outcome = tools.execute("search_products", "{not json", context)
    assert outcome.error == "invalid_arguments"


def test_unknown_tool_is_handled(context):
    assert tools.execute("run_sql", "{}", context).error == "unknown_tool"


def test_business_rule_errors_are_returned_to_the_model(context):
    outcome = _run(context, "search_products", query="de")
    assert outcome.error == "invalid_request"


def test_database_failure_does_not_leak_details(db_path):
    from emporio.db import connect_readonly

    broken = connect_readonly(db_path)
    broken.close()
    ctx = tools.ToolContext(conn=broken, customer_id=6, today=date(2026, 11, 3))
    outcome = tools.execute("get_product_details", '{"product_id": 95}', ctx)
    assert outcome.error == "lookup_failed"
    assert "closed" not in json.dumps(outcome.payload)


def test_outcome_reports_sources_and_products(context):
    knowledge = _run(context, "search_knowledge", question="Me arrependi da compra")
    assert "4.1" in knowledge.sources
    catalog = _run(context, "calculate_offer", product_id=127, payment="pix")
    assert catalog.products == {127: "Kalani KAL-700T Tenor Natural"}
