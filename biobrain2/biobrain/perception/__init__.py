"""
biobrain.perception — Input interpretation and structuring
============================================================

Converts noisy RawInput into structured PerceivedInput.
Now includes OperationClass detection for structured policy enforcement.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..core.enums import TrustLevel, OperationClass
from ..core.signals import RawInput, PerceivedInput, ModeState

logger = logging.getLogger("biobrain.perception")

# ─── Risk patterns ────────────────────────────────────────────────────────────

RISK_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\b(rm\s+-rf|sudo\s+rm|drop\s+table|delete\s+from)\b", "destructive_command"),
    (r"(?i)\b(password|secret|api[_-]?key|token|credential)\b", "sensitive_data"),
    (r"(?i)\b(ignore\s+previous|disregard\s+instructions|you\s+are\s+now)\b", "prompt_injection"),
    (r"(?i)\b(urgent|emergency|critical|immediately)\b", "urgency_marker"),
    (r"(?i)\b(exploit|vulnerability|cve-\d{4}|injection|xss|sqli)\b", "security_context"),
    (r"(?i)\b(admin|root|superuser|escalat)\b", "privilege_context"),
]

# ─── Intent patterns ─────────────────────────────────────────────────────────

INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)^(scan|test|pentest|audit|assess)", "security_assessment"),
    (r"(?i)(find|search|look\s+up|query|retrieve)", "information_retrieval"),
    (r"(?i)(create|build|make|generate|write)", "creation"),
    (r"(?i)(fix|repair|debug|resolve|patch)", "remediation"),
    (r"(?i)(explain|describe|what\s+is|how\s+does)", "explanation"),
    (r"(?i)(compare|versus|vs\.?|difference)", "comparison"),
    (r"(?i)(plan|strategy|roadmap|schedule)", "planning"),
    (r"(?i)(report|summary|status|overview)", "reporting"),
    (r"(?i)(deploy|release|push|ship)", "deployment"),
    (r"(?i)(configure|setup|install|enable)", "configuration"),
    (r"(?i)(delete|remove|drop|purge|destroy)", "deletion"),
]

# ─── Intent → OperationClass mapping ─────────────────────────────────────────

INTENT_TO_OPERATION: dict[str, OperationClass] = {
    "security_assessment": OperationClass.EXECUTE,
    "information_retrieval": OperationClass.READ,
    "creation": OperationClass.WRITE,
    "remediation": OperationClass.WRITE,
    "explanation": OperationClass.READ,
    "comparison": OperationClass.READ,
    "planning": OperationClass.READ,
    "reporting": OperationClass.REPORT,
    "deployment": OperationClass.EXECUTE,
    "configuration": OperationClass.CONFIGURE,
    "deletion": OperationClass.DELETE,
    "general": OperationClass.READ,
}

# ─── Classification ──────────────────────────────────────────────────────────

CLASSIFICATION_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(pentest|penetration|vulnerability|exploit|attack)", "security"),
    (r"(?i)(code|function|class|module|script|api)", "engineering"),
    (r"(?i)(document|report|write|draft|memo)", "documentation"),
    (r"(?i)(deploy|infra|server|container|docker|k8s)", "operations"),
    (r"(?i)(train|model|dataset|fine.?tune|embedding)", "ml_engineering"),
    (r"(?i)(meeting|team|standup|sprint|review)", "collaboration"),
]


def perceive(raw: RawInput, mode: Optional[ModeState] = None) -> PerceivedInput:
    """Convert raw input into structured, classified signal."""
    content = raw.content.strip()
    normalized = re.sub(r"\s+", " ", content).strip()
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", normalized)

    intent = _extract_intent(content)
    entities = _extract_entities(content)
    risk_indicators = _detect_risks(content, raw.trust)
    classification = _classify(content)
    operation = INTENT_TO_OPERATION.get(intent, OperationClass.READ)

    return PerceivedInput(
        raw=raw,
        intent=intent,
        entities=entities,
        language="en",
        risk_indicators=risk_indicators,
        classification=classification,
        operation_class=operation,
        normalized_content=normalized,
    )


def _extract_intent(content: str) -> str:
    for pattern, intent in INTENT_PATTERNS:
        if re.search(pattern, content):
            return intent
    return "general"


def _extract_entities(content: str) -> list[str]:
    entities = []
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", content):
        entities.append(match.group(1))
    common = {"The", "This", "That", "What", "When", "Where", "How", "Why",
              "Can", "Could", "Should", "Would", "Will", "Does", "Did",
              "For", "But", "And", "Not", "Yes", "No", "I", "We", "You"}
    for match in re.finditer(r"\b([A-Z][a-z]{2,})\b", content):
        word = match.group(1)
        if word not in common:
            entities.append(word)
    for match in re.finditer(r"CVE-\d{4}-\d{4,}", content):
        entities.append(match.group(0))
    for match in re.finditer(r"https?://\S+", content):
        entities.append(match.group(0))
    return list(dict.fromkeys(entities))[:20]


def _detect_risks(content: str, trust: TrustLevel) -> list[str]:
    risks = []
    for pattern, risk_type in RISK_PATTERNS:
        if re.search(pattern, content):
            risks.append(risk_type)
    if trust in (TrustLevel.UNTRUSTED, TrustLevel.ADVERSARIAL):
        risks.append("untrusted_source")
    return list(dict.fromkeys(risks))


def _classify(content: str) -> str:
    for pattern, cls in CLASSIFICATION_PATTERNS:
        if re.search(pattern, content):
            return cls
    return "general"
