"""Two-tier memory.

Working memory is pluggable: Redis for distributed runs, in-memory for local
single-process smoke tests. Long-term memory is left as a Protocol so it can be
wired to MemPalace later without changing runtime contracts.
"""
from __future__ import annotations

import json
import os
from typing import Optional, Protocol

from .config import settings
from .contracts import Candidate, Hypothesis


class MemoryProtocol(Protocol):
    async def set_board(self, challenge_id: str, board: dict) -> None: ...
    async def get_board(self, challenge_id: str) -> dict: ...
    async def upsert_hypothesis(self, h: Hypothesis) -> None: ...
    async def list_hypotheses(self, challenge_id: str) -> list[Hypothesis]: ...
    async def reject_path(self, challenge_id: str, signature: str) -> None: ...
    async def is_rejected(self, challenge_id: str, signature: str) -> bool: ...
    async def record_candidate(self, c: Candidate) -> None: ...
    async def close(self) -> None: ...


class RedisWorkingMemory:
    def __init__(self, redis_url: Optional[str] = None):
        import redis.asyncio as aioredis  # lazy
        self._r = aioredis.from_url(redis_url or settings.redis_url)
        self._ttl = settings.working_ttl_s

    async def set_board(self, challenge_id: str, board: dict) -> None:
        await self._r.set(f"ctf:{challenge_id}:board", json.dumps(board), ex=self._ttl)

    async def get_board(self, challenge_id: str) -> dict:
        raw = await self._r.get(f"ctf:{challenge_id}:board")
        return json.loads(raw) if raw else {}

    async def upsert_hypothesis(self, h: Hypothesis) -> None:
        await self._r.hset(f"ctf:{h.challenge_id}:hyp", h.id, h.model_dump_json())
        await self._r.expire(f"ctf:{h.challenge_id}:hyp", self._ttl)

    async def list_hypotheses(self, challenge_id: str) -> list[Hypothesis]:
        d = await self._r.hgetall(f"ctf:{challenge_id}:hyp")
        return [Hypothesis.model_validate_json(v) for v in d.values()]

    async def reject_path(self, challenge_id: str, signature: str) -> None:
        await self._r.sadd(f"ctf:{challenge_id}:rejected", signature)
        await self._r.expire(f"ctf:{challenge_id}:rejected", self._ttl)

    async def is_rejected(self, challenge_id: str, signature: str) -> bool:
        return bool(await self._r.sismember(f"ctf:{challenge_id}:rejected", signature))

    async def record_candidate(self, c: Candidate) -> None:
        await self._r.hset(f"ctf:{c.challenge_id}:cand", c.id, c.model_dump_json())
        await self._r.expire(f"ctf:{c.challenge_id}:cand", self._ttl)

    async def close(self) -> None:
        await self._r.aclose()


class InMemoryWorkingMemory:
    def __init__(self):
        self.boards: dict[str, dict] = {}
        self.hypotheses: dict[str, dict[str, Hypothesis]] = {}
        self.rejected: dict[str, set[str]] = {}
        self.candidates: dict[str, dict[str, Candidate]] = {}

    async def set_board(self, challenge_id: str, board: dict) -> None:
        self.boards[challenge_id] = dict(board)

    async def get_board(self, challenge_id: str) -> dict:
        return dict(self.boards.get(challenge_id, {}))

    async def upsert_hypothesis(self, h: Hypothesis) -> None:
        self.hypotheses.setdefault(h.challenge_id, {})[h.id] = h

    async def list_hypotheses(self, challenge_id: str) -> list[Hypothesis]:
        return list(self.hypotheses.get(challenge_id, {}).values())

    async def reject_path(self, challenge_id: str, signature: str) -> None:
        self.rejected.setdefault(challenge_id, set()).add(signature)

    async def is_rejected(self, challenge_id: str, signature: str) -> bool:
        return signature in self.rejected.get(challenge_id, set())

    async def record_candidate(self, c: Candidate) -> None:
        self.candidates.setdefault(c.challenge_id, {})[c.id] = c

    async def close(self) -> None:
        return None


# Backward-compatible name. Redis is no longer the default for local runs.
WorkingMemory = RedisWorkingMemory


def make_working_memory() -> MemoryProtocol:
    mode = os.getenv("CTF_MEMORY", "").strip().lower()
    if mode == "redis":
        return RedisWorkingMemory()
    if mode == "memory":
        return InMemoryWorkingMemory()
    return RedisWorkingMemory() if os.getenv("CTF_REDIS") else InMemoryWorkingMemory()


class LongTermMemory(Protocol):
    """Wire to MemPalace. retrieve() is called at triage; consolidate() by the
    post-solve step (v2)."""
    async def retrieve(self, signals: list[str], k: int = 5) -> list[dict]: ...
    async def consolidate(self, challenge_id: str, lesson: dict) -> None: ...


class NullLongTermMemory:
    """Default no-op so the runtime boots without MemPalace attached."""
    async def retrieve(self, signals: list[str], k: int = 5) -> list[dict]:
        return []

    async def consolidate(self, challenge_id: str, lesson: dict) -> None:
        return None
