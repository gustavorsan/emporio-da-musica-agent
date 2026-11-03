"""Shared fixtures: one database built from the real source files per test session."""

from __future__ import annotations

import sqlite3

import pytest

from emporio import config
from emporio.db import connect_readonly
from emporio.loader import build_database


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "emporio.db"
    build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, path)
    return path


@pytest.fixture
def conn(db_path):
    connection: sqlite3.Connection = connect_readonly(db_path)
    yield connection
    connection.close()
