"""Read-only database access for the service side of the agent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from emporio.loader import fingerprint, source_files


class DatabaseNotReady(RuntimeError):
    """Raised when the generated database has not been prepared yet."""


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the database in read-only mode; as_uri() encodes spaces and accents in the path."""
    if not db_path.exists():
        raise DatabaseNotReady(
            f"Banco não encontrado em {db_path}. Execute: python -m emporio prepare"
        )
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sources_changed(conn: sqlite3.Connection, data_dir: Path, knowledge_dir: Path) -> bool:
    """Report whether the source files differ from those the database was built from."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'sources_sha256'").fetchone()
    return row is None or row["value"] != fingerprint(source_files(data_dir, knowledge_dir))
