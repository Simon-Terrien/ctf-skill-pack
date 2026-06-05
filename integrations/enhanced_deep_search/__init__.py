"""EnhancedDeepSearchService — external intelligence adapter for ctfrt.

Wraps ctfrt.tools.DeepSearcher to answer external intelligence questions.
Maps the DeepSearcher's evidence_ledger to EvidenceRef items.

No execution authority. No Gate bypass. Advisory only.
"""
from __future__ import annotations


class EnhancedDeepSearchService:
    """External intelligence via DeepSearcher multi-hop web search."""

    def __init__(self, max_rounds: int = 3):
        self._max_rounds = max_rounds

    async def ask(self, question):
        from ctfrt.intelligence import EvidenceRef, IntelligenceAnswer
        from ctfrt.tools import DeepSearcher

        searcher = DeepSearcher(max_rounds=self._max_rounds)
        try:
            result = await searcher.investigate(goal=question.question)
        except Exception as exc:
            return IntelligenceAnswer(
                answer="DeepSearcher error.",
                confidence=0.0,
                evidence=[],
                warnings=[f"enhanced_deep_search: {repr(exc)}"],
            )

        ledger = result.get("evidence_ledger", [])
        synthesis = result.get("synthesis", {})
        gaps = synthesis.get("unresolved_gaps", [])

        evidence = [
            EvidenceRef(
                source_id=e.get("source", "unknown"),
                source_type="external_source",
                title=e.get("query", ""),
                summary=e.get("claim", "")[:200],
                confidence=0.6 if e.get("reliability") != "low" else 0.3,
                url=e.get("source") if e.get("source", "").startswith("http") else None,
            )
            for e in ledger
        ]

        answer = synthesis.get("answer_or_plan", "") or "No answer found."
        confidence = 0.7 if evidence else 0.0
        warnings = [f"unresolved_gap: {g}" for g in gaps]

        return IntelligenceAnswer(
            answer=answer,
            confidence=confidence,
            evidence=evidence,
            recommended_next_action=None,
            warnings=warnings,
        )
