"""CMS-CAG — institutional memory as a question-answering service.

Stage 1 of the CMS integration. A specialist asks "what worked last time on this
shape?" and gets an evidence-backed *recommendation* — never a command, never on
the critical path.

Design honesty (the impedance I flagged): CMS's L3 evidence/belief layer is
domain-locked to speaker personality (scopes pragmatic/epistemic/social/dynamics,
text-only rules). It does not model CTF techniques. So this adapter uses CMS as
what it genuinely is at L1/L2 — a persistent, session-scoped store of text
observations grouped into episodes — and does CTF-aware retrieval (keyword
overlap over mission summaries) in this layer. The personality belief engine is
intentionally NOT wired. Retrieval is advisory and improves as missions
accumulate; it never gates a solve.

CMS is lazy-imported: ctfrt boots without it. Attach this only where the
cms-runtime package is installed/on path. Otherwise the runtime uses
NullMemoryQuery (memory_query.py) and degrades gracefully.
"""
from __future__ import annotations

import itertools
import re
import time

from .log import get_logger, kv
from .memory_query import EvidenceRef, MemoryAnswer, MemoryQuestion

log = get_logger(__name__)

_STOP = frozenset("the a an of on in to for and or with what which when how did "
                  "have has was were is are do does this that it we i".split())

# technique keywords worth surfacing as actionable next-steps
_TECHNIQUES = [
    "ltrace", "strace", "strcmp", "memcmp", "xor", "angr", "z3", "ghidra",
    "rop", "ret2libc", "format string", "padding oracle", "cbc", "rsa",
    "wiener", "fermat", "spectrogram", "steghide", "zsteg", "volatility",
    "tshark", "sqli", "ssti", "ssrf", "jwt", "deserialization", "pyjail",
    "dynamic tracing", "symbolic execution",
]

_USER = "ctfrt"


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOP}


class _CTFExtractor:
    """Minimal deterministic feature extractor. CMS needs one for episode
    boundary detection; its linguistic features are irrelevant to our text
    retrieval, so we return stable values."""
    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        return {"semantic_density": 0.5, "pragmatic_load": 0.3,
                "epistemic_certainty": 0.7, "temporal_orientation": 0.5,
                "topic_concreteness": 0.6, "intent_direction": 0.5}


