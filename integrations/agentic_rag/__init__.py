"""AgenticRagService — internal RAG adapter for ctfrt advisory intelligence.

Scans local corpus files (vendor/techniques/*.md, SKILL.md files, solved trace
summaries) for keyword overlap with the question. Returns top matches as
EvidenceRef items so the specialist can incorporate prior institutional
knowledge without calling an LLM.

No execution authority. No Gate bypass. Advisory only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _tokens(text: str) -> set[str]:
    _STOP = frozenset("the a an of on in to for and or with what which when how did "
                      "have has was were is are do does this that it we i".split())
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOP}


def _score(query_tokens: set[str], doc_text: str) -> float:
    doc_tokens = _tokens(doc_text)
    if not doc_tokens:
        return 0.0
    overlap = query_tokens & doc_tokens
    return len(overlap) / max(len(query_tokens), 1)


def _discover_corpus_files() -> list[Path]:
    """Find vendor/techniques/*.md, */SKILL.md, and trace solved summaries."""
    # Walk up from this file to find the repo root (contains vendor/ and */SKILL.md)
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent, here.parent.parent.parent]:
        if (candidate / "vendor" / "corpus_index.yaml").exists():
            root = candidate
            break
    else:
        return []

    files: list[Path] = []
    # Technique corpus
    for md in (root / "vendor" / "techniques").glob("*.md"):
        files.append(md)
    # SKILL.md files
    for skill in root.glob("*/SKILL.md"):
        files.append(skill)
    # Solved trace snippets from .ctfrt/traces (optional)
    trace_dir = Path(os.getenv("CTF_TRACE_DIR", ".ctfrt/traces"))
    if trace_dir.is_dir():
        for jsonl in list(trace_dir.glob("*.jsonl"))[:20]:
            files.append(jsonl)
    return files


class AgenticRagService:
    """Local RAG over vendor/techniques/*.md + SKILL.md files."""

    def __init__(self, corpus_files: list[Path] | None = None):
        self._corpus_files = corpus_files  # None → discover lazily

    def _files(self) -> list[Path]:
        if self._corpus_files is not None:
            return self._corpus_files
        return _discover_corpus_files()

    async def ask(self, question):
        from ctfrt.intelligence import EvidenceRef, IntelligenceAnswer

        q_tokens = _tokens(question.question)
        max_results = question.max_results

        scored: list[tuple[float, Path, str]] = []
        for path in self._files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            score = _score(q_tokens, text)
            if score > 0.0:
                scored.append((score, path, text))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]

        evidence = [
            EvidenceRef(
                source_id=str(path),
                source_type="internal_corpus",
                title=path.name,
                summary=text[:200].replace("\n", " ").strip(),
                confidence=min(0.9, score * 2),
            )
            for score, path, text in top
        ]

        if not evidence:
            return IntelligenceAnswer(
                answer="No matching corpus entries found.",
                confidence=0.0,
                evidence=[],
                warnings=["agentic_rag: no corpus match"],
            )

        best_text = top[0][2][:500].replace("\n", " ")
        return IntelligenceAnswer(
            answer=f"Best match ({top[0][1].name}): {best_text}",
            confidence=min(0.9, top[0][0] * 2),
            evidence=evidence,
            recommended_next_action=None,
            warnings=[],
        )
