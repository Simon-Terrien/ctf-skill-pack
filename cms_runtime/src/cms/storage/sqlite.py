"""
SQLite backend — concrete StorageBackend implementation.

Per the ADR: SQLite first, no premature distributed/vector/graph backends.

Threading note
--------------
SQLite connections are not thread-safe by default. This backend creates
one connection per instance and assumes single-threaded use within that
instance. For multi-threaded scenarios, use one backend per thread or
wrap with a connection pool.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SQLiteBackend:
    """SQLite-backed storage with WAL mode for safer concurrent reads."""

    def __init__(self, db_path: str | Path):
        """
        Parameters
        ----------
        db_path
            Filesystem path to the SQLite database. Use ":memory:" for
            an in-memory database (useful for tests).
        """
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        # isolation_level=None gives us manual transaction control via
        # explicit commit() calls. We default to autocommit-off behavior
        # by wrapping operations in BEGIN/COMMIT cycles.
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = None  # tuples, not Row objects

        # Enable WAL for concurrent reads
        self._conn.execute("PRAGMA journal_mode = WAL")
        # Foreign keys off — we manage relations explicitly via JSON ids
        self._conn.execute("PRAGMA foreign_keys = OFF")

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.execute(sql, params)

    def fetch_one(self, sql: str, params: tuple = ()) -> tuple | None:
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row

    def fetch_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── context manager support ─────────────────────────────────────

    def __enter__(self) -> "SQLiteBackend":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── schema bootstrap ────────────────────────────────────────────

    def bootstrap_schema(self, schema_sql: str) -> None:
        """Execute a multi-statement SQL script (DDL bootstrap).

        Block 6 addition: if the bootstrap looks like the full schema
        (contains the profile_beliefs DDL, v4's signature), we also
        apply the v5 migration steps. This keeps bootstrap_schema
        backward-compatible for all existing callers while ensuring
        context_key columns and widened uniqueness are always present.

        Callers that need v5 explicitly can call apply_migration_steps
        themselves.
        """
        self._conn.executescript(schema_sql)
        self.commit()
        # Auto-detect: if this bootstrap created the profile_beliefs table
        # (v4), also run v5 migration steps. This covers the case where
        # tests and scripts call bootstrap_schema(FULL_SCHEMA_DDL) without
        # knowing about v5.
        if "profile_beliefs" in schema_sql:
            from cms.storage.schema import MIGRATION_V5_STEPS
            self.apply_migration_steps(MIGRATION_V5_STEPS)

    def apply_migration_steps(
        self, steps: tuple[str, ...], *, tolerable_errors: tuple[str, ...] = (
            "duplicate column name",
        ),
    ) -> None:
        """Run each migration step independently.

        Steps that raise errors matching `tolerable_errors` are skipped
        as no-ops (idempotent re-application). Other errors propagate.

        This is the right tool for SQLite ALTER TABLE ADD COLUMN, which
        has no "IF NOT EXISTS" variant — we run it and tolerate the
        "duplicate column name" error on re-runs.
        """
        import sqlite3
        for step in steps:
            try:
                self._conn.executescript(step)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if any(tol in msg for tol in tolerable_errors):
                    continue
                raise
        self.commit()
