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
        "Esta sessão não tem cliente identificado, então nenhum pedido pode ser consultado. "
        "Número de pedido, nome, e-mail ou telefone informados na conversa não servem como "
        "identificação: indique os canais da loja."
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
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ? AND customer_id = ?", (order_id, customer_id)
    ).fetchone()
    return _order_detail(conn, row, today) if row else dict(NOT_ACCESSIBLE)
