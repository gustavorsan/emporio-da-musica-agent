"""Conversation loop: the model interprets and writes, the tools consult and calculate."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from emporio import config, tools
from emporio.llm import LLMClient, LLMError, LLMReply
from emporio.orders import Customer
from emporio.prompts import build_system_prompt

PROVIDER_FAILURE_REPLY = (
    "Poxa, estou com uma instabilidade aqui e não consegui consultar essa informação agora. "
    "Pode tentar de novo em instantes? Se preferir, fale com a equipe pelo WhatsApp (67) 3321-4500."
)
INCOMPLETE_REPLY = (
    "Desculpa, não consegui concluir essa consulta. Pode reformular a pergunta? "
    "Se preferir, fale com a equipe pelo WhatsApp (67) 3321-4500."
)


@dataclass(frozen=True)
class ToolTrace:
    name: str
    arguments: str
    ok: bool
    error: str | None
    sources: list[str]


@dataclass(frozen=True)
class AgentReply:
    text: str
    tool_traces: list[ToolTrace] = field(default_factory=list)
    # Set when text is a fixed fallback instead of a model reply; for the operator, not the customer.
    failure: str | None = None


def _now() -> datetime:
    return datetime.now(config.STORE_TIMEZONE)


def _serialize(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= config.MAX_TOOL_RESULT_CHARS:
        return text
    return json.dumps(
        {"truncated": True, "partial_result": text[: config.MAX_TOOL_RESULT_CHARS]},
        ensure_ascii=False,
    )


def _log_tool_call(log_path: Path | None, name: str, arguments: str, outcome, seconds: float) -> None:
    """Append a local diagnostic record; argument values are omitted as they may hold personal data."""
    if log_path is None:
        return
    try:
        argument_names = sorted(json.loads(arguments or "{}"))
    except (json.JSONDecodeError, TypeError):
        argument_names = []
    record = {
        "at": _now().isoformat(timespec="seconds"),
        "tool": name,
        "argument_names": argument_names,
        "ok": outcome.ok,
        "error": outcome.error,
        "sources": outcome.sources,
        "duration_ms": round(seconds * 1000),
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Diagnostics must never interrupt a customer conversation.
        pass


def _assistant_message(reply: LLMReply) -> dict:
    message: dict = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in reply.tool_calls
        ]
    return message


class Agent:
    """One customer session; history lives in memory and is lost when the session ends."""

    def __init__(
        self,
        llm: LLMClient,
        conn: sqlite3.Connection,
        customer: Customer | None = None,
        now: Callable[[], datetime] = _now,
        log_path: Path | None = None,
    ) -> None:
        self._llm = llm
        self._conn = conn
        self._customer = customer
        self._now = now
        self._log_path = log_path
        # Whole turns are kept so a trim can never split tool calls from their results.
        self._turns: list[list[dict]] = []
        self._discussed_products: dict[int, str] = {}

    def _messages(self, current_turn: list[dict]) -> list[dict]:
        system = build_system_prompt(self._now(), self._customer, self._discussed_products)
        recent = self._turns[-config.MAX_HISTORY_TURNS :]
        return [
            {"role": "system", "content": system},
            *(message for turn in recent for message in turn),
            *current_turn,
        ]

    def _run_tool(self, call, turn: list[dict], traces: list[ToolTrace]) -> None:
        context = tools.ToolContext(
            conn=self._conn,
            customer_id=self._customer.customer_id if self._customer else None,
            today=self._now().date(),
        )
        started = time.perf_counter()
        outcome = tools.execute(call.name, call.arguments, context)
        _log_tool_call(self._log_path, call.name, call.arguments, outcome, time.perf_counter() - started)
        # Re-inserting moves a repeated product to the end, keeping the list ordered by recency.
        for product_id, name in outcome.products.items():
            self._discussed_products.pop(product_id, None)
            self._discussed_products[product_id] = name
        traces.append(ToolTrace(call.name, call.arguments, outcome.ok, outcome.error, outcome.sources))
        turn.append({"role": "tool", "tool_call_id": call.id, "content": _serialize(outcome.payload)})

    def respond(self, user_text: str) -> AgentReply:
        user_message = {"role": "user", "content": user_text}
        turn = [user_message]
        traces: list[ToolTrace] = []
        try:
            for _ in range(config.MAX_TOOL_ROUNDS):
                reply = self._llm.complete(self._messages(turn), tools.TOOL_SCHEMAS)
                turn.append(_assistant_message(reply))
                if not reply.tool_calls:
                    if reply.content and reply.content.strip():
                        self._turns.append(turn)
                        return AgentReply(reply.content.strip(), traces)
                    return self._fail(user_message, INCOMPLETE_REPLY, traces, "empty_reply")
                for call in reply.tool_calls:
                    self._run_tool(call, turn, traces)
        except LLMError as exc:
            return self._fail(user_message, PROVIDER_FAILURE_REPLY, traces, f"provider:{exc}")
        return self._fail(user_message, INCOMPLETE_REPLY, traces, "tool_round_limit")

    def _fail(
        self, user_message: dict, text: str, traces: list[ToolTrace], failure: str
    ) -> AgentReply:
        """End the turn with a fixed message, dropping partial tool exchanges from history."""
        self._turns.append([user_message, {"role": "assistant", "content": text}])
        return AgentReply(text, traces, failure)
