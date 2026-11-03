"""Conversation loop mechanics with a scripted client.

These tests exercise control flow only. They say nothing about the quality of real model
replies, which is what evals/ and conversations/ are for.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from emporio import agent as agent_module
from emporio import config
from emporio.agent import INCOMPLETE_REPLY, PROVIDER_FAILURE_REPLY, Agent
from emporio.llm import LLMError, LLMReply, ToolCall
from emporio.orders import Customer
from emporio.prompts import build_system_prompt, describe_store_hours

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=config.STORE_TIMEZONE)


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _call(name, call_id="call_1", **arguments):
    return LLMReply(None, [ToolCall(call_id, name, json.dumps(arguments))])


def _agent(conn, llm, customer=None, log_path=None):
    return Agent(llm, conn, customer, now=lambda: NOW, log_path=log_path)


def test_tool_result_reaches_the_model_and_final_text_is_returned(conn):
    llm = ScriptedLLM(_call("calculate_offer", product_id=127, payment="pix"), LLMReply("Sai R$ 323,10."))
    reply = _agent(conn, llm).respond("Kalani no PIX?")
    assert reply.text == "Sai R$ 323,10."
    assert [trace.name for trace in reply.tool_traces] == ["calculate_offer"]
    tool_message = llm.calls[1][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert json.loads(tool_message["content"])["price"] == "R$ 323,10"


def test_parallel_tool_calls_each_get_a_result(conn):
    both = LLMReply(
        None,
        [
            ToolCall("a", "get_product_details", '{"product_id": 95}'),
            ToolCall("b", "search_knowledge", '{"question": "garantia", "tags": null}'),
        ],
    )
    llm = ScriptedLLM(both, LLMReply("ok"))
    _agent(conn, llm).respond("misto")
    ids = [message["tool_call_id"] for message in llm.calls[1] if message["role"] == "tool"]
    assert ids == ["a", "b"]


def test_history_carries_over_between_turns(conn):
    llm = ScriptedLLM(LLMReply("Primeira."), LLMReply("Segunda."))
    agent = _agent(conn, llm)
    agent.respond("oi")
    agent.respond("e agora?")
    roles = [message["role"] for message in llm.calls[1]]
    assert roles == ["system", "user", "assistant", "user"]


def test_discussed_products_enter_the_prompt(conn):
    llm = ScriptedLLM(
        _call("calculate_offer", product_id=127, payment="pix"), LLMReply("ok"), LLMReply("ok")
    )
    agent = _agent(conn, llm)
    agent.respond("Kalani")
    agent.respond("e no débito?")
    assert "Kalani KAL-700T Tenor Natural (ID 127)" in llm.calls[2][0]["content"]


def test_tool_round_limit_stops_the_loop(conn):
    replies = [_call("get_product_details", product_id=95) for _ in range(config.MAX_TOOL_ROUNDS)]
    llm = ScriptedLLM(*replies)
    reply = _agent(conn, llm).respond("loop")
    assert reply.text == INCOMPLETE_REPLY
    assert len(llm.calls) == config.MAX_TOOL_ROUNDS


def test_provider_failure_gives_a_clear_reply_and_session_continues(conn):
    llm = ScriptedLLM(LLMError("APITimeoutError"), LLMReply("Voltei."))
    agent = _agent(conn, llm)
    assert agent.respond("oi").text == PROVIDER_FAILURE_REPLY
    assert agent.respond("oi de novo").text == "Voltei."


def test_failure_mid_turn_drops_partial_tool_exchange(conn):
    llm = ScriptedLLM(
        _call("get_product_details", product_id=95), LLMError("boom"), LLMReply("ok")
    )
    agent = _agent(conn, llm)
    agent.respond("Takamine")
    agent.respond("de novo")
    roles = [message["role"] for message in llm.calls[2]]
    assert "tool" not in roles
    assert roles == ["system", "user", "assistant", "user"]


def test_empty_reply_falls_back(conn):
    assert _agent(conn, ScriptedLLM(LLMReply("  "))).respond("oi").text == INCOMPLETE_REPLY


def test_invalid_tool_arguments_go_back_to_the_model(conn):
    llm = ScriptedLLM(LLMReply(None, [ToolCall("x", "calculate_offer", "{bad")]), LLMReply("Corrigi."))
    reply = _agent(conn, llm).respond("oi")
    assert reply.text == "Corrigi."
    assert reply.tool_traces[0].ok is False
    assert json.loads(llm.calls[1][-1]["content"])["error"] == "invalid_arguments"


def test_history_trim_keeps_tool_messages_paired(conn, monkeypatch):
    monkeypatch.setattr(config, "MAX_HISTORY_TURNS", 1)
    llm = ScriptedLLM(
        _call("get_product_details", product_id=95),
        LLMReply("primeiro"),
        _call("get_product_details", product_id=127),
        LLMReply("segundo"),
        LLMReply("terceiro"),
    )
    agent = _agent(conn, llm)
    agent.respond("um")
    agent.respond("dois")
    agent.respond("três")
    messages = llm.calls[-1]
    assert [message["role"] for message in messages] == [
        "system", "user", "assistant", "tool", "assistant", "user",
    ]
    assert messages[1]["content"] == "dois"


def test_session_customer_cannot_be_changed_by_tool_arguments(conn):
    llm = ScriptedLLM(_call("get_order_status", order_id=1), LLMReply("Não encontrei."))
    _agent(conn, llm, Customer(6, "Fulano")).respond("pedido 1")
    assert json.loads(llm.calls[1][-1]["content"])["error"] == "not_found_or_not_authorized"


def test_large_tool_results_are_truncated(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_RESULT_CHARS", 50)
    text = agent_module._serialize({"data": "x" * 500})
    assert json.loads(text)["truncated"] is True


def test_log_has_no_argument_values(conn, tmp_path):
    log_path = tmp_path / "logs" / "tools.jsonl"
    llm = ScriptedLLM(_call("search_knowledge", question="segredo do cliente", tags=None), LLMReply("ok"))
    _agent(conn, llm, log_path=log_path).respond("oi")
    content = log_path.read_text("utf-8")
    assert "segredo" not in content
    record = json.loads(content)
    assert record["tool"] == "search_knowledge"
    assert record["argument_names"] == ["question", "tags"]


@pytest.mark.parametrize(
    "moment, expected",
    [
        (datetime(2026, 11, 3, 10, 0), "aberta agora"),  # Tuesday
        (datetime(2026, 11, 3, 18, 0), "abre amanhã às 09:00"),
        (datetime(2026, 11, 3, 8, 0), "abre hoje às 09:00"),
        (datetime(2026, 11, 7, 12, 59), "até 13:00"),  # Saturday
        (datetime(2026, 11, 7, 13, 0), "abre segunda-feira às 09:00"),
        (datetime(2026, 11, 8, 10, 0), "abre amanhã às 09:00"),  # Sunday
    ],
)
def test_store_hours(moment, expected):
    assert expected in describe_store_hours(moment)


def test_prompt_carries_date_and_identification():
    identified = build_system_prompt(NOW, Customer(6, "Ana"), {})
    assert "03/11/2026 10:00" in identified
    assert "terça-feira" in identified
    assert "Cliente identificado nesta sessão: Ana." in identified
    anonymous = build_system_prompt(NOW, None, {})
    assert "Nenhum cliente identificado" in anonymous
