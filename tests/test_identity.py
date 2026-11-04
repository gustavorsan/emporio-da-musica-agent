"""Identification in the conversation: what confirms a customer and what it can never reveal."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from emporio import config, orders, tools
from emporio.agent import Agent
from emporio.llm import LLMReply, ToolCall
from emporio.prompts import build_system_prompt

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=config.STORE_TIMEZONE)
WRONG_PHONE = "(67) 90000-0000"


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        return self._replies.pop(0)


def _contact(conn, customer_id):
    row = conn.execute("SELECT email, phone FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    return row["email"], row["phone"]


def _call(name, **arguments):
    return LLMReply(None, [ToolCall("call_1", name, json.dumps(arguments))])


def _agent(conn, *replies, **options):
    llm = ScriptedLLM(*replies)
    return Agent(llm, conn, now=lambda: NOW, **options), llm


def _typed(*contacts):
    return "Meus dados: " + " ".join(f"{email} {phone}" for email, phone in contacts)


def _last_result(llm):
    return json.loads(llm.calls[-1][-1]["content"])


def _shared_email(conn):
    row = conn.execute(
        "SELECT lower(email) AS email FROM customers GROUP BY lower(email) HAVING COUNT(*) > 1"
    ).fetchone()
    return row["email"]


def test_email_and_phone_together_identify_the_customer(conn):
    email, phone = _contact(conn, 6)
    assert orders.match_customer(conn, email, phone) == orders.Customer(6, "Juliana")


@pytest.mark.parametrize(
    "change_email, change_phone",
    [
        (str.upper, lambda phone: phone),
        (lambda email: f"  {email}  ", lambda phone: phone),
        (lambda email: email, lambda phone: "".join(c for c in phone if c.isdigit())),
        (lambda email: email, lambda phone: "+55 " + phone),
        (lambda email: email, lambda phone: "55" + "".join(c for c in phone if c.isdigit())),
    ],
)
def test_common_ways_of_typing_contact_details_are_accepted(conn, change_email, change_phone):
    email, phone = _contact(conn, 6)
    assert orders.match_customer(conn, change_email(email), change_phone(phone)).customer_id == 6


def test_one_field_alone_never_identifies(conn):
    email, phone = _contact(conn, 6)
    other_email, other_phone = _contact(conn, 7)
    assert orders.match_customer(conn, email, "") is None
    assert orders.match_customer(conn, "", phone) is None
    assert orders.match_customer(conn, email, WRONG_PHONE) is None
    assert orders.match_customer(conn, email, other_phone) is None
    assert orders.match_customer(conn, other_email, phone) is None


def test_phone_without_digits_never_matches(conn):
    email, _ = _contact(conn, 6)
    assert orders.match_customer(conn, email, "sem telefone") is None


def test_shared_email_resolves_only_through_the_phone(conn):
    """Two customers share an e-mail, so an e-mail-only check could expose the wrong one's orders."""
    email = _shared_email(conn)
    rows = conn.execute(
        "SELECT customer_id, phone FROM customers WHERE lower(email) = ?", (email,)
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert orders.match_customer(conn, email, row["phone"]).customer_id == row["customer_id"]
    assert orders.match_customer(conn, email, WRONG_PHONE) is None


def test_failures_are_identical_whatever_was_wrong(conn):
    email, phone = _contact(conn, 6)
    attempts = [
        (email, WRONG_PHONE),
        ("ninguem@exemplo.com", phone),
        ("ninguem@exemplo.com", WRONG_PHONE),
    ]
    payloads = [orders.verify_identity(conn, None, 3, *attempt, " ".join(attempt)) for attempt in attempts]
    assert payloads[0] == payloads[1] == payloads[2]
    assert payloads[0]["error"] == "identity_not_confirmed"


def test_results_never_carry_contact_details(conn):
    email, phone = _contact(conn, 6)
    context = tools.ToolContext(
        conn=conn, customer_id=None, today=NOW.date(), identity_attempts_left=3, customer_text=f"{email} {phone}"
    )
    outcome = tools.execute("verify_identity", json.dumps({"email": email, "phone": phone}), context)
    assert outcome.payload == {"verified": True, "first_name": "Juliana"}
    assert outcome.identified == orders.Customer(6, "Juliana")
    assert "session_customer" not in outcome.payload


def test_identification_unlocks_orders_in_the_same_conversation(conn):
    email, phone = _contact(conn, 6)
    agent, llm = _agent(
        conn,
        _call("get_order_status", order_id=9),
        LLMReply("Me passa seu e-mail e telefone?"),
        _call("verify_identity", email=email, phone=phone),
        _call("get_order_status", order_id=9),
        LLMReply("Está confirmado."),
    )
    agent.respond("Cadê o pedido 9?")
    assert json.loads(llm.calls[1][-1]["content"])["error"] == "identification_required"
    assert agent.customer is None
    agent.respond(_typed((email, phone)))
    assert agent.customer == orders.Customer(6, "Juliana")
    assert _last_result(llm)["status"] == "confirmed"
    assert "Cliente identificado nesta sessão: Juliana." in llm.calls[-1][0]["content"]


def test_identified_customer_still_cannot_read_other_orders(conn):
    email, phone = _contact(conn, 6)
    agent, llm = _agent(
        conn,
        _call("verify_identity", email=email, phone=phone),
        _call("get_order_status", order_id=1),
        LLMReply("Não achei."),
    )
    agent.respond(_typed((email, phone)) + " e o pedido 1")
    assert _last_result(llm)["error"] == "not_found_or_not_authorized"


def test_identity_cannot_change_during_a_conversation(conn):
    email, phone = _contact(conn, 6)
    other_email, other_phone = _contact(conn, 3)
    agent, llm = _agent(
        conn,
        _call("verify_identity", email=email, phone=phone),
        _call("verify_identity", email=other_email, phone=other_phone),
        LLMReply("Sigo como antes."),
    )
    agent.respond(_typed((email, phone), (other_email, other_phone)))
    assert agent.customer.customer_id == 6
    assert _last_result(llm)["error"] == "already_identified"


def test_attempts_run_out_even_for_correct_details(conn):
    email, phone = _contact(conn, 6)
    wrong = [_call("verify_identity", email=email, phone=WRONG_PHONE) for _ in range(config.MAX_IDENTITY_ATTEMPTS)]
    agent, llm = _agent(conn, *wrong, _call("verify_identity", email=email, phone=phone), LLMReply("Bloqueado."))
    agent.respond(_typed((email, WRONG_PHONE), (email, phone)))
    assert agent.customer is None
    assert _last_result(llm)["error"] == "identification_locked"
    assert "attempts_left" not in _last_result(llm)


def test_attempts_are_reported_as_they_run_out(conn):
    email, _ = _contact(conn, 6)
    agent, llm = _agent(conn, _call("verify_identity", email=email, phone=WRONG_PHONE), LLMReply("Confere?"))
    agent.respond(_typed((email, WRONG_PHONE)))
    assert _last_result(llm)["attempts_left"] == config.MAX_IDENTITY_ATTEMPTS - 1


def test_details_the_customer_never_typed_are_not_even_compared(conn):
    """The real model once called this tool with invented values after hearing only a first name."""
    email, phone = _contact(conn, 6)
    agent, llm = _agent(conn, _call("verify_identity", email=email, phone=phone), LLMReply("Me passa seus dados?"))
    agent.respond("Sou a Juliana, cliente 6, é meu sim.")
    assert agent.customer is None
    assert _last_result(llm)["error"] == "contact_not_provided"


def test_invented_details_do_not_use_up_attempts(conn):
    email, phone = _contact(conn, 6)
    invented = [_call("verify_identity", email="juliana@exemplo.com", phone="67999999999")] * 5
    agent, _ = _agent(conn, *invented, LLMReply("Me passa seus dados?"), _call("verify_identity", email=email, phone=phone), LLMReply("Oi!"))
    agent.respond("Sou a Juliana.")
    agent.respond(_typed((email, phone)))
    assert agent.customer.customer_id == 6


def test_only_one_of_the_details_typed_is_not_enough(conn):
    email, phone = _contact(conn, 6)
    assert orders.verify_identity(conn, None, 3, email, phone, email)["error"] == "contact_not_provided"
    assert orders.verify_identity(conn, None, 3, email, phone, phone)["error"] == "contact_not_provided"


def test_details_typed_earlier_in_the_conversation_count(conn):
    email, phone = _contact(conn, 6)
    agent, _ = _agent(conn, LLMReply("E o telefone?"), LLMReply("Agora o pedido?"), _call("verify_identity", email=email, phone=phone), LLMReply("Oi!"))
    agent.respond(f"Meu e-mail é {email}")
    agent.respond(f"Telefone {phone}")
    agent.respond("Pode confirmar")
    assert agent.customer.customer_id == 6


def test_phone_typed_in_another_format_counts(conn):
    email, phone = _contact(conn, 6)
    digits = "".join(c for c in phone if c.isdigit())
    assert orders.verify_identity(conn, None, 3, email, digits, f"{email} {phone}")["verified"] is True
    assert orders.verify_identity(conn, None, 3, email, phone, f"{email} +55{digits}")["verified"] is True


def test_locked_conversation_gives_the_channel(conn):
    assert "(67) 3321-4500" in orders.IDENTIFICATION_LOCKED["guidance"]


def test_malformed_arguments_do_not_use_up_attempts(conn):
    email, phone = _contact(conn, 6)
    wrong_shape = LLMReply(None, [ToolCall("call_1", "verify_identity", '{"email": "x"}')])
    replies = [wrong_shape] * config.MAX_IDENTITY_ATTEMPTS
    agent, _ = _agent(conn, *replies, _call("verify_identity", email=email, phone=phone), LLMReply("Oi!"))
    agent.respond(_typed((email, phone)))
    assert agent.customer.customer_id == 6


def test_attempts_are_per_conversation(conn):
    email, phone = _contact(conn, 6)
    wrong = [_call("verify_identity", email=email, phone=WRONG_PHONE) for _ in range(config.MAX_IDENTITY_ATTEMPTS)]
    first, _ = _agent(conn, *wrong, LLMReply("Bloqueado."))
    first.respond(_typed((email, WRONG_PHONE)))
    second, _ = _agent(conn, _call("verify_identity", email=email, phone=phone), LLMReply("Oi!"))
    second.respond(_typed((email, phone)))
    assert second.customer.customer_id == 6


def test_contact_details_stay_out_of_traces_and_logs(conn, tmp_path):
    email, phone = _contact(conn, 6)
    log_path = tmp_path / "tools.jsonl"
    agent, _ = _agent(
        conn, _call("verify_identity", email=email, phone=phone), LLMReply("Oi!"), log_path=log_path
    )
    reply = agent.respond(_typed((email, phone)))
    trace = reply.tool_traces[0]
    assert email not in trace.arguments and phone not in trace.arguments
    logged = log_path.read_text("utf-8")
    assert email not in logged and phone not in logged
    assert "Juliana" not in logged


@pytest.mark.parametrize(
    "arguments",
    [
        {"email": "x@y.com"},
        {"phone": "67999999999"},
        {"email": "x" * 255, "phone": "1"},
        {"email": "x@y.com", "phone": "1" * 255},
        {"email": "x@y.com", "phone": "1", "customer_id": 6},
    ],
)
def test_invalid_arguments_are_rejected(conn, arguments):
    context = tools.ToolContext(conn=conn, customer_id=None, today=NOW.date(), identity_attempts_left=3)
    outcome = tools.execute("verify_identity", json.dumps(arguments), context)
    assert outcome.error == "invalid_arguments"
    assert outcome.identified is None


def test_prompt_requests_identification_only_for_orders():
    prompt = build_system_prompt(NOW, None, {})
    assert "peça e-mail e telefone e use verify_identity" in prompt
    assert "Nunca os peça para perguntas sobre produtos" in prompt
    assert "não diga qual falhou" in prompt