class CMSMemory:
    """MemoryQueryService backed by a CMS L1+L2 engine."""

    def __init__(self, db_path: str = ":memory:", *, episode_window: int = 8):
        # lazy import — keeps ctfrt free of the cms dependency
        from cms.storage.sqlite import SQLiteBackend
        from cms.storage.schema import FULL_SCHEMA_DDL
        from cms.storage.observation_store import ObservationStore
        from cms.storage.episode_store import EpisodeStore
        from cms.l1.adapter import LegacyExtractorAdapter
        from cms.l1.service import ObservationService
        from cms.l2.policies import WindowedClosurePolicy
        from cms.l2.service import EpisodeService
        from cms.runtime.engine import CMSEngine

        be = SQLiteBackend(db_path)
        be.bootstrap_schema(FULL_SCHEMA_DDL)
        self._obs_store = ObservationStore(be)
        ep_store = EpisodeStore(be)
        self._ids = itertools.count()
        obs_service = ObservationService(
            adapter=LegacyExtractorAdapter(_CTFExtractor()),
            store=self._obs_store,
            id_factory=lambda: f"obs_{next(self._ids):06d}",
        )
        ep_service = EpisodeService(
            store=ep_store, policy=WindowedClosurePolicy(max_size=episode_window),
            id_factory=lambda: f"ep_{next(self._ids):06d}",
        )
        self._engine = CMSEngine(obs_service, ep_service)  # no L3: by design

    # ── writer side ────────────────────────────────────────────────────────
    def record_mission(self, mission_id: str, category: str, summary: str) -> None:
        """File one mission outcome as a session-scoped observation. `category`
        is the session lane, so retrieval can scope to it."""
        self._engine.process_turn(
            _USER, category, f"{mission_id}-{time.time_ns()}", summary)
        log.info("mission recorded to institutional memory",
                 extra=kv(mission_id=mission_id, category=category))

    # ── reader side (the CAG answer) ─────────────────────────────────────────
    async def ask(self, q: MemoryQuestion) -> MemoryAnswer:
        category = q.category or ""
        recent = self._obs_store.latest_for_session(_USER, category, limit=50) \
            if category else []
        qtokens = _tokens(q.question)

        scored: list[tuple[float, object]] = []
        for o in recent:
            overlap = qtokens & _tokens(o.raw_text)
            if overlap:
                scored.append((len(overlap) / max(len(qtokens), 1), o))
        scored.sort(key=lambda s: s[0], reverse=True)
        top = scored[: q.max_evidence]

        if not top:
            return MemoryAnswer(
                question=q.question, answer="No comparable past missions found.",
                confidence=0.0,
                warnings=["institutional memory has no matching prior missions"])

        evidence = [
            EvidenceRef(source_id=getattr(o, "obs_id", "?"),
                        source_type="mission_observation",
                        summary=o.raw_text[:200], support_score=round(score, 3),
                        timestamp=str(getattr(o, "created_at", "")))
            for score, o in top
        ]
        techniques = self._techniques_in(t[1].raw_text for t in top)
        answer = (f"{len(top)} prior {category or 'mission'}(s) resemble this. "
                  + (f"Techniques that appeared: {', '.join(techniques)}."
                     if techniques else "No specific technique tagged."))
        rec = (f"Consider {techniques[0]} — it appeared in the closest prior "
               f"mission(s)." if techniques else None)
        # confidence scales with match strength and corroboration, capped
        confidence = min(0.9, top[0][0] * (0.6 + 0.1 * len(top)))
        return MemoryAnswer(
            question=q.question, answer=answer, confidence=round(confidence, 2),
            evidence=evidence, related_patterns=techniques,
            recommended_next_action=rec)  # a suggestion, never a command

    @staticmethod
    def _techniques_in(texts) -> list[str]:
        seen: dict[str, int] = {}
        for txt in texts:
            low = txt.lower()
            for tech in _TECHNIQUES:
                if tech in low:
                    seen[tech] = seen.get(tech, 0) + 1
        return [t for t, _ in sorted(seen.items(), key=lambda x: x[1], reverse=True)]


class MemoryConsumer:
    """Feeds the durable trace spine into institutional memory: subscribes to
    ctf.traces and records solved/rejected missions. Thin glue — the value is
    in CMSMemory. Off the critical path."""

    def __init__(self, bus, memory: CMSMemory):
        self.bus = bus
        self.memory = memory

    async def run(self) -> None:
        from .config import Topics
        async for ev in self.bus.subscribe(Topics.TRACES, group="memory"):
            kind = ev.get("kind")
            payload = ev.get("payload", {})
            cid = ev.get("challenge_id", "?")
            category = payload.get("category") or ev.get("category") or ""
            if kind == "solved":
                techniques = payload.get("technique", []) or []
                tech_str = ", ".join(techniques) if techniques else "technique untagged"
                self.memory.record_mission(
                    cid, category,
                    f"Mission {cid} {category}: SOLVED via {tech_str}. "
                    f"flag={payload.get('flag', '')} source={payload.get('source', '')}")
            elif kind == "candidate_rejected":
                reasons = payload.get("reasons", [])
                self.memory.record_mission(
                    cid, category,
                    f"Mission {cid} {category}: candidate REJECTED ({', '.join(reasons)}).")

    def consolidate(self, challenge_id: str, lesson: dict) -> None:
        """Store a post-solve lesson in institutional memory (L1/L2 only)."""
        techniques = lesson.get("technique", [])
        source = lesson.get("source", "")
        category = lesson.get("category", "")
        tech_str = ", ".join(techniques) if techniques else "unknown technique"
        text = (
            f"Lesson from {challenge_id} [{category}]: solved via {tech_str}. "
            f"Source: {source}. "
            + (f"Evidence: {lesson.get('evidence', '')}." if lesson.get("evidence") else "")
        )
        try:
            self.memory.record_mission(challenge_id, category, text)
            log.info("lesson consolidated", extra=kv(challenge_id=challenge_id, techniques=techniques))
        except Exception as exc:
            log.warning("consolidate failed", extra=kv(challenge_id=challenge_id, error=repr(exc)))
