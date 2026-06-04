"""Wire contracts for the CTF runtime.

These mirror shared/schemas.md exactly — the skill SOPs and the bus messages
speak the same language. Every Kafka message body is one of these models,
serialized to JSON. Change a schema here and in shared/schemas.md together.
"""
from __future__ import annotations

import base64
import time
import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> float:
    return time.time()


class Category(str, Enum):
    reverse = "reverse"
    crypto = "crypto-attack"
    web = "web-exploit"
    pwn = "binary-pwn"
    forensics = "forensics"
    stego = "stego"
    jail = "jail-escape"
    osint = "osint"
    misc = "misc"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


ValidationLevel = Literal["observed", "format_ok", "reproduced", "oracle_accepted"]


def _bytes_to_b64(v: Optional[bytes]) -> Optional[str]:
    if v is None:
        return None
    return base64.b64encode(v).decode("ascii")


def _b64_to_bytes(v):
    if v is None or isinstance(v, bytes):
        return v
    if isinstance(v, str):
        try:
            return base64.b64decode(v.encode("ascii"), validate=True)
        except Exception:
            # Backward-compatible fallback for older/plain JSON payloads.
            return v.encode("utf-8", errors="surrogateescape")
    return v


# ---- researcher output (LOCKED, synchronous tool — not a bus message) -------
class Evidence(BaseModel):
    source: str
    type: Literal["local_notes", "official_docs", "writeup", "web", "code_reference"]
    reliability: Confidence


class ResearchResult(BaseModel):
    original_question: str
    extracted_tokens: list[str] = Field(default_factory=list)
    short_answer: str
    actionable_extract: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)
    handoff_needed: bool = False
    handoff_reason: Optional[str] = None


# ---- hypothesis ledger ------------------------------------------------------
class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: "H" + _id()[:6])
    challenge_id: str
    category: Category
    claim: str
    confidence: Confidence
    evidence: list[str] = Field(default_factory=list)
    next_test: str = ""
    exit_condition: str = ""
    result: Literal["open", "confirmed", "killed"] = "open"
    iterations: int = 0  # for the two-barren-iterations pivot rule


# ---- candidate flag (the only thing the gate accepts) -----------------------
class Candidate(BaseModel):
    id: str = Field(default_factory=_id)
    challenge_id: str
    workdir: str = ""
    candidate: str
    source: str
    flag_format: Optional[str] = None
    format_match: Optional[bool] = None
    validation_level: ValidationLevel = "observed"
    local_validation: Literal["passed", "failed", "not_attempted"] = "not_attempted"
    oracle_validation: Literal["passed", "failed", "not_available"] = "not_available"
    status: Literal["raw", "format_ok", "locally_verified", "solved"] = "raw"
    confidence: Confidence = Confidence.low
    evidence: list[str] = Field(default_factory=list)
    technique: list[str] = Field(default_factory=list)  # how it was solved (for memory)
    # how the gate can INDEPENDENTLY re-derive truth from the artifact.
    # {"method": "reencode_xor"|"sandbox_exec", "artifact": path, ...}
    reproduction: Optional[dict] = None


# ---- bus messages -----------------------------------------------------------
class Challenge(BaseModel):
    id: str = Field(default_factory=_id)
    name: str
    workdir: str = ""
    category_hint: Optional[Category] = None
    artifacts: list[str] = Field(default_factory=list)  # paths in the challenge dir
    flag_format: Optional[str] = None
    remote: Optional[str] = None
    description: str = ""


class Task(BaseModel):
    """Orchestrator -> specialist. Fanned out on ctf.tasks, keyed by category."""
    id: str = Field(default_factory=_id)
    challenge_id: str
    workdir: str = ""
    category: Category
    artifacts: list[str]
    flag_format: Optional[str] = None
    triage: dict = Field(default_factory=dict)
    sandbox_profile: str = "default"
    created_at: float = Field(default_factory=_now)


class Handoff(BaseModel):
    """Specialist -> orchestrator: this is actually another category."""
    challenge_id: str
    from_category: Category
    target: Category
    reason: str
    carry: dict = Field(default_factory=dict)  # extracted params, etc.
    handoff_depth: int = 0  # incremented on each re-route; enforces _MAX_HANDOFF_DEPTH


class TraceEvent(BaseModel):
    """Append-only. The spine for writeups and (later) memory consolidation."""
    id: str = Field(default_factory=_id)
    challenge_id: str
    category: Optional[Category] = None
    kind: str               # tool_call | finding | hypothesis | candidate | handoff | error
    payload: dict = Field(default_factory=dict)
    ts: float = Field(default_factory=_now)


class SandboxRequest(BaseModel):
    id: str = Field(default_factory=_id)
    challenge_id: str
    workdir: str = ""
    artifact: str
    argv: list[str] = Field(default_factory=list)
    stdin: Optional[bytes] = None
    network: bool = False
    timeout_s: int = 30
    writable: bool = False

    @field_validator("stdin", mode="before")
    @classmethod
    def _decode_stdin(cls, v):
        return _b64_to_bytes(v)

    @field_serializer("stdin", when_used="json")
    def _serialize_stdin(self, v: Optional[bytes]):
        return _bytes_to_b64(v)


class SandboxResult(BaseModel):
    request_id: str
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    artifacts: list[str] = Field(default_factory=list)

    @field_validator("stdout", "stderr", mode="before")
    @classmethod
    def _decode_bytes(cls, v):
        return _b64_to_bytes(v)

    @field_serializer("stdout", "stderr", when_used="json")
    def _serialize_bytes(self, v: bytes):
        return _bytes_to_b64(v) or ""
