"""Advisory intelligence service contracts.

These services provide evidence-backed recommendations to specialists. They do
not execute tools, emit candidates, or decide truth.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    source_id: str
    source_type: Literal["internal_corpus", "external_source", "trace", "writeup", "notes", "docs", "other"]
    title: str | None = None
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    url: str | None = None


class IntelligenceQuestion(BaseModel):
    mission_id: str
    requester: str
    question: str
    context_refs: list[str] = Field(default_factory=list)
    source_scope: Literal["internal", "external", "both"] = "internal"
    max_results: int = Field(default=5, ge=1, le=20)


class IntelligenceAnswer(BaseModel):
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    recommended_next_action: str | None = None
    warnings: list[str] = Field(default_factory=list)


class IntelligenceService:
    async def ask(self, question: IntelligenceQuestion) -> IntelligenceAnswer:
        raise NotImplementedError


class NullIntelligenceService(IntelligenceService):
    def __init__(self, warning: str = "intelligence_disabled"):
        self._warning = warning

    async def ask(self, question: IntelligenceQuestion) -> IntelligenceAnswer:
        return IntelligenceAnswer(
            answer="No intelligence service configured.",
            confidence=0.0,
            evidence=[],
            warnings=[self._warning],
        )
