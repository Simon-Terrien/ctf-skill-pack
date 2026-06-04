"""Synchronous shared-service tools.

researcher and deepsearcher are called mid-solve by a specialist that blocks on
the answer, so they are in-process tools, not bus agents. They return the locked
ResearchResult contract. The actual retrieval (RAG corpus, web, docs) is wired
behind the `search` callables — point them at your stack.
"""
from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Awaitable, Callable, Optional

from .contracts import Confidence, Evidence, ResearchResult

# A retrieval backend: (query) -> list of (source, type, text, reliability)
SearchFn = Callable[[str], Awaitable[list[tuple[str, str, str, str]]]]
TraceFn = Callable[[str, dict], Awaitable[None]]


async def _null_search(query: str) -> list[tuple[str, str, str, str]]:
    return []


def _tokenize_query(query: str) -> list[str]:
    return [token.lower() for token in query.split() if token.strip()]


async def _search_directory(query: str, root: str | None, source_type: str) -> list[tuple[str, str, str, str]]:
    if not root:
        return []
    base = Path(root)
    if not base.exists():
        return []
    tokens = _tokenize_query(query)
    hits: list[tuple[str, str, str, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        if tokens and not all(token in low for token in tokens):
            continue
        snippet = text.strip().replace("\n", " ")[:280]
        reliability = "medium" if source_type == "local_notes" else "low"
        hits.append((str(path), source_type, snippet, reliability))
    return hits


async def local_notes_search(query: str) -> list[tuple[str, str, str, str]]:
    return await _search_directory(query, os.getenv("CTF_NOTES_DIR"), "local_notes")


async def writeup_search(query: str) -> list[tuple[str, str, str, str]]:
    return await _search_directory(query, os.getenv("CTF_WRITEUPS_DIR"), "writeup")


async def composed_local_search(query: str) -> list[tuple[str, str, str, str]]:
    hits = await local_notes_search(query)
    if hits:
        return hits
    return await writeup_search(query)


def make_researcher(trace: TraceFn | None = None) -> "Researcher":
    return Researcher(local_search=composed_local_search, trace=trace)


class Researcher:
    """Fast, single-pass, 1-3 queries. Escalates to deepsearcher, never loops."""

    def __init__(self, local_search: SearchFn = _null_search, web_search: SearchFn = _null_search,
                 trace: TraceFn | None = None):
        self._local = local_search
        self._web = web_search
        self._trace = trace

    def bind_trace(self, trace: TraceFn | None) -> "Researcher":
        self._trace = trace
        return self

    async def _emit(self, kind: str, payload: dict) -> None:
        if self._trace is not None:
            await self._trace(kind, payload)

    async def lookup(self, question: str, tokens: Optional[list[str]] = None) -> ResearchResult:
        tokens = tokens or [question]
        started = time.time()
        await self._emit("tool_call_started", {
            "tool": "researcher.lookup",
            "question": question,
            "tokens": tokens[:3],
        })
        # source priority: local corpus first, then web — do not web-search what's local
        try:
            hits: list[tuple[str, str, str, str]] = []
            for t in tokens[:3]:
                hits += await self._local(t)
                if not hits:
                    hits += await self._web(t)
                if hits:
                    break

            if not hits:
                result = ResearchResult(
                    original_question=question, extracted_tokens=tokens,
                    short_answer="", actionable_extract="",
                    confidence=Confidence.low, handoff_needed=True,
                    handoff_reason="no convergence in <=3 queries",
                )
                await self._emit("tool_call_finished", {
                    "tool": "researcher.lookup",
                    "ok": True,
                    "duration_ms": round((time.time() - started) * 1000, 2),
                    "hits": 0,
                    "handoff_needed": True,
                })
                return result

            src, typ, text, rel = hits[0]
            result = ResearchResult(
                original_question=question,
                extracted_tokens=tokens,
                short_answer=text[:280],
                actionable_extract=text[:280],
                confidence=Confidence(rel) if rel in ("low", "medium", "high") else Confidence.medium,
                evidence=[Evidence(source=src, type=typ, reliability=Confidence.medium)],
            )
            await self._emit("tool_call_finished", {
                "tool": "researcher.lookup",
                "ok": True,
                "duration_ms": round((time.time() - started) * 1000, 2),
                "hits": len(hits),
            })
            return result
        except Exception as exc:
            await self._emit("tool_call_failed", {
                "tool": "researcher.lookup",
                "ok": False,
                "duration_ms": round((time.time() - started) * 1000, 2),
                "error": repr(exc),
            })
            raise


class DeepSearcher:
    """Iterative multi-hop with an evidence ledger and a hard round budget.
    Never fabricates to close a gap; returns partial synthesis with explicit gaps."""

    def __init__(self, web_search: SearchFn = _null_search, max_rounds: int = 5):
        self._web = web_search
        self._max_rounds = max_rounds
        self._trace: TraceFn | None = None

    def bind_trace(self, trace: TraceFn | None) -> "DeepSearcher":
        self._trace = trace
        return self

    async def _emit(self, kind: str, payload: dict) -> None:
        if self._trace is not None:
            await self._trace(kind, payload)

    async def investigate(self, goal: str, prior_failed: Optional[list[str]] = None) -> dict:
        started = time.time()
        await self._emit("tool_call_started", {
            "tool": "deepsearcher.investigate",
            "goal": goal,
            "prior_failed": prior_failed or [],
        })
        ledger: list[dict] = []
        gaps: list[str] = []
        seen = set(prior_failed or [])
        # decompose -> retrieve -> reflect loop (decomposition stubbed for you to
        # plug a planner model; structure and budget are the contract)
        for _ in range(self._max_rounds):
            query = goal  # TODO: planner produces next query from gaps
            if query in seen:
                break
            seen.add(query)
            for src, typ, text, rel in await self._web(query):
                ledger.append({"claim": text[:200], "source": src, "reliability": rel})
            if ledger:
                break
        if not ledger:
            gaps.append(goal)
        result = {
            "goal": goal,
            "evidence_ledger": ledger,
            "synthesis": {
                "answer_or_plan": ledger[0]["claim"] if ledger else "",
                "confidence": "medium" if ledger else "low",
                "unresolved_gaps": gaps,
            },
            "escalation_brief": {
                "goal": goal,
                "queries_attempted": list(seen),
                "unresolved_gaps": gaps,
            },
        }
        await self._emit("tool_call_finished", {
            "tool": "deepsearcher.investigate",
            "ok": True,
            "duration_ms": round((time.time() - started) * 1000, 2),
            "ledger_count": len(ledger),
            "gap_count": len(gaps),
        })
        return result
