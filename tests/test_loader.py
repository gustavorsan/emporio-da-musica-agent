"""Data import: fidelity to the sources, repeatability and failure behavior."""

from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from emporio import config
from emporio.db import connect_readonly, sources_changed
from emporio.loader import LoadError, build_database, find_csv

EXPECTED_COUNTS = {
    "categories": 9,
    "products": 65,
    "promotions": 25,
    "customers": 50,
    "orders": 20,
    "order_items": 22,
}


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_counts_match_sources(conn):
    assert {table: _count(conn, table) for table in EXPECTED_COUNTS} == EXPECTED_COUNTS


def test_rebuild_does_not_duplicate(tmp_path):
    path = tmp_path / "again.db"
    assert build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, path) == EXPECTED_COUNTS
    assert build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, path) == EXPECTED_COUNTS


def test_locked_database_reports_a_clear_error_and_leaves_no_temp_file(tmp_path, monkeypatch):
    # Windows refuses to replace a file while a chat session holds it open.
    path = tmp_path / "locked.db"
    build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, path)
    published = path.read_bytes()

    def refuse(source, target):
        raise PermissionError("in use")

    monkeypatch.setattr("emporio.loader.os.replace", refuse)
    with pytest.raises(LoadError, match="sessões de chat"):
        build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, path)
    assert path.read_bytes() == published
    assert [item.name for item in tmp_path.iterdir()] == ["locked.db"]


def test_no_orphan_references(conn):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_money_is_stored_in_exact_cents(conn):
    price = conn.execute("SELECT price_cents FROM products WHERE product_id = 81").fetchone()[0]
    assert price == 59990
    total = conn.execute("SELECT total_cents FROM orders WHERE order_id = 4").fetchone()[0]
    assert total == 79990


@pytest.mark.parametrize("order_id, cents", [(3, 345000), (20, 140000)])
def test_historical_order_totals_are_preserved(conn, order_id, cents):
    row = conn.execute("SELECT total_cents FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    assert row[0] == cents


def test_orders_with_installments_below_current_minimum_still_load(conn):
    method = conn.execute("SELECT payment_method FROM orders WHERE order_id = 9").fetchone()[0]
    assert method == "credit_6x"


def test_empty_optional_fields_become_null(conn):
    row = conn.execute(
        "SELECT tracking_code, estimated_delivery, notes FROM orders WHERE order_id = 9"
    ).fetchone()
    assert tuple(row) == (None, None, None)


def test_specs_are_valid_json_objects(conn):
    for row in conn.execute("SELECT specs_json FROM products"):
        assert isinstance(json.loads(row[0]), dict)


def test_service_connection_cannot_write(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM customers")


def _copy_sources(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for table in EXPECTED_COUNTS:
        shutil.copy(find_csv(config.DATA_DIR, table), data_dir)
    return data_dir


def test_failed_load_preserves_previous_database(tmp_path):
    data_dir = _copy_sources(tmp_path)
    path = tmp_path / "published.db"
    build_database(data_dir, config.KNOWLEDGE_DIR, path)

    products = find_csv(data_dir, "products")
    products.write_text(
        products.read_text("utf-8").replace("599.9,Yamaha C40", "abc,Yamaha C40"), "utf-8"
    )
    with pytest.raises(LoadError, match="price_brl"):
        build_database(data_dir, config.KNOWLEDGE_DIR, path)

    conn = connect_readonly(path)
    assert _count(conn, "products") == 65
    conn.close()
    assert [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name] == []


def test_broken_reference_is_rejected(tmp_path):
    data_dir = _copy_sources(tmp_path)
    items = find_csv(data_dir, "order_items")
    items.write_text(items.read_text("utf-8") + "1,1,99999\n", "utf-8")
    with pytest.raises(LoadError):
        build_database(data_dir, config.KNOWLEDGE_DIR, tmp_path / "new.db")
    assert not (tmp_path / "new.db").exists()


def test_detects_changed_sources(tmp_path):
    data_dir = _copy_sources(tmp_path)
    path = tmp_path / "fresh.db"
    build_database(data_dir, config.KNOWLEDGE_DIR, path)
    conn = connect_readonly(path)
    assert not sources_changed(conn, data_dir, config.KNOWLEDGE_DIR)
    orders = find_csv(data_dir, "orders")
    orders.write_text(orders.read_text("utf-8") + "\n", "utf-8")
    assert sources_changed(conn, data_dir, config.KNOWLEDGE_DIR)
    conn.close()
