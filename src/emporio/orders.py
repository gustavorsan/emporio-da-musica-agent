"""Order lookups, always scoped to the customer the application identified for the session."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from emporio.pricing import format_brl

STATUS_LABELS = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

_PAYMENT_LABELS = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}
_CREDIT_RE = re.compile(r"^credit_(\d+)x$")

# Identical for foreign and missing orders, so a reply never reveals which IDs exist.
NOT_ACCESSIBLE = {
    "error": "not_found_or_not_authorized",
    "guidance": (
        "Não há pedido com esse número associado ao cliente desta sessão. Não confirme se o "
        "pedido existe nem dê detalhes: peça para conferir o número ou indique os canais da loja."
    ),
}

IDENTIFICATION_REQUIRED = {
    "error": "identification_required",
    "guidance": (
        "Esta sessão não tem cliente identificado. Peça o e-mail e o telefone do cadastro e use "
        "verify_identity. Nome, número de pedido ou afirmações não servem como identificação."
    ),
}

ALREADY_IDENTIFIED = {
    "error": "already_identified",
    "guidance": (
        "Esta sessão já tem um cliente identificado, e isso não muda durante a conversa. "
        "Para consultar outro cadastro, é preciso iniciar uma nova conversa."
    ),
}

IDENTIFICATION_LOCKED = {
    "error": "identification_locked",
    "guidance": (
        "O limite de tentativas desta conversa acabou. Não peça os dados de novo: diga que a "
        "consulta pode ser feita com a equipe pelo WhatsApp (67) 3321-4500."
    ),
}

# Not an attempt: nothing was compared, because the customer never typed these values.
CONTACT_NOT_PROVIDED = {
    "error": "contact_not_provided",
    "guidance": (
        "O cliente ainda não informou esse e-mail e esse telefone nesta conversa. Nunca invente "
        "nem complete esses dados: peça os dois ao cliente."
    ),
}

# Identical for a wrong e-mail, a wrong phone and an unknown customer, so nothing can be probed.
IDENTITY_NOT_CONFIRMED = {
    "error": "identity_not_confirmed",
    "guidance": (
        "Os dados não foram confirmados. Não diga qual deles falhou nem se algum existe no "
        "cadastro. Peça para conferir o e-mail e o telefone, ou indique os canais da loja."
    ),
}


@dataclass(frozen=True)
class Customer:
    customer_id: int
    first_name: str


def find_customer(conn: sqlite3.Connection, customer_id: int) -> Customer | None:
    """Return only what the session needs; contact fields never leave the database."""
    row = conn.execute(
        "SELECT customer_id, name FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return Customer(row["customer_id"], row["name"].split()[0]) if row else None


def _phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    # Customers may include the country code; the records only hold area code and number.
    return digits[2:] if len(digits) == 13 and digits.startswith("55") else digits


def match_customer(conn: sqlite3.Connection, email: str, phone: str) -> Customer | None:
    """Find a customer only when both fields match; one e-mail is shared by two customers."""
    rows = conn.execute(
        "SELECT customer_id, name, phone FROM customers WHERE lower(email) = ?",
        (email.strip().casefold(),),
    ).fetchall()
    digits = _phone_digits(phone)
    for row in rows:
        if digits and _phone_digits(row["phone"]) == digits:
            return Customer(row["customer_id"], row["name"].split()[0])
    return None


def _typed_by_customer(email: str, phone: str, customer_text: str) -> bool:
    """Check the values came from the customer, so the model cannot guess or reuse them."""
    typed_email = email.strip().casefold()
    typed_phone = _phone_digits(phone)
    return bool(typed_email and typed_phone) and (
        typed_email in customer_text.casefold() and typed_phone in re.sub(r"\D", "", customer_text)
    )


def verify_identity(
    conn: sqlite3.Connection,
    session_customer_id: int | None,
    attempts_left: int,
    email: str,
    phone: str,
    customer_text: str,
) -> dict:
    """Confirm a customer for the session; the reply never carries contact data."""
    if session_customer_id is not None:
        return dict(ALREADY_IDENTIFIED)
    if attempts_left <= 0:
        return dict(IDENTIFICATION_LOCKED)
    if not _typed_by_customer(email, phone, customer_text):
        return dict(CONTACT_NOT_PROVIDED)
    customer = match_customer(conn, email, phone)
    if customer is None:
        return {**IDENTITY_NOT_CONFIRMED, "attempts_left": attempts_left - 1}
    # session_customer is removed by the tool layer before the model sees this payload.
    return {"verified": True, "first_name": customer.first_name, "session_customer": customer}


def _payment_label(method: str) -> str:
    credit = _CREDIT_RE.match(method)
    if credit:
        return f"cartão de crédito em {credit.group(1)}x"
    return _PAYMENT_LABELS.get(method, method)


def _order_items(conn: sqlite3.Connection, order_id: int) -> list[dict]:
    # Items are history: current price and availability are left out on purpose.
    rows = conn.execute(
        """
        SELECT p.name, i.quantity
        FROM order_items AS i JOIN products AS p ON p.product_id = i.product_id
        WHERE i.order_id = ?
        ORDER BY p.name
        """,
        (order_id,),
    )
    return [{"name": row["name"], "quantity": row["quantity"]} for row in rows]


def _order_detail(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> dict:
    order = {
        "order_id": row["order_id"],
        "order_date": row["order_date"],
        "status": row["status"],
        "status_label": STATUS_LABELS[row["status"]],
        "total_recorded": format_brl(row["total_cents"]),
        "total_note": "Total histórico registrado no pedido; não recalcular com preços atuais.",
        "payment_method": _payment_label(row["payment_method"]),
        "items": _order_items(conn, row["order_id"]),
        "tracking_code": row["tracking_code"],
        "estimated_delivery": row["estimated_delivery"],
        # The sources only hold a forecast, so receipt is never something the data can confirm.
        "actual_delivery_date_recorded": False,
    }
    missing = [key for key in ("tracking_code", "estimated_delivery") if row[key] is None]
    if missing:
        order["missing_fields"] = missing
    if row["notes"]:
        order["notes"] = row["notes"]
    estimated = row["estimated_delivery"]
    if estimated and row["status"] == "shipped" and date.fromisoformat(estimated) < today:
        order["estimated_delivery_passed"] = True
        order["estimated_delivery_note"] = (
            "A previsão já passou e o registro não foi atualizado. Não afirme atraso nem entrega: "
            "relate o status registrado e oriente a confirmação com a equipe."
        )
    return order


def find_owned_order(conn: sqlite3.Connection, customer_id: int, order_id: int) -> sqlite3.Row | None:
    """Fetch an order only if it belongs to this customer; ownership is part of the query."""
    try:
        return conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND customer_id = ?", (order_id, customer_id)
        ).fetchone()
    except OverflowError:
        # SQLite integers stop at 64 bits, so a longer number cannot be an existing order.
        return None


def get_order_status(
    conn: sqlite3.Connection, customer_id: int | None, order_id: int | None, today: date
) -> dict:
    """Fetch one order or list the session customer's orders; ownership is part of the query."""
    if customer_id is None:
        return dict(IDENTIFICATION_REQUIRED)
    if order_id is None:
        rows = conn.execute(
            """
            SELECT order_id, order_date, status, total_cents
            FROM orders WHERE customer_id = ? ORDER BY order_date DESC
            """,
            (customer_id,),
        ).fetchall()
        return {
            "orders": [
                {
                    "order_id": row["order_id"],
                    "order_date": row["order_date"],
                    "status": row["status"],
                    "status_label": STATUS_LABELS[row["status"]],
                    "total_recorded": format_brl(row["total_cents"]),
                }
                for row in rows
            ]
        }
    row = find_owned_order(conn, customer_id, order_id)
    return _order_detail(conn, row, today) if row else dict(NOT_ACCESSIBLE)
