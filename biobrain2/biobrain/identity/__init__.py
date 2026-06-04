"""
biobrain.identity — Persona + structured policy enforcement
=============================================================

Replaces substring-based constraint matching with structured
OperationClass and domain-based policy checks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from ..core.enums import OperationClass, SystemMode
from ..core.signals import IdentityState, PolicyRule, ModeState

logger = logging.getLogger("biobrain.identity")


def load_identity(
    config_path: Optional[str] = None,
    mempalace_identity_path: Optional[str] = None,
) -> IdentityState:
    """Load identity from config and/or MemPalace identity.txt."""
    identity = IdentityState()

    if mempalace_identity_path:
        mp_path = Path(mempalace_identity_path)
        if mp_path.exists():
            try:
                identity.persona = mp_path.read_text().strip()
            except Exception as e:
                logger.warning("Failed to read MemPalace identity: %s", e)

    if config_path:
        cfg_path = Path(config_path)
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    cfg = yaml.safe_load(f) or {}
                identity.persona = cfg.get("persona", identity.persona)
                identity.role = cfg.get("role", "")
                identity.mandate = cfg.get("mandate", "")
                identity.communication_style = cfg.get("communication_style", "")

                # Structured policy fields
                identity.allowed_domains = cfg.get("allowed_domains", [])
                identity.forbidden_operations = [
                    OperationClass(op) for op in cfg.get("forbidden_operations", [])
                ]
                identity.require_approval_for = [
                    OperationClass(op) for op in cfg.get("require_approval_for", [])
                ]
                identity.require_evidence_for = cfg.get("require_evidence_for", [])

                # Explicit policy rules
                for rule_cfg in cfg.get("policies", []):
                    identity.policies.append(PolicyRule(
                        domain=rule_cfg.get("domain", "*"),
                        operation=OperationClass(rule_cfg.get("operation", "read")),
                        effect=rule_cfg.get("effect", "allow"),
                        condition=rule_cfg.get("condition", ""),
                        modes=[SystemMode(m) for m in rule_cfg.get("modes", [])],
                    ))

            except Exception as e:
                logger.warning("Failed to load identity config: %s", e)

    return identity


def check_policy(
    identity: IdentityState,
    operation: OperationClass,
    domain: str,
    mode: ModeState,
) -> tuple[str, str]:
    """Check whether an operation is allowed by policy.

    Returns (effect, reason) where effect is one of:
      "allow"            — proceed
      "deny"             — blocked
      "require_approval" — needs human confirmation

    This replaces the old substring-based is_in_scope / is_constrained.
    """
    # 1. Check forbidden operations (hard deny)
    if operation in identity.forbidden_operations:
        return "deny", f"Operation {operation.value} is forbidden by identity policy"

    # 2. Check domain allowlist (if defined)
    if identity.allowed_domains:
        if not any(d.lower() in domain.lower() for d in identity.allowed_domains):
            return "deny", f"Domain '{domain}' not in allowed domains: {identity.allowed_domains}"

    # 3. Check approval requirements
    if operation in identity.require_approval_for:
        return "require_approval", f"Operation {operation.value} requires human approval"

    # 4. Check explicit policy rules
    for rule in identity.policies:
        # Match domain
        if rule.domain != "*" and rule.domain.lower() not in domain.lower():
            continue
        # Match operation
        if rule.operation != operation:
            continue
        # Match mode (if specified)
        if rule.modes and mode.mode not in rule.modes:
            continue
        # Rule matches
        return rule.effect, f"Policy rule: {rule.domain}/{rule.operation.value} → {rule.effect}"

    # 5. Default: allow
    return "allow", ""


def needs_evidence(identity: IdentityState, domain: str) -> bool:
    """Check if evidence is required for this domain."""
    return any(d.lower() in domain.lower() for d in identity.require_evidence_for)
