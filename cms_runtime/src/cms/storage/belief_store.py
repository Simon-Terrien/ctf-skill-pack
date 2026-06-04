"""
BeliefStore — persistence for ProfileBelief records.

Same dumb-store pattern as the other stores. CRUD + status/dimension
filtering. No business logic.

Upsert semantics: beliefs are mutated in place over their lifecycle
(value, confidence, stability, status, ledger all evolve), so this
store uses INSERT OR REPLACE on belief_id and a UNIQUE constraint on
(user_id, dimension) to prevent duplicate dimensions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from cms.l3.belief import ProfileBelief
from cms.storage.base import StorageBackend


class BeliefStore:
    """CRUD store for ProfileBelief records."""

    _COLUMNS = (
        "belief_id", "user_id", "dimension",
        "value", "confidence", "stability",
        "status", "created_at", "updated_at",
        "supporting_memory_ids_json", "counterevidence_ids_json",
        "context_key",
        "metadata_json",
    )
    _PLACEHOLDERS = ", ".join(["?"] * len(_COLUMNS))
    _COL_LIST = ", ".join(_COLUMNS)

    def __init__(self, backend: StorageBackend):
        self._backend = backend

    # ── write ───────────────────────────────────────────────────────

    def upsert(self, belief: ProfileBelief) -> None:
        """Insert or update a belief.

        Beliefs evolve in place — their value, confidence, status, and
        ledger all change over their lifetime. We need:
          - update semantics for an existing belief_id (lifecycle changes)
          - insert semantics for a new belief_id
          - hard failure when a NEW belief_id collides with an existing
            (user_id, dimension) — that's a programming error, not an upsert

        INSERT OR REPLACE silently swallows UNIQUE conflicts, which would
        mask the third case. So we route explicitly.
        """
        if self._exists(belief.belief_id):
            self._update(belief)
        else:
            self._insert(belief)

    def upsert_many(self, beliefs: Iterable[ProfileBelief]) -> int:
        count = 0
        for belief in beliefs:
            self.upsert(belief)
            count += 1
        return count

    def _exists(self, belief_id: str) -> bool:
        row = self._backend.fetch_one(
            "SELECT 1 FROM profile_beliefs WHERE belief_id = ? LIMIT 1",
            (belief_id,),
        )
        return row is not None

    def _insert(self, belief: ProfileBelief) -> None:
        """Plain INSERT — UNIQUE on (user_id, dimension) fires on conflict."""
        sql = (
            f"INSERT INTO profile_beliefs ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        self._backend.execute(sql, self._to_row(belief))
        self._backend.commit()

    def _update(self, belief: ProfileBelief) -> None:
        """In-place UPDATE for an existing belief_id."""
        set_clause = ", ".join(f"{c} = ?" for c in self._COLUMNS if c != "belief_id")
        non_id_values = tuple(
            v for c, v in zip(self._COLUMNS, self._to_row(belief)) if c != "belief_id"
        )
        sql = f"UPDATE profile_beliefs SET {set_clause} WHERE belief_id = ?"
        self._backend.execute(sql, non_id_values + (belief.belief_id,))
        self._backend.commit()

    # ── read ────────────────────────────────────────────────────────

    def get(self, belief_id: str) -> ProfileBelief | None:
        sql = f"SELECT {self._COL_LIST} FROM profile_beliefs WHERE belief_id = ?"
        row = self._backend.fetch_one(sql, (belief_id,))
        return self._from_row(row) if row else None

    def get_for_user_dimension(
        self, user_id: str, dimension: str, context_key: str | None = None,
    ) -> ProfileBelief | None:
        """Fetch the belief for (user, dimension, context_key).

        context_key=None retrieves the global belief (one per dimension).
        context_key="research" retrieves the scoped-to-research belief
        in that dimension. Block 6 uniqueness guarantees at most one
        belief per (user_id, dimension, context_key).
        """
        if context_key is None:
            sql = (
                f"SELECT {self._COL_LIST} FROM profile_beliefs "
                f"WHERE user_id = ? AND dimension = ? AND context_key IS NULL"
            )
            row = self._backend.fetch_one(sql, (user_id, dimension))
        else:
            sql = (
                f"SELECT {self._COL_LIST} FROM profile_beliefs "
                f"WHERE user_id = ? AND dimension = ? AND context_key = ?"
            )
            row = self._backend.fetch_one(sql, (user_id, dimension, context_key))
        return self._from_row(row) if row else None

    def list_for_user_dimension(
        self, user_id: str, dimension: str,
    ) -> list[ProfileBelief]:
        """All beliefs for a dimension, across all contexts (including global).

        Returns global first (context_key NULL), then scoped beliefs
        ordered by context_key. Useful for reconciling global vs scoped
        views of the same dimension — they coexist per guardrail B.
        """
        sql = (
            f"SELECT {self._COL_LIST} FROM profile_beliefs "
            f"WHERE user_id = ? AND dimension = ? "
            f"ORDER BY context_key IS NOT NULL, context_key ASC"
        )
        rows = self._backend.fetch_all(sql, (user_id, dimension))
        return [self._from_row(r) for r in rows]

    def list_global(self, user_id: str) -> list[ProfileBelief]:
        """Beliefs where context_key is None."""
        sql = (
            f"SELECT {self._COL_LIST} FROM profile_beliefs "
            f"WHERE user_id = ? AND context_key IS NULL "
            f"ORDER BY dimension ASC"
        )
        rows = self._backend.fetch_all(sql, (user_id,))
        return [self._from_row(r) for r in rows]

    def list_scoped(self, user_id: str) -> list[ProfileBelief]:
        """Beliefs where context_key is not None."""
        sql = (
            f"SELECT {self._COL_LIST} FROM profile_beliefs "
            f"WHERE user_id = ? AND context_key IS NOT NULL "
            f"ORDER BY context_key ASC, dimension ASC"
        )
        rows = self._backend.fetch_all(sql, (user_id,))
        return [self._from_row(r) for r in rows]

    def list_for_user(
        self, user_id: str, *, limit: int = 1000
    ) -> list[ProfileBelief]:
        sql = (
            f"SELECT {self._COL_LIST} FROM profile_beliefs "
            f"WHERE user_id = ? ORDER BY dimension ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, limit))
        return [self._from_row(r) for r in rows]

    def list_by_status(
        self, user_id: str, status: str, *, limit: int = 1000
    ) -> list[ProfileBelief]:
        sql = (
            f"SELECT {self._COL_LIST} FROM profile_beliefs "
            f"WHERE user_id = ? AND status = ? "
            f"ORDER BY dimension ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, status, limit))
        return [self._from_row(r) for r in rows]

    def list_active(self, user_id: str) -> list[ProfileBelief]:
        return self.list_by_status(user_id, "active")

    def list_tentative(self, user_id: str) -> list[ProfileBelief]:
        return self.list_by_status(user_id, "tentative")

    def list_stale(self, user_id: str) -> list[ProfileBelief]:
        return self.list_by_status(user_id, "stale")

    def list_invalidated(self, user_id: str) -> list[ProfileBelief]:
        return self.list_by_status(user_id, "invalidated")

    def count_for_user(self, user_id: str) -> int:
        sql = "SELECT COUNT(*) FROM profile_beliefs WHERE user_id = ?"
        row = self._backend.fetch_one(sql, (user_id,))
        return int(row[0]) if row else 0

    # ── delete ──────────────────────────────────────────────────────

    def delete(self, belief_id: str) -> None:
        self._backend.execute(
            "DELETE FROM profile_beliefs WHERE belief_id = ?", (belief_id,)
        )
        self._backend.commit()

    def delete_for_user(self, user_id: str) -> int:
        count = self.count_for_user(user_id)
        self._backend.execute(
            "DELETE FROM profile_beliefs WHERE user_id = ?", (user_id,)
        )
        self._backend.commit()
        return count

    # ── (de)serialization ───────────────────────────────────────────

    @staticmethod
    def _to_row(belief: ProfileBelief) -> tuple:
        return (
            belief.belief_id,
            belief.user_id,
            belief.dimension,
            belief.value,
            belief.confidence,
            belief.stability,
            belief.status,
            belief.created_at.isoformat(),
            belief.updated_at.isoformat(),
            json.dumps(belief.supporting_memory_ids),
            json.dumps(belief.counterevidence_ids),
            belief.context_key,
            json.dumps(belief.metadata),
        )

    @staticmethod
    def _from_row(row: tuple) -> ProfileBelief:
        return ProfileBelief(
            belief_id=row[0],
            user_id=row[1],
            dimension=row[2],
            value=row[3],
            confidence=row[4],
            stability=row[5],
            status=row[6],
            created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
            supporting_memory_ids=json.loads(row[9]),
            counterevidence_ids=json.loads(row[10]),
            context_key=row[11],
            metadata=json.loads(row[12]),
        )
