"""Return deadlines: boundaries, what each rule counts from, and order ownership."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from emporio import config, tools
from emporio.agent import Agent
from emporio.llm import LLMReply, ToolCall
from emporio.orders import Customer
from emporio.prompts import build_system_prompt

TODAY = date(2026, 11, 10)


def _check(conn, customer_id=None, today=TODAY, **arguments):
    context = tools.ToolContext(conn=conn, customer_id=customer_id, today=today)
    return tools.execute("check_return_window", json.dumps(arguments), context)


def _regret(conn, received, **extra):
    return _check(conn, reason="regret", purchase_channel="online", received_date=received, **extra).payload


@pytest.mark.parametrize(
    "received, result, deadline, elapsed, remaining, last_day",
    [
        ("2026-11-10", "within_window", "17/11/2026", 0, 7, False),
        ("2026-11-05", "within_window", "12/11/2026", 5, 2, False),
        ("2026-11-03", "within_window", "10/11/2026", 7, 0, True),
        ("2026-11-02", "window_closed", "09/11/2026", 8, 0, False),
        ("2025-11-03", "window_closed", "10/11/2025", 372, 0, False),
    ],
)
def test_regret_counts_seven_calendar_days_from_receipt(conn, received, result, deadline, elapsed, remaining, last_day):
    payload = _regret(conn, received)
    assert payload["result"] == result
    assert payload["deadline"] == deadline
    assert payload["days_elapsed"] == elapsed
    assert payload["days_remaining"] == remaining
    assert payload["last_day"] is last_day
    assert payload["today"] == "10/11/2026"
    assert payload["policy_section"] == "4.1"


def test_deadline_crosses_month_and_year(conn):
    payload = _check(
        conn, today=date(2027, 1, 2), reason="regret", purchase_channel="online", received_date="2026-12-28"
    ).payload
    assert (payload["deadline"], payload["days_remaining"]) == ("04/01/2027", 2)


def test_deadline_handles_leap_day(conn):
    payload = _check(
        conn, today=date(2028, 2, 29), reason="regret", purchase_channel="online", received_date="2028-02-25"
    ).payload
    assert (payload["deadline"], payload["days_elapsed"]) == ("03/03/2028", 4)


def test_days_ago_is_resolved_against_today(conn):
    payload = _check(conn, reason="regret", purchase_channel="online", received_days_ago=5).payload
    assert payload["counted_from"] == "05/11/2026"
    assert payload["days_remaining"] == 2


def test_regret_asks_for_everything_it_lacks(conn):
    payload = _check(conn, reason="regret").payload
    assert payload["result"] == "needs_information"
    assert payload["missing"] == ["purchase_channel", "received_date"]


def test_missing_channel_gives_only_a_conditional_count(conn):
    payload = _check(conn, reason="regret", received_days_ago=1).payload
    assert (payload["result"], payload["missing"]) == ("needs_information", ["purchase_channel"])
    assert "deadline" not in payload
    assert payload["if_online"]["deadline"] == "16/11/2026"


def test_missing_receipt_gives_no_conditional_count(conn):
    payload = _check(conn, reason="regret").payload
    assert "if_online" not in payload


def test_purchase_date_never_stands_in_for_receipt_on_regret(conn):
    payload = _check(conn, reason="regret", purchase_channel="online", purchase_date="2026-11-09").payload
    assert (payload["result"], payload["missing"]) == ("needs_information", ["received_date"])


def test_regret_does_not_cover_store_purchases(conn):
    payload = _check(conn, reason="regret", purchase_channel="store", received_date="2026-11-09").payload
    assert payload["result"] == "not_applicable"
    assert "deadline" not in payload


@pytest.mark.parametrize(
    "purchase, result, deadline",
    [
        ("2026-10-11", "within_window", "10/11/2026"),
        ("2026-10-10", "window_closed", "09/11/2026"),
    ],
)
def test_defect_counts_thirty_days_from_purchase(conn, purchase, result, deadline):
    payload = _check(conn, reason="defect", purchase_date=purchase).payload
    assert (payload["result"], payload["deadline"]) == (result, deadline)
    assert payload["policy_section"] == "4.2"


def test_defect_needs_a_purchase_date(conn):
    payload = _check(conn, reason="defect", received_date="2026-11-09").payload
    assert (payload["result"], payload["missing"]) == ("needs_information", ["purchase_date"])


def test_closed_defect_window_is_not_presented_as_no_coverage(conn):
    payload = _check(conn, reason="defect", purchase_date="2026-08-01", received_date="2026-08-20").payload
    assert payload["result"] == "window_closed"
    assert "não significa ausência de cobertura" in payload["guidance"]
    legal = payload["legal_warranty"]
    assert (legal["open"], legal["deadline"], legal["days_remaining"]) == (True, "18/11/2026", 8)


def test_legal_warranty_needs_the_receipt_date(conn):
    legal = _check(conn, reason="defect", purchase_date="2026-08-01").payload["legal_warranty"]
    assert legal["counted_from"] == "recebimento"
    assert "deadline" not in legal


@pytest.mark.parametrize(
    "dates, result",
    [
        ({"purchase_date": "2026-11-05"}, "within_window"),
        ({"purchase_date": "2026-11-05", "received_date": "2026-11-08"}, "within_window"),
        ({"received_date": "2026-11-01"}, "window_closed"),
        ({"purchase_date": "2026-10-20", "received_date": "2026-11-01"}, "window_closed"),
        ({"purchase_date": "2026-11-01", "received_date": "2026-11-08"}, "depends_on_start"),
        ({"purchase_date": "2026-11-01"}, "needs_information"),
        ({"received_date": "2026-11-08"}, "needs_information"),
        ({}, "needs_information"),
    ],
)
def test_preference_concludes_only_what_holds_for_either_start(conn, dates, result):
    payload = _check(conn, reason="preference", **dates).payload
    assert payload["result"] == result


def test_unclear_preference_start_gives_both_deadlines_and_no_verdict(conn):
    payload = _check(conn, reason="preference", purchase_date="2026-11-01", received_date="2026-11-08").payload
    assert payload["deadline_if_from_purchase"] == "08/11/2026"
    assert payload["deadline_if_from_receipt"] == "15/11/2026"
    assert "open" not in payload


def test_recorded_order_date_is_used_for_defects(conn):
    # Order 4 belongs to customer 1 and was placed on 2025-12-10.
    payload = _check(conn, customer_id=1, today=date(2025, 12, 20), reason="defect", order_id=4).payload
    assert payload["counted_from"] == "10/12/2025"
    assert (payload["deadline"], payload["days_remaining"]) == ("09/01/2026", 20)


def test_recorded_order_date_beats_a_stated_one(conn):
    payload = _check(
        conn, customer_id=1, today=date(2025, 12, 20), reason="defect", order_id=4, purchase_date="2025-12-19"
    ).payload
    assert payload["counted_from"] == "10/12/2025"


def test_delivery_forecast_is_never_used_as_receipt(conn):
    # Order 4 has a forecast of 2025-12-22, inside the window, but receipt was never recorded.
    payload = _check(
        conn, customer_id=1, today=date(2025, 12, 23), reason="regret", purchase_channel="online", order_id=4
    ).payload
    assert (payload["result"], payload["missing"]) == ("needs_information", ["received_date"])
    assert "2025" not in json.dumps({key: payload[key] for key in ("result", "missing")})


def test_foreign_and_missing_orders_get_the_same_denial(conn):
    foreign = _check(conn, customer_id=6, reason="defect", order_id=1)
    missing = _check(conn, customer_id=6, reason="defect", order_id=99999)
    huge = _check(conn, customer_id=6, reason="defect", order_id=10**20)
    assert foreign.payload == missing.payload == huge.payload
    assert foreign.error == "not_found_or_not_authorized"
    assert "15/10/2025" not in json.dumps(foreign.payload)


def test_order_needs_an_identified_session(conn):
    outcome = _check(conn, reason="defect", order_id=4)
    assert outcome.error == "identification_required"


def test_dates_work_without_an_identified_session(conn):
    assert _check(conn, reason="defect", purchase_date="2026-11-01").payload["result"] == "within_window"


def test_cancelled_order_has_no_deadline(conn):
    # Order 20 belongs to customer 7 and was cancelled.
    payload = _check(conn, customer_id=7, reason="defect", order_id=20).payload
    assert payload["result"] == "order_cancelled"
    assert "deadline" not in payload


@pytest.mark.parametrize("customer_id, order_id", [(6, 9), (2, 8)])
def test_undelivered_order_with_stated_receipt_is_flagged(conn, customer_id, order_id):
    payload = _check(
        conn,
        customer_id=customer_id,
        today=date(2026, 3, 10),
        reason="regret",
        purchase_channel="online",
        order_id=order_id,
        received_date="2026-03-08",
    ).payload
    assert payload["result"] == "within_window"
    assert "ainda não indica entrega" in payload["record_note"]


def test_delivered_order_has_no_record_note(conn):
    payload = _check(conn, customer_id=1, today=date(2025, 12, 20), reason="defect", order_id=4).payload
    assert "record_note" not in payload


@pytest.mark.parametrize(
    "arguments, fragment",
    [
        ({"reason": "defect", "purchase_date": "2026-11-11"}, "posterior a hoje"),
        ({"reason": "regret", "purchase_channel": "online", "received_date": "2026-12-25"}, "posterior a hoje"),
        ({"reason": "defect", "purchase_date": "2026-11-05", "received_date": "2026-11-01"}, "anterior à compra"),
    ],
)
def test_impossible_dates_go_back_to_the_model(conn, arguments, fragment):
    outcome = _check(conn, **arguments)
    assert outcome.error == "invalid_request"
    assert fragment in outcome.payload["message"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"reason": "refund"},
        {"reason": "defect", "purchase_date": "10/11/2026"},
        {"reason": "defect", "purchase_date": "2026-02-30"},
        {"reason": "defect", "purchase_date": "20261101"},
        {"reason": "defect", "purchase_date": "2026-W45-2"},
        {"reason": "defect", "purchase_days_ago": -1},
        {"reason": "defect", "purchase_days_ago": 10**9},
        {"reason": "defect", "purchase_date": "2026-11-01", "purchase_days_ago": 2},
        {"reason": "regret", "received_date": "2026-11-01", "received_days_ago": 2},
        {"reason": "regret", "purchase_channel": "whatsapp"},
    ],
)
def test_invalid_arguments_are_rejected(conn, arguments):
    outcome = _check(conn, **arguments)
    assert outcome.error == "invalid_arguments"
    json.dumps(outcome.payload)


def test_model_supplied_customer_is_rejected(conn):
    context = tools.ToolContext(conn=conn, customer_id=6, today=TODAY)
    raw = json.dumps({"reason": "defect", "order_id": 1, "customer_id": 3})
    outcome = tools.execute("check_return_window", raw, context)
    assert outcome.error == "invalid_arguments"
    assert "15/10/2025" not in json.dumps(outcome.payload)


def test_policy_section_reaches_the_trace_sources(conn):
    assert _check(conn, reason="defect", purchase_date="2026-11-01").sources == ["4.2"]


def test_payload_tells_the_model_nothing_was_opened(conn):
    assert "não abre nenhuma solicitação" in _regret(conn, "2026-11-09")["conditions_note"]


class _ScriptedLLM:
    model = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        return self._replies.pop(0)


def test_agent_uses_the_store_date_not_the_model(conn):
    """Just before midnight in Campo Grande the date must still be the store's own."""
    moment = datetime(2026, 11, 10, 23, 30, tzinfo=config.STORE_TIMEZONE)
    arguments = json.dumps(
        {"reason": "regret", "purchase_channel": "online", "received_date": "2026-11-03"}
    )
    llm = _ScriptedLLM(
        LLMReply(None, [ToolCall("call_1", "check_return_window", arguments)]),
        LLMReply("Último dia!"),
    )
    agent = Agent(llm, conn, Customer(6, "Juliana"), now=lambda: moment)
    reply = agent.respond("Recebi em 03/11, dá para devolver?")
    assert "10/11/2026 23:30" in llm.calls[0][0]["content"]
    result = json.loads(llm.calls[1][-1]["content"])
    assert (result["today"], result["last_day"]) == ("10/11/2026", True)
    assert reply.tool_traces[0].sources == ["4.1"]


def test_prompt_forbids_counting_days_by_hand():
    prompt = build_system_prompt(datetime(2026, 11, 10, 10, 0, tzinfo=config.STORE_TIMEZONE), None, {})
    assert "check_return_window" in prompt
    assert "Nunca conte dias por conta própria" in prompt
    assert "Previsão de entrega não é data de recebimento" in prompt
