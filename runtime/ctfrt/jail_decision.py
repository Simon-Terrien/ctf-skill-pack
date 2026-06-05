"""Decision rules for the jail-escape specialist.

Maps sandbox/jail artifact signals (Python jail, rbash, restricted eval) to bypass actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class JailArtifactSignals:
    kind: str = "unknown"   # python_jail, bash_jail, javascript_sandbox, text
    has_python_restrictions: bool = False  # __builtins__, exec, eval blocked
    has_bash_restrictions: bool = False    # rbash, restricted PATH
    has_filter_keywords: bool = False      # blacklist pattern in code
    allowed_chars: list[str] = field(default_factory=list)
    string_snippets: list[str] = field(default_factory=list)


def analyze_jail_artifact(text: str, filename: str = "") -> JailArtifactSignals:
    sig = JailArtifactSignals()
    low = text.lower()

    if filename.endswith(".py") or "import" in low or "def " in low or "__" in text:
        sig.kind = "python_jail"
    elif filename.endswith(".sh") or "#!/bin" in text or "rbash" in low:
        sig.kind = "bash_jail"
    elif "function" in low and "var " in low:
        sig.kind = "javascript_sandbox"
    else:
        sig.kind = "text"

    sig.has_python_restrictions = bool(
        re.search(r"__builtins__|restricted|blacklist|not allowed|forbidden", low)
    )
    sig.has_bash_restrictions = bool(
        re.search(r"rbash|restricted shell|PATH=|readonly PATH", low)
    )
    sig.has_filter_keywords = bool(
        re.search(r"filter|blacklist|whitelist|not in|not allowed", low)
    )

    snippets = re.findall(r"[A-Za-z0-9_\-./]{4,}", text)
    sig.string_snippets = snippets[:20]
    return sig


class JailDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0


def evaluate_jail_decision(signals: JailArtifactSignals) -> JailDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []

    if signals.kind == "python_jail":
        rules.append("python_jail_detected")
        actions.extend(["subclass_enum", "string_encoding_bypass", "ast_probe"])
        techniques.extend(["python-jail", "subclass-escape"])
        if signals.has_filter_keywords:
            rules.append("filter_present")
            actions.append("filter_bypass_probe")

    elif signals.kind == "bash_jail":
        rules.append("bash_jail_detected")
        actions.extend(["tab_completion_enum", "builtin_abuse", "editor_escape"])
        techniques.extend(["rbash-escape", "bash-bypass"])

    elif signals.kind == "javascript_sandbox":
        rules.append("javascript_sandbox_detected")
        actions.extend(["proto_pollution_probe", "constructor_escape"])
        techniques.extend(["js-sandbox", "prototype-pollution"])

    if not rules:
        rules.append("unknown_jail_type")
        actions.extend(["strings_extraction", "static_analysis"])
        techniques.append("jail-analysis")

    confidence = min(0.9, 0.2 * len(rules)) if rules else 0.0
    return JailDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
    )
