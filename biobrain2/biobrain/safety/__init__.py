"""
biobrain.safety — Deterministic pre-reasoning safety gate
============================================================

Reflex layer: fast, deterministic, auditable.
All verdicts (BLOCK, SANITIZE, ESCALATE, ROUTE) are now fully implemented.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..core.enums import ReflexVerdict, SystemMode, TrustLevel
from ..core.signals import SalienceScore, ModeState
from ..core.trace import ReflexResponse

logger = logging.getLogger("biobrain.safety")


BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions", "prompt_injection_ignore"),
    (r"(?i)you\s+are\s+now\s+(?:a|an|the)\s+", "prompt_injection_identity"),
    (r"(?i)disregard\s+(?:all\s+)?(?:above|prior|previous)", "prompt_injection_disregard"),
    (r"(?i)new\s+system\s+prompt", "prompt_injection_system"),
    (r"(?i)rm\s+-rf\s+/(?!\s*$)", "destructive_rm_root"),
    (r"(?i)drop\s+database\s+", "destructive_drop_db"),
    (r"(?i)format\s+(?:c:|/dev/)", "destructive_format"),
]

ESCALATION_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(?:production|prod)\s+(?:deploy|push|release)", "prod_deployment"),
    (r"(?i)(?:delete|remove)\s+(?:all|every)\s+", "mass_deletion"),
    (r"(?i)(?:grant|revoke)\s+(?:admin|root|superuser)", "privilege_change"),
    (r"(?i)(?:disable|turn\s+off)\s+(?:auth|security|firewall|waf)", "security_disable"),
]

# Route patterns: obvious routing that doesn't need deep reasoning
ROUTE_PATTERNS: list[tuple[str, str, str]] = [
    (r"(?i)^(help|h|\?)$", "help_request", "help_handler"),
    (r"(?i)^(status|health|ping)$", "status_request", "status_handler"),
    (r"(?i)^(version|ver)$", "version_request", "version_handler"),
]


def check_reflexes(
    salience: SalienceScore,
    mode: Optional[ModeState] = None,
) -> ReflexResponse:
    """Run all reflex checks. Short-circuits on first match.

    Order: BLOCK → SANITIZE → ESCALATE → ROUTE → PASS
    """
    mode = mode or ModeState()
    content = salience.perceived.raw.content

    # ── 1. BLOCK: forbidden patterns ──────────────────────────────────────
    for pattern, rule in BLOCK_PATTERNS:
        if re.search(pattern, content):
            logger.warning("REFLEX BLOCK: %s", rule)
            return ReflexResponse(
                verdict=ReflexVerdict.BLOCK, rule_triggered=rule,
                reason=f"Safety reflex: {rule} pattern detected",
            )

    if salience.perceived.raw.trust == TrustLevel.ADVERSARIAL:
        return ReflexResponse(
            verdict=ReflexVerdict.BLOCK, rule_triggered="adversarial_source",
            reason="Input source marked as adversarial",
        )

    # ── 2. SANITIZE: malformed or oversized input ─────────────────────────
    if not content or not content.strip():
        return ReflexResponse(
            verdict=ReflexVerdict.SANITIZE, rule_triggered="empty_input",
            reason="Empty or whitespace-only input",
            sanitized_content="",
        )

    if len(content) > 100_000:
        return ReflexResponse(
            verdict=ReflexVerdict.SANITIZE, rule_triggered="input_too_long",
            reason=f"Input exceeds 100K chars ({len(content)} chars)",
            sanitized_content=content[:100_000] + "\n[TRUNCATED]",
        )

    # ── 3. ESCALATE: risky operations ─────────────────────────────────────
    for pattern, rule in ESCALATION_PATTERNS:
        if re.search(pattern, content):
            if mode.mode == SystemMode.AUTONOMOUS:
                logger.info("REFLEX WARN (autonomous mode): %s", rule)
                continue
            logger.info("REFLEX ESCALATE: %s", rule)
            return ReflexResponse(
                verdict=ReflexVerdict.ESCALATE, rule_triggered=rule,
                reason=f"Requires human review: {rule}",
            )

    if salience.risk_score >= 0.85 and mode.mode != SystemMode.AUTONOMOUS:
        return ReflexResponse(
            verdict=ReflexVerdict.ESCALATE, rule_triggered="high_risk_score",
            reason=f"Risk score {salience.risk_score} exceeds threshold",
        )

    # ── 4. ROUTE: obvious deterministic routing ───────────────────────────
    for pattern, rule, target in ROUTE_PATTERNS:
        if re.search(pattern, content):
            return ReflexResponse(
                verdict=ReflexVerdict.ROUTE, rule_triggered=rule,
                reason=f"Deterministic route to {target}",
                route_target=target,
            )

    # ── 5. PASS ───────────────────────────────────────────────────────────
    return ReflexResponse(verdict=ReflexVerdict.PASS)
