"""
EvidenceStore — persistence for MemoryEvidence records.

CRUD + idempotency check. No business logic. Same pattern as
ObservationStore and EpisodeStore.

Key differences from the other stores:
  - has_evidence_for() supports the service-level idempotency fast path
  - list_for_source() enables Block 4+ to retrieve the evidence chain
    for a specific observation or episode
  - list_by_scope() supports scope-filtered retrieval for Block 4
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from cms.l3.evidence import MemoryEvidence
from cms.storage.base import StorageBackend


class EvidenceStore:
    """CRUD store for MemoryEvidence records with idempotency support."""

    _COLUMNS = (
        "memory_id", "user_id", "created_at",
        "source_kind", "source_id", "rule_id",
        "scope", "subscope", "tags_json",
        "summary",
        "support_score", "relevance_score",
        "feature_snapshot_json",
        "supersedes_json", "contradicted_by_json",
        "context_key",
        "metadata_json",
    )
    _PLACEHOLDERS = ", ".join(["?"] * len(_COLUMNS))
    _COL_LIST = ", ".join(_COLUMNS)

    def __init__(self, backend: StorageBackend):
        self._backend = backend

    # ── write ───────────────────────────────────────────────────────

    def save(self, record: MemoryEvidence) -> None:
        """Insert a new record.

        Uses plain INSERT (not INSERT OR REPLACE) so the schema-level
        UNIQUE constraint on (user_id, source_kind, source_id, rule_id)
        actually fires on duplicate idempotency keys. The primary
        fast-path idempotency check lives in EvidenceService; this is
        the safety backstop.

        If you need upsert semantics, use EvidenceService which checks
        has_evidence_for() before calling save().
        """
        sql = (
            f"INSERT INTO memory_evidence ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        self._backend.execute(sql, self._to_row(record))
        self._backend.commit()

    def save_many(self, records: Iterable[MemoryEvidence]) -> int:
        sql = (
            f"INSERT INTO memory_evidence ({self._COL_LIST}) "
            f"VALUES ({self._PLACEHOLDERS})"
        )
        count = 0
        for record in records:
            self._backend.execute(sql, self._to_row(record))
            count += 1
        self._backend.commit()
        return count

    # ── idempotency check ───────────────────────────────────────────

    def has_evidence_for(
        self,
        *,
        user_id: str,
        source_kind: str,
        source_id: str,
        rule_id: str,
    ) -> bool:
        """Fast-path idempotency check.

        Returns True if a record already exists with the given key.
        Used by EvidenceService to skip already-filed rule firings.
        """
        sql = (
            "SELECT 1 FROM memory_evidence "
            "WHERE user_id = ? AND source_kind = ? "
            "AND source_id = ? AND rule_id = ? LIMIT 1"
        )
        row = self._backend.fetch_one(
            sql, (user_id, source_kind, source_id, rule_id)
        )
        return row is not None

    # ── read ────────────────────────────────────────────────────────

    def get(self, memory_id: str) -> MemoryEvidence | None:
        sql = f"SELECT {self._COL_LIST} FROM memory_evidence WHERE memory_id = ?"
        row = self._backend.fetch_one(sql, (memory_id,))
        return self._from_row(row) if row else None

    def list_for_user(
        self, user_id: str, *, limit: int = 1000
    ) -> list[MemoryEvidence]:
        sql = (
            f"SELECT {self._COL_LIST} FROM memory_evidence "
            f"WHERE user_id = ? ORDER BY created_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, limit))
        return [self._from_row(r) for r in rows]

    def list_for_source(
        self,
        user_id: str,
        source_kind: str,
        source_id: str,
    ) -> list[MemoryEvidence]:
        """All evidence records produced from a specific source object.

        Useful for audit: "what evidence did this observation produce?"
        """
        sql = (
            f"SELECT {self._COL_LIST} FROM memory_evidence "
            f"WHERE user_id = ? AND source_kind = ? AND source_id = ? "
            f"ORDER BY created_at ASC"
        )
        rows = self._backend.fetch_all(sql, (user_id, source_kind, source_id))
        return [self._from_row(r) for r in rows]

    def list_by_scope(
        self,
        user_id: str,
        scope: str,
        *,
        limit: int = 1000,
    ) -> list[MemoryEvidence]:
        """All evidence records in a specific scope.

        Supports scope-based retrieval for Block 4.
        """
        sql = (
            f"SELECT {self._COL_LIST} FROM memory_evidence "
            f"WHERE user_id = ? AND scope = ? "
            f"ORDER BY created_at ASC LIMIT ?"
        )
        rows = self._backend.fetch_all(sql, (user_id, scope, limit))
        return [self._from_row(r) for r in rows]

    def search(
        self,
        user_id: str,
        *,
        scope: str | None = None,
        subscope: str | None = None,
        source_kind: str | None = None,
        context_key: str | None = None,
        match_null_context: bool = False,
        limit: int = 5,
    ) -> list[MemoryEvidence]:
        """Block 4 retrieval surface — scope/subscope/source/context filtering.

        Ordering (deterministic, locked):
            1. created_at DESC (newer first)
            2. support_score DESC (stronger first)
            3. memory_id DESC (deterministic tie-break)

        scope, subscope, source_kind, and context_key are AND-combined
        when present. If context_key is provided AND match_null_context
        is True, matches records with context_key = given_value OR NULL.
        This supports "scoped retrieval with global fallback" without
        forcing callers to run two queries.
        """
        clauses = ["user_id = ?"]
        params: list = [user_id]
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if subscope is not None:
            clauses.append("subscope = ?")
            params.append(subscope)
        if source_kind is not None:
            clauses.append("source_kind = ?")
            params.append(source_kind)
        if context_key is not None:
            if match_null_context:
                clauses.append("(context_key = ? OR context_key IS NULL)")
                params.append(context_key)
            else:
                clauses.append("context_key = ?")
                params.append(context_key)

        where = " AND ".join(clauses)
        sql = (
            f"SELECT {self._COL_LIST} FROM memory_evidence "
            f"WHERE {where} "
            f"ORDER BY created_at DESC, support_score DESC, memory_id DESC "
            f"LIMIT ?"
        )
        params.append(limit)
        rows = self._backend.fetch_all(sql, tuple(params))
        return [self._from_row(r) for r in rows]

    def find_supersession_candidates(
        self,
        user_id: str,
        rule_id: str,
        context_key: str | None,
        *,
        exclude_memory_id: str | None = None,
    ) -> list[MemoryEvidence]:
        """Find prior evidence in the same lane that a new record would supersede.

        Lane = (user_id, rule_id, context_key). Strict: None and non-None
        context_keys are different lanes. This is the refinement you
        locked — replacement is lane-scoped, not cross-lane.

        Returns newest-first so the caller can decide which of several
        prior records to mark as superseded.
        """
        clauses = ["user_id = ?", "rule_id = ?"]
        params: list = [user_id, rule_id]
        if context_key is None:
            clauses.append("context_key IS NULL")
        else:
            clauses.append("context_key = ?")
            params.append(context_key)
        if exclude_memory_id is not None:
            clauses.append("memory_id != ?")
            params.append(exclude_memory_id)

        where = " AND ".join(clauses)
        sql = (
            f"SELECT {self._COL_LIST} FROM memory_evidence "
            f"WHERE {where} "
            f"ORDER BY created_at DESC, memory_id DESC"
        )
        rows = self._backend.fetch_all(sql, tuple(params))
        return [self._from_row(r) for r in rows]

    def count_for_user(self, user_id: str) -> int:
        sql = "SELECT COUNT(*) FROM memory_evidence WHERE user_id = ?"
        row = self._backend.fetch_one(sql, (user_id,))
        return int(row[0]) if row else 0

    # ── delete ──────────────────────────────────────────────────────

    def delete(self, memory_id: str) -> None:
        self._backend.execute(
            "DELETE FROM memory_evidence WHERE memory_id = ?", (memory_id,)
        )
        self._backend.commit()

    def delete_for_user(self, user_id: str) -> int:
        count = self.count_for_user(user_id)
        self._backend.execute(
            "DELETE FROM memory_evidence WHERE user_id = ?", (user_id,)
        )
        self._backend.commit()
        return count

    # ── (de)serialization ───────────────────────────────────────────

    @staticmethod
    def _to_row(record: MemoryEvidence) -> tuple:
        return (
            record.memory_id,
            record.user_id,
            record.created_at.isoformat(),
            record.source_kind,
            record.source_id,
            record.rule_id,
            record.scope,
            record.subscope,
            json.dumps(record.tags),
            record.summary,
            record.support_score,
            record.relevance_score,
            json.dumps(record.feature_snapshot),
            json.dumps(record.supersedes),
            json.dumps(record.contradicted_by),
            record.context_key,
            json.dumps(record.metadata),
        )

    @staticmethod
    def _from_row(row: tuple) -> MemoryEvidence:
        return MemoryEvidence(
            memory_id=row[0],
            user_id=row[1],
            created_at=datetime.fromisoformat(row[2]),
            source_kind=row[3],  # type: ignore[arg-type]
            source_id=row[4],
            rule_id=row[5],
            scope=row[6],
            subscope=row[7],
            tags=json.loads(row[8]),
            summary=row[9],
            support_score=row[10],
            relevance_score=row[11],
            feature_snapshot=json.loads(row[12]),
            supersedes=json.loads(row[13]),
            contradicted_by=json.loads(row[14]),
            context_key=row[15],
            metadata=json.loads(row[16]),
        )
