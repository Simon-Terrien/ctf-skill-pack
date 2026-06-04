"""
ObservationStore — persistence for L1Observation records.

CRUD operations only. No business logic. Stores treat the dataclass
as the source of truth for field layout.

Serialization
-------------
Lists and dicts are serialized to JSON strings in TEXT columns.
This is deliberate: we keep the storage layer dependency-free
(no SQLAlchemy, no ORMs) and accept the cost of JSON serialization
for free-form fields. For the volumes the runtime targets initially
(thousands of observations per user), this is fine.

Datetime handling
-----------------
Datetimes are stored as ISO 8601 strings (UTC). They are converted
to Python datetimes with timezone info on read. We never store naive
datetimes — the L1Observation invariants assume timezone-aware values.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from cms.l1.observation import L1Observation
from cms.storage.base import StorageBackend


class ObservationStore:
    """CRUD store for L1Observation records."""

    _COLUMNS = (
        "obs_id", "user_id", "session_id", "turn_id", "created_at",
        "raw_text", "language",
        "cms_real_json", "cms_imag_json", "temporal_phase",
        "features_json", "tags_json", "entities_json",
        "quality_json", "metadata_json",
    )
    _PLACEHOLDERS = ", ".join(["?"] * len(_COLUMNS))
    _COL_LIST = ", ".join(_COLUMNS)

    def __init__(self, backend: StorageBackend):
        self._backend = backend

    # ── write ───────────────────────────────────────────────────────

    def save(self, obs: L1Observation) -> None:
        """Insert or replace an observation."""
        sql = (
            f"INSERT OR REPLACE INTO observations ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        self._backend.execute(sql, self._to_row(obs))
        self._backend.commit()

    def save_many(self, observations: Iterable[L1Observation]) -> int:
        """Bulk insert/replace. Returns count saved."""
        sql = (
            f"INSERT OR REPLACE INTO observations ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        count = 0
        for obs in observations:
            self._backend.execute(sql, self._to_row(obs))
            count += 1
        self._backend.commit()
        return count

    # ── read ────────────────────────────────────────────────────────

    def get(self, obs_id: str) -> L1Observation | None:
        sql = f"SELECT {self._COL_LIST} FROM observations WHERE obs_id = ?"
        row = self._backend.fetch_one(sql, (obs_id,))
        return self._from_row(row) if row else None

    def list_for_session(
        self, user_id: str, session_id: str, *, limit: int = 1000
    ) -> list[L1Observation]:
        sql = (
            f"SELECT {self._COL_LIST} FROM observations "
            f"WHERE user_id = ? AND session_id = ? "
            f"ORDER BY created_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, session_id, limit))
        return [self._from_row(r) for r in rows]

    def list_for_user(
        self, user_id: str, *, limit: int = 1000
    ) -> list[L1Observation]:
        sql = (
            f"SELECT {self._COL_LIST} FROM observations "
            f"WHERE user_id = ? ORDER BY created_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, limit))
        return [self._from_row(r) for r in rows]

    def latest_for_session(
        self, user_id: str, session_id: str, *, limit: int = 5
    ) -> list[L1Observation]:
        """Return the most recent observations for a session, newest first.

        Used by retrieval to surface what the user just said. Order is
        deterministic: created_at DESC, then obs_id DESC for tie-break.
        """
        sql = (
            f"SELECT {self._COL_LIST} FROM observations "
            f"WHERE user_id = ? AND session_id = ? "
            f"ORDER BY created_at DESC, obs_id DESC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, session_id, limit))
        return [self._from_row(r) for r in rows]

    def count_for_user(self, user_id: str) -> int:
        sql = "SELECT COUNT(*) FROM observations WHERE user_id = ?"
        row = self._backend.fetch_one(sql, (user_id,))
        return int(row[0]) if row else 0

    # ── delete ──────────────────────────────────────────────────────

    def delete(self, obs_id: str) -> None:
        self._backend.execute("DELETE FROM observations WHERE obs_id = ?", (obs_id,))
        self._backend.commit()

    def delete_for_user(self, user_id: str) -> int:
        """Delete all observations for a user. Returns count deleted."""
        # SQLite's DELETE doesn't return rowcount via the protocol we use,
        # so count first then delete.
        count = self.count_for_user(user_id)
        self._backend.execute("DELETE FROM observations WHERE user_id = ?", (user_id,))
        self._backend.commit()
        return count

    # ── (de)serialization ───────────────────────────────────────────

    @staticmethod
    def _to_row(obs: L1Observation) -> tuple:
        return (
            obs.obs_id,
            obs.user_id,
            obs.session_id,
            obs.turn_id,
            obs.created_at.isoformat(),
            obs.raw_text,
            obs.language,
            json.dumps(obs.cms_real),
            json.dumps(obs.cms_imag),
            obs.temporal_phase,
            json.dumps(obs.features),
            json.dumps(obs.tags),
            json.dumps(obs.entities),
            json.dumps(obs.quality),
            json.dumps(obs.metadata),
        )

    @staticmethod
    def _from_row(row: tuple) -> L1Observation:
        return L1Observation(
            obs_id=row[0],
            user_id=row[1],
            session_id=row[2],
            turn_id=row[3],
            created_at=datetime.fromisoformat(row[4]),
            raw_text=row[5],
            language=row[6],
            cms_real=json.loads(row[7]),
            cms_imag=json.loads(row[8]),
            temporal_phase=row[9],
            features=json.loads(row[10]),
            tags=json.loads(row[11]),
            entities=json.loads(row[12]),
            quality=json.loads(row[13]),
            metadata=json.loads(row[14]),
        )
