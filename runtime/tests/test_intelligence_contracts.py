from __future__ import annotations

import asyncio
import os

from ctfrt.agency_registry import (
    build_external_intelligence_agency,
    build_internal_knowledge_agency,
)
from ctfrt.intelligence import (
    EvidenceRef,
    IntelligenceAnswer,
    IntelligenceQuestion,
    NullIntelligenceService,
)


def run(coro):
    return asyncio.run(coro)


async def test_null_service_returns_no_evidence():
    service = NullIntelligenceService()
    answer = await service.ask(IntelligenceQuestion(
        mission_id="m-1",
        requester="reverse-specialist",
        question="Have we seen this xor technique before?",
    ))
    assert answer.answer == "No intelligence service configured."
    assert answer.confidence == 0.0
    assert answer.evidence == []
    assert answer.warnings == ["intelligence_disabled"]


def test_intelligence_contracts_serialize_and_deserialize():
    question = IntelligenceQuestion(
        mission_id="m-2",
        requester="biobrain",
        question="Which prior traces used xor inversion?",
        context_refs=["trace:xor-clean", "skill:reverse"],
        source_scope="both",
        max_results=3,
    )
    raw_question = question.model_dump_json()
    restored_question = IntelligenceQuestion.model_validate_json(raw_question)
    assert restored_question == question

    answer = IntelligenceAnswer(
        answer="Two prior reverse traces used xor inversion.",
        confidence=0.8,
        evidence=[
            EvidenceRef(
                source_id="trace:xor-clean",
                source_type="trace",
                title="xor-clean summary",
                summary="Solved with xor,keygen-inversion.",
                confidence=0.91,
            )
        ],
        recommended_next_action="Inspect blob_hex and xor_key fields before sandbox execution.",
        warnings=["advisory_only"],
    )
    raw_answer = answer.model_dump_json()
    restored_answer = IntelligenceAnswer.model_validate_json(raw_answer)
    assert restored_answer == answer


def test_registry_returns_null_services_by_default():
    old_internal = os.environ.pop("CTF_INTELLIGENCE_INTERNAL", None)
    old_external = os.environ.pop("CTF_INTELLIGENCE_EXTERNAL", None)
    try:
        internal = build_internal_knowledge_agency()
        external = build_external_intelligence_agency()
        assert isinstance(internal, NullIntelligenceService)
        assert isinstance(external, NullIntelligenceService)
    finally:
        if old_internal is not None:
            os.environ["CTF_INTELLIGENCE_INTERNAL"] = old_internal
        if old_external is not None:
            os.environ["CTF_INTELLIGENCE_EXTERNAL"] = old_external


def test_registry_does_not_require_external_repos():
    old_internal = os.environ.get("CTF_INTELLIGENCE_INTERNAL")
    old_external = os.environ.get("CTF_INTELLIGENCE_EXTERNAL")
    try:
        os.environ["CTF_INTELLIGENCE_INTERNAL"] = "1"
        os.environ["CTF_INTELLIGENCE_EXTERNAL"] = "1"
        internal = build_internal_knowledge_agency()
        external = build_external_intelligence_agency()
        assert isinstance(internal, NullIntelligenceService)
        assert isinstance(external, NullIntelligenceService)
        internal_answer = run(internal.ask(IntelligenceQuestion(
            mission_id="m-3",
            requester="reverse-specialist",
            question="What prior mission matches this trace?",
            source_scope="internal",
        )))
        external_answer = run(external.ask(IntelligenceQuestion(
            mission_id="m-4",
            requester="reverse-specialist",
            question="What public docs explain this tool error?",
            source_scope="external",
        )))
        assert internal_answer.warnings == ["internal_intelligence_adapter_unavailable"]
        assert external_answer.warnings == ["external_intelligence_adapter_unavailable"]
    finally:
        if old_internal is None:
            os.environ.pop("CTF_INTELLIGENCE_INTERNAL", None)
        else:
            os.environ["CTF_INTELLIGENCE_INTERNAL"] = old_internal
        if old_external is None:
            os.environ.pop("CTF_INTELLIGENCE_EXTERNAL", None)
        else:
            os.environ["CTF_INTELLIGENCE_EXTERNAL"] = old_external


if __name__ == "__main__":
    TESTS = [
        test_null_service_returns_no_evidence,
        test_intelligence_contracts_serialize_and_deserialize,
        test_registry_returns_null_services_by_default,
        test_registry_does_not_require_external_repos,
    ]
    for test in TESTS:
        result = test()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
        print(f"PASS {test.__name__}")
