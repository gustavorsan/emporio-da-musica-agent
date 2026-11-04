"""Return and exchange deadlines from manual sections 4 and 8.1.

Counting calendar days is arithmetic, so it happens here and the model only reports the result.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Literal

from emporio.catalog import ToolInputError
from emporio.orders import IDENTIFICATION_REQUIRED, NOT_ACCESSIBLE, find_owned_order

Reason = Literal["regret", "defect", "preference"]
Channel = Literal["online", "store"]

REGRET_DAYS = 7
DEFECT_DAYS = 30
PREFERENCE_DAYS = 7
LEGAL_WARRANTY_DAYS = 90

_CONDITIONS_NOTE = (
    "O prazo é só uma das condições. Consulte search_knowledge para o estado do produto, a embalagem "
    "e os itens não elegíveis. Esta ferramenta não abre nenhuma solicitação."
)


def _format(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _window(start: date, days: int, today: date) -> dict:
    deadline = start + timedelta(days=days)
    remaining = (deadline - today).days
    return {
        "counted_from": _format(start),
        "deadline": _format(deadline),
        "days_elapsed": (today - start).days,
        "days_remaining": max(remaining, 0),
        "open": remaining >= 0,
        # The deadline day still counts, which the customer needs to act on today.
        "last_day": remaining == 0,
    }


def _result(window: dict) -> str:
    return "within_window" if window["open"] else "window_closed"


def _needs(*missing: str) -> dict:
    return {
        "result": "needs_information",
        "missing": list(missing),
        "guidance": "Pergunte ao cliente o que falta, sem estimar datas. Previsão de entrega não serve como recebimento.",
    }


def _regret(today: date, channel: Channel | None, purchase: date | None, received: date | None) -> dict:
    missing = [
        name
        for name, value in (("purchase_channel", channel), ("received_date", received))
        if value is None
    ]
    if channel == "store":
        return {
            "result": "not_applicable",
            "guidance": (
                "O direito de arrependimento da seção 4.1 é para compras online. Em loja física, veja as "
                "trocas por preferência (4.3) ou por defeito (4.2) e oriente a confirmação com a equipe."
            ),
        }
    if missing:
        payload = _needs(*missing)
        # Only the channel is missing: give the count, so the model has no reason to do it by hand.
        if received is not None:
            payload["if_online"] = _window(received, REGRET_DAYS, today)
        return payload
    window = _window(received, REGRET_DAYS, today)
    return {"result": _result(window), **window}


def _defect(today: date, channel: Channel | None, purchase: date | None, received: date | None) -> dict:
    if purchase is None:
        return _needs("purchase_date")
    window = _window(purchase, DEFECT_DAYS, today)
    payload = {"result": _result(window), **window}
    # The legal warranty runs from receipt, so it can outlast the store's exchange window.
    if received is None:
        payload["legal_warranty"] = {
            "days": LEGAL_WARRANTY_DAYS,
            "counted_from": "recebimento",
            "note": "Sem a data de recebimento não dá para calcular esse prazo: pergunte-a ao cliente se ele for relevante.",
        }
    else:
        payload["legal_warranty"] = {"days": LEGAL_WARRANTY_DAYS, **_window(received, LEGAL_WARRANTY_DAYS, today)}
    if not window["open"]:
        payload["guidance"] = (
            "A troca direta pela loja terminou, mas isso não significa ausência de cobertura. Distinga a "
            "garantia legal (8.1) e a do fabricante (8.2), que varia conforme o certificado. A loja pode "
            "intermediar mediante solicitação; oriente a confirmação com a equipe."
        )
    return payload


def _preference(today: date, channel: Channel | None, purchase: date | None, received: date | None) -> dict:
    """The manual does not say when these 7 days start, so answer only what holds for either start."""
    from_purchase = _window(purchase, PREFERENCE_DAYS, today) if purchase else None
    from_receipt = _window(received, PREFERENCE_DAYS, today) if received else None
    # Receipt never precedes purchase: open from purchase implies open from receipt, and vice versa when closed.
    if from_purchase and from_purchase["open"]:
        return {"result": "within_window", **from_purchase}
    if from_receipt and not from_receipt["open"]:
        return {"result": "window_closed", **from_receipt}
    if from_purchase and from_receipt:
        return {
            "result": "depends_on_start",
            "deadline_if_from_purchase": from_purchase["deadline"],
            "deadline_if_from_receipt": from_receipt["deadline"],
            "guidance": (
                "O manual não define o início destes 7 dias, e isso muda a conclusão. Não afirme que está "
                "dentro ou fora do prazo: oriente a confirmação com a equipe."
            ),
        }
    return _needs(*[
        name for name, value in (("purchase_date", purchase), ("received_date", received)) if value is None
    ])


_RULES = {
    "regret": ("4.1", _regret),
    "defect": ("4.2", _defect),
    "preference": ("4.3", _preference),
}


def _validate_dates(today: date, purchase: date | None, received: date | None) -> None:
    for label, day in (("compra", purchase), ("recebimento", received)):
        if day is not None and day > today:
            raise ToolInputError(
                f"A data de {label} ({_format(day)}) é posterior a hoje ({_format(today)}). Confirme a data com o cliente."
            )
    if purchase is not None and received is not None and received < purchase:
        raise ToolInputError(
            f"O recebimento ({_format(received)}) é anterior à compra ({_format(purchase)}). Confirme as datas com o cliente."
        )


def check_return_window(
    conn: sqlite3.Connection,
    customer_id: int | None,
    today: date,
    reason: Reason,
    purchase_channel: Channel | None = None,
    order_id: int | None = None,
    purchase_date: date | None = None,
    received_date: date | None = None,
) -> dict:
    """State whether a date-based deadline is open; a recorded order date beats a stated one."""
    section, rule = _RULES[reason]
    record_note = None
    if order_id is not None:
        if customer_id is None:
            return dict(IDENTIFICATION_REQUIRED)
        order = find_owned_order(conn, customer_id, order_id)
        if order is None:
            return dict(NOT_ACCESSIBLE)
        if order["status"] == "cancelled":
            return {
                "result": "order_cancelled",
                "policy_section": section,
                "guidance": "O pedido consta como cancelado, então não há prazo a calcular. Oriente a confirmação com a equipe.",
            }
        purchase_date = date.fromisoformat(order["order_date"])
        if order["status"] != "delivered":
            record_note = (
                "O registro do pedido ainda não indica entrega. Relate o status registrado e oriente a "
                "confirmação com a equipe, sem afirmar nem negar o recebimento."
            )
    _validate_dates(today, purchase_date, received_date)
    payload = {
        "today": _format(today),
        "reason": reason,
        "policy_section": section,
        **rule(today, purchase_channel, purchase_date, received_date),
        "conditions_note": _CONDITIONS_NOTE,
    }
    if record_note:
        payload["record_note"] = record_note
    return payload
