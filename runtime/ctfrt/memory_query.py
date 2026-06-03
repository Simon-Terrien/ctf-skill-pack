"""CMS-CAG memory-query seam.

This defines the interface for an institutional-memory question-answering
service (the CMS-CAG agent): a specialist asks "what worked last time on this
shape?" and gets an evidence-backed *recommendation* — never a command. The
service sits OFF the critical path: it informs reasoning, it does not execute,
validate flags, or route missions.

Only the contract and a null implementation live here. The CMS-CAG agent itself
(text-summary bridge over CMS `process_turn`, per the staged plan) is the next
weld and is intentionally NOT implemented yet.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    source_id: str
    source_type: str
    summary: str
    support_score: float = 0.0
    timestamp: Optional[str] = None


class MemoryQuestion(BaseModel):
    mission_id: str
    requester: str                       # which agent/officer is asking
    question: str
    category: Optional[str] = None
    context_refs: list[str] = Field(default_factory=list)
    desired_answer_type: str = "recommendation"
    max_evidence: int = 5


class MemoryAnswer(BaseModel):
    question: str
    answer: str
    confidence: float = 0.0
    evidence: list[EvidenceRef] = Field(default_factory=list)
    related_patterns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_next_action: Optional[str] = None   # a suggestion, never a command


@runtime_checkable
class MemoryQueryService(Protocol):
    async def ask(self, q: MemoryQuestion) -> MemoryAnswer: ...


class NullMemoryQuery:
    """Default: no institutional memory wired. Returns an empty, low-confidence
    answer so callers degrade gracefully."""
    async def ask(self, q: MemoryQuestion) -> MemoryAnswer:
        return MemoryAnswer(question=q.question, answer="", confidence=0.0,
                            warnings=["no institutional memory service attached"])
