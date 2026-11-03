"""Validated, atomic import of the source files into a fresh SQLite database."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Callable, Iterable

PAYMENT_METHOD_RE = re.compile(r"^(pix|debit|boleto|credit_(?:[1-9]|1[0-2])x)$")


class LoadError(RuntimeError):
    """Raised when a source file fails validation; no database is published."""


def _optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _required(value: str | None, field: str) -> str:
    cleaned = _optional(value)
    if cleaned is None:
        raise ValueError(f"{field} is required")
    return cleaned


def _integer(value: str | None, field: str) -> int:
    try:
        return int(_required(value, field))
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer: {value!r}") from exc


def _cents(value: str | None, field: str) -> int:
    try:
        amount = Decimal(_required(value, field))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal amount: {value!r}") from exc
    cents = amount * 100
    if not amount.is_finite() or cents != cents.to_integral_value():
        raise ValueError(f"{field} has more than two decimal places: {value!r}")
    return int(cents)


def _iso_date(value: str | None, field: str) -> str:
    text = _required(value, field)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date: {value!r}") from exc
    return text


def _discount(value: str | None) -> str:
    text = _required(value, "discount_percent")
    try:
        percent = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"discount_percent must be numeric: {value!r}") from exc
    if not 0 < percent < 100:
        raise ValueError(f"discount_percent out of range: {value!r}")
    return text


def _flag(value: str | None) -> int:
    text = _required(value, "is_active")
    if text not in {"0", "1"}:
        raise ValueError(f"is_active must be 0 or 1: {value!r}")
    return int(text)


def _specs(value: str | None) -> str:
    specs = json.loads(_required(value, "specs"))
    if not isinstance(specs, dict):
        raise ValueError("specs must be a JSON object")
    return json.dumps(specs, ensure_ascii=False, sort_keys=True)


def _payment_method(value: str | None) -> str:
    text = _required(value, "payment_method")
    if not PAYMENT_METHOD_RE.match(text):
        raise ValueError(f"unknown payment_method: {value!r}")
    return text


def _category(row: dict) -> tuple:
    return (
        _integer(row["category_id"], "category_id"),
        _required(row["name"], "name"),
        _optional(row["description"]),
    )


def _product(row: dict) -> tuple:
    return (
        _integer(row["product_id"], "product_id"),
        _required(row["name"], "name"),
        _cents(row["price_brl"], "price_brl"),
        _integer(row["category_id"], "category_id"),
        _optional(row["description"]),
        _integer(row["stock_quantity"], "stock_quantity"),
        _required(row["status"], "status"),
        _specs(row["specs"]),
        _iso_date(row["created_at"], "created_at"),
    )


def _promotion(row: dict) -> tuple:
    return (
        _integer(row["promotion_id"], "promotion_id"),
        _integer(row["product_id"], "product_id"),
        _discount(row["discount_percent"]),
        _optional(row["description"]),
        _flag(row["is_active"]),
    )


def _customer(row: dict) -> tuple:
    return (
        _integer(row["customer_id"], "customer_id"),
        _required(row["name"], "name"),
        _optional(row["phone"]),
        _optional(row["email"]),
        _optional(row["city"]),
    )


def _order(row: dict) -> tuple:
    estimated = _optional(row["estimated_delivery"])
    return (
        _integer(row["order_id"], "order_id"),
        _integer(row["customer_id"], "customer_id"),
        _iso_date(row["order_date"], "order_date"),
        _required(row["status"], "status"),
        _cents(row["total_brl"], "total_brl"),
        # Recorded installments are history: some predate the current minimums, so only
        # the format is validated here, never the installment value.
        _payment_method(row["payment_method"]),
        _optional(row["tracking_code"]),
        _iso_date(estimated, "estimated_delivery") if estimated else None,
        _optional(row["notes"]),
    )


def _order_item(row: dict) -> tuple:
    return (
        _integer(row["order_id"], "order_id"),
        _integer(row["product_id"], "product_id"),
        _integer(row["quantity"], "quantity"),
    )


# Ordered so that referenced tables load before the tables pointing at them.
TABLES: tuple[tuple[str, Callable[[dict], tuple], int], ...] = (
    ("categories", _category, 3),
    ("products", _product, 9),
    ("promotions", _promotion, 5),
    ("customers", _customer, 5),
    ("orders", _order, 9),
    ("order_items", _order_item, 3),
)


def find_csv(data_dir: Path, table: str) -> Path:
    """Locate a table's CSV by suffix, which accepts the provided file names as they are."""
    matches = sorted(data_dir.glob(f"*{table}.csv"))
    if len(matches) != 1:
        raise LoadError(f"Expected one CSV for '{table}' in {data_dir}, found {len(matches)}")
    return matches[0]


def _read_rows(path: Path, parse: Callable[[dict], tuple]) -> list[tuple]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = []
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                rows.append(parse(row))
            except (KeyError, ValueError) as exc:
                raise LoadError(f"{path.name}, line {line}: {exc}") from exc
    return rows


def source_files(data_dir: Path, knowledge_dir: Path) -> list[Path]:
    csvs = [find_csv(data_dir, table) for table, _, _ in TABLES]
    return csvs + sorted(knowledge_dir.glob("*.md"))


def fingerprint(paths: Iterable[Path]) -> str:
    """Hash file names and contents so stale databases can be detected at startup."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _populate(conn: sqlite3.Connection, data_dir: Path, knowledge_dir: Path) -> None:
    conn.executescript(resources.files("emporio").joinpath("schema.sql").read_text("utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        for table, parse, columns in TABLES:
            rows = _read_rows(find_csv(data_dir, table), parse)
            placeholders = ", ".join("?" * columns)
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("sources_sha256", fingerprint(source_files(data_dir, knowledge_dir))),
                ("loaded_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ],
        )
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise LoadError(f"Foreign key violations found: {violations[:5]}")


def build_database(data_dir: Path, knowledge_dir: Path, db_path: Path) -> dict[str, int]:
    """Build a new database beside the target and swap it in only after full validation.

    A failed import leaves any previously published database untouched. Callers must
    close their service connections first, since Windows cannot replace an open file.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(f"{db_path.name}.tmp-{os.getpid()}")
    temp_path.unlink(missing_ok=True)
    conn = sqlite3.connect(temp_path)
    try:
        _populate(conn, data_dir, knowledge_dir)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table, _, _ in TABLES
        }
    except sqlite3.Error as exc:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise LoadError(f"Database constraint failed: {exc}") from exc
    except Exception:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(temp_path, db_path)
    return counts
