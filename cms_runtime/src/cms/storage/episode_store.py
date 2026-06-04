"""
EpisodeStore — persistence for L2Episode records.

CRUD operations only. No business logic. Same pattern as ObservationStore.

Range queries support common access patterns:
  - all episodes for a session, ordered by start_at
  - all episodes for a user across sessions
  - episodes within a time range (for retrieval-driven workflows in Block 4)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from cms.l2.episode import L2Episode
from cms.storage.base import StorageBackend


class EpisodeStore:
    """CRUD store for L2Episode records."""

    _COLUMNS = (
        "episode_id", "user_id", "session_id",
        "created_at", "start_at", "end_at",
        "obs_ids_json", "trajectory_signature_json",
        "surprise_score", "drift_score", "confidence_score",
        "closure_reason", "metadata_json",
    )
    _PLACEHOLDERS = ", ".join(["?"] * len(_COLUMNS))
    _COL_LIST = ", ".join(_COLUMNS)

    def __init__(self, backend: StorageBackend):
        self._backend = backend

    # ── write ───────────────────────────────────────────────────────

    def save(self, ep: L2Episode) -> None:
        sql = (
            f"INSERT OR REPLACE INTO episodes ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        self._backend.execute(sql, self._to_row(ep))
        self._backend.commit()

    def save_many(self, episodes: Iterable[L2Episode]) -> int:
        sql = (
            f"INSERT OR REPLACE INTO episodes ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        count = 0
        for ep in episodes:
            self._backend.execute(sql, self._to_row(ep))
            count += 1
        self._backend.commit()
        return count

    # ── read ────────────────────────────────────────────────────────

    def get(self, episode_id: str) -> L2Episode | None:
        sql = f"SELECT {self._COL_LIST} FROM episodes WHERE episode_id = ?"
        row = self._backend.fetch_one(sql, (episode_id,))
        return self._from_row(row) if row else None

    def list_for_session(
        self, user_id: str, session_id: str, *, limit: int = 1000
    ) -> list[L2Episode]:
        sql = (
            f"SELECT {self._COL_LIST} FROM episodes "
            f"WHERE user_id = ? AND session_id = ? "
            f"ORDER BY start_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, session_id, limit))
        return [self._from_row(r) for r in rows]

    def latest_for_session(
        self, user_id: str, session_id: str, *, limit: int = 3
    ) -> list[L2Episode]:
        """Return the most recent closed episodes for a session, newest first.

        "Closed" is implicit: only persisted episodes are visible to stores;
        in-memory open episodes live in EpisodeService and are not returned.
        Order is deterministic: start_at DESC, then episode_id DESC for tie-break.
        """
        sql = (
            f"SELECT {self._COL_LIST} FROM episodes "
            f"WHERE user_id = ? AND session_id = ? "
            f"ORDER BY start_at DESC, episode_id DESC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, session_id, limit))
        return [self._from_row(r) for r in rows]

    def list_for_user(
        self, user_id: str, *, limit: int = 1000
    ) -> list[L2Episode]:
        sql = (
            f"SELECT {self._COL_LIST} FROM episodes "
            f"WHERE user_id = ? ORDER BY start_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, limit))
        return [self._from_row(r) for r in rows]

    def list_in_range(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 1000,
    ) -> list[L2Episode]:
        """Episodes whose start_at falls within [start, end]."""
        sql = (
            f"SELECT {self._COL_LIST} FROM episodes "
            f"WHERE user_id = ? AND start_at >= ? AND start_at <= ? "
            f"ORDER BY start_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(
            sql, (user_id, start.isoformat(), end.isoformat(), limit)
        )
        return [self._from_row(r) for r in rows]

    def count_for_user(self, user_id: str) -> int:
        sql = "SELECT COUNT(*) FROM episodes WHERE user_id = ?"
        row = self._backend.fetch_one(sql, (user_id,))
        return int(row[0]) if row else 0

    # ── delete ──────────────────────────────────────────────────────

    def delete(self, episode_id: str) -> None:
        self._backend.execute(
            "DELETE FROM episodes WHERE episode_id = ?", (episode_id,)
        )
        self._backend.commit()

    def delete_for_user(self, user_id: str) -> int:
        count = self.count_for_user(user_id)
        self._backend.execute(
            "DELETE FROM episodes WHERE user_id = ?", (user_id,)
        )
        self._backend.commit()
        return count

    # ── (de)serialization ───────────────────────────────────────────

    @staticmethod
    def _to_row(ep: L2Episode) -> tuple:
        return (
            ep.episode_id,
            ep.user_id,
            ep.session_id,
            ep.created_at.isoformat(),
            ep.start_at.isoformat(),
            ep.end_at.isoformat(),
            json.dumps(ep.obs_ids),
            json.dumps(ep.trajectory_signature),
            ep.surprise_score,
            ep.drift_score,
            ep.confidence_score,
            ep.closure_reason,
            json.dumps(ep.metadata),
        )

    @staticmethod
    def _from_row(row: tuple) -> L2Episode:
        return L2Episode(
            episode_id=row[0],
            user_id=row[1],
            session_id=row[2],
            created_at=datetime.fromisoformat(row[3]),
            start_at=datetime.fromisoformat(row[4]),
            end_at=datetime.fromisoformat(row[5]),
            obs_ids=json.loads(row[6]),
            trajectory_signature=json.loads(row[7]),
            surprise_score=row[8],
            drift_score=row[9],
            confidence_score=row[10],
            closure_reason=row[11],
            metadata=json.loads(row[12]),
        )
