"""
Storage base — connection abstraction.

The runtime is consumer-neutral, but storage is also backend-neutral.
The runtime depends on store *interfaces* (protocols), not on SQLite
or any specific database.

Per the ADR, SQLite is the first concrete backend. Other backends
(Postgres, etc.) can be added later by implementing the same protocols.
"""

from __future__ import annotations

from typing import Protocol


class StorageBackend(Protocol):
    """Minimal contract a storage backend must satisfy.

    Concrete backends (SQLiteBackend, PostgresBackend, etc.) implement
    this protocol. Stores (ObservationStore, etc.) consume backends
    through this interface.
    """

    def execute(self, sql: str, params: tuple = ()) -> None: ...
    def fetch_one(self, sql: str, params: tuple = ()) -> tuple | None: ...
    def fetch_all(self, sql: str, params: tuple = ()) -> list[tuple]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...
