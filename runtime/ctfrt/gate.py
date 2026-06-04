"""flag-discipline, as a service.

Consumes ctf.candidates, runs the final-answer checks + validation levels from
flag-discipline/SKILL.md, emits the verdict to ctf.flags. This is the ONLY path
by which a challenge becomes `solved`. Specialists cannot self-declare.
"""
from __future__ import annotations

import re

from .bus import Bus
from .config import Topics
from .contracts import Candidate, Confidence, TraceEvent
from .log import get_logger, kv
from .memory import MemoryProtocol
from .verify import Verifier

log = get_logger(__name__)


class Gate:
    def __init__(self, bus: Bus, mem: MemoryProtocol, verifier: Verifier | None = None):
        self.bus = bus
        self.mem = mem
        # When attached, the gate INDEPENDENTLY verifies reproduction claims
        # against the artifact, overriding the producer's self-assertion.
        # Production should always attach one; without it the gate trusts
        # the engine's local_validation (weaker — the legacy behavior).
        self.verifier = verifier

    def _format_match(self, c: Candidate) -> bool | None:
        if not c.flag_format:
            return None
        try:
            return re.fullmatch(c.flag_format, c.candidate) is not None
        except re.error:
            return c.flag_format.strip("/") in c.candidate

    def _final_answer_checks(self, c: Candidate) -> list[str]:
        """Returns reasons to reject. Empty list == passes."""
        reasons: list[str] = []

        if c.oracle_validation == "failed":
            reasons.append("reject_oracle_failed")

        # sandbox_exec binary-accept is ground truth even without a CTF{} wrapper:
        # when the binary itself verified the candidate (reproduced+local_passed) and
        # the reproduction method is sandbox_exec, format check is secondary.
        # reencode_xor and bare claims still require format match.
        reproduced_via_sandbox = (
            c.validation_level == "reproduced"
            and c.local_validation == "passed"
            and isinstance(c.reproduction, dict)
            and c.reproduction.get("method") == "sandbox_exec"
        )
        if (c.flag_format and c.oracle_validation == "not_available"
                and c.format_match is False and not reproduced_via_sandbox):
            reasons.append("reject_no_flag_format")

        if c.validation_level in ("observed", "format_ok"):
            reasons.append("reject_no_reproduction_path")

        if c.local_validation != "passed" and c.oracle_validation != "passed":
            reasons.append("reject_unverified_candidate")

        if not c.evidence:
            reasons.append("reject_no_evidence_ledger")

        if "patched" in c.source.lower() and c.oracle_validation != "passed":
            reasons.append("reject_patched_binary_success")

        return reasons

    async def evaluate(self, c: Candidate, artifacts: list[str] | None = None) -> Candidate:
        c.format_match = self._format_match(c)

        # Trust-but-verify: if the candidate claims reproduction and a verifier
        # is attached, the gate re-derives truth from the artifact and OVERRIDES
        # the producer's self-claim with its own result.
        if self.verifier is not None and c.validation_level == "reproduced":
            verified = await self.verifier.verify(c, artifacts or [])
            if verified:
                c.local_validation = "passed"
            else:
                # demote the unproven claim; the checks below will reject it
                c.local_validation = "failed"
                c.validation_level = "format_ok"
                log.warning("reproduction claim failed independent verification",
                            extra=kv(challenge_id=c.challenge_id, candidate_id=c.id))

        rejects = self._final_answer_checks(c)

        if rejects:
            c.status = "raw"
            c.confidence = Confidence.low
            log.info("candidate rejected", extra=kv(
                challenge_id=c.challenge_id, candidate_id=c.id, reasons=rejects))
            await self.bus.publish(Topics.TRACES, TraceEvent(
                challenge_id=c.challenge_id,
                kind="candidate_rejected",
                payload={"candidate_id": c.id, "reasons": rejects},
            ))
            log.debug("publish topic", extra=kv(
                topic=Topics.TRACES, challenge_id=c.challenge_id, kind="candidate_rejected"))
            await self.bus.publish(Topics.TRACES, TraceEvent(
                challenge_id=c.challenge_id,
                kind="gate_verdict",
                payload={
                    "candidate_id": c.id,
                    "accepted": False,
                    "status": c.status,
                    "validation_level": c.validation_level,
                    "format_match": c.format_match,
                    "local_validation": c.local_validation,
                    "oracle_validation": c.oracle_validation,
                    "technique": c.technique,
                    "evidence_count": len(c.evidence),
                    "reasons": rejects,
                },
            ))
            log.debug("publish topic", extra=kv(
                topic=Topics.TRACES, challenge_id=c.challenge_id, kind="gate_verdict"))
            await self.mem.record_candidate(c)
            return c

        if c.oracle_validation == "passed" or c.validation_level == "oracle_accepted":
            c.status = "solved"
            c.validation_level = "oracle_accepted"
            c.confidence = Confidence.high
        elif c.local_validation == "passed" and c.validation_level == "reproduced":
            # Oracle-less challenge: deterministic local reproduction is the top proof.
            c.status = "solved" if c.oracle_validation == "not_available" else "locally_verified"
            c.confidence = Confidence.high
        elif c.format_match is not False:
            c.status = "format_ok"

        log.info("candidate verdict", extra=kv(
            challenge_id=c.challenge_id, candidate_id=c.id,
            status=c.status, validation_level=c.validation_level))
        await self.bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=c.challenge_id,
            kind="candidate_accepted" if c.status in ("solved", "locally_verified") else "gate_verdict",
            payload={
                "candidate_id": c.id,
                "accepted": c.status in ("solved", "locally_verified"),
                "status": c.status,
                "validation_level": c.validation_level,
                "format_match": c.format_match,
                "local_validation": c.local_validation,
                "oracle_validation": c.oracle_validation,
                "technique": c.technique,
                "evidence_count": len(c.evidence),
            },
        ))
        log.debug("publish topic", extra=kv(
            topic=Topics.TRACES, challenge_id=c.challenge_id,
            kind="candidate_accepted" if c.status in ("solved", "locally_verified") else "gate_verdict"))
        await self.mem.record_candidate(c)
        return c

    async def run(self) -> None:
        log.info("gate started", extra=kv(group="gate"))
        log.debug("subscribe topic", extra=kv(topic=Topics.CANDIDATES, group="gate"))
        async for raw in self.bus.subscribe(Topics.CANDIDATES, group="gate"):
            c = await self.evaluate(Candidate.model_validate(raw))
            log.debug("publish topic", extra=kv(topic=Topics.FLAGS, challenge_id=c.challenge_id))
            await self.bus.publish(Topics.FLAGS, c, key=c.challenge_id)
