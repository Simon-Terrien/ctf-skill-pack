"""Decision rules for the web-exploit specialist.

Maps HTTP/URL artifact signals to analysis actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class WebArtifactSignals:
    kind: str = "unknown"   # url, http_log, html, js, json, text
    url: str = ""
    has_form: bool = False
    has_sql_hint: bool = False
    has_template_hint: bool = False
    has_jwt: bool = False
    has_deserialization_hint: bool = False
    has_path_param: bool = False
    string_snippets: list[str] = field(default_factory=list)


def analyze_web_artifact(text: str, filename: str = "") -> WebArtifactSignals:
    sig = WebArtifactSignals()
    low = text.lower()

    if text.startswith(("http://", "https://")) or filename.endswith(".url"):
        sig.kind = "url"
        sig.url = text.strip()
    elif "http/" in low or "get /" in low or "post /" in low or "host:" in low:
        sig.kind = "http_log"
    elif "<html" in low or "<!doctype" in low:
        sig.kind = "html"
    elif filename.endswith(".js"):
        sig.kind = "js"
    elif filename.endswith(".json") or (text.strip().startswith("{") and text.strip().endswith("}")):
        sig.kind = "json"
    else:
        sig.kind = "text"

    sig.has_form = bool(re.search(r"<form|input type|action=", low))
    sig.has_sql_hint = bool(re.search(r"select.*from|union.*select|or.*1=1|'.*--", low))
    sig.has_template_hint = bool(re.search(r"\{\{.*\}\}|\{%.*%\}|\$\{.*\}", text))
    sig.has_jwt = bool(re.search(r"ey[A-Za-z0-9+/]{20,}\.[A-Za-z0-9+/]{20,}\.[A-Za-z0-9+/]{20,}", text))
    sig.has_deserialization_hint = bool(re.search(r"pickle|serialize|marshal|objectinputstream|ois\.read", low))
    sig.has_path_param = bool(re.search(r"\?[a-z_]+=|/[a-z]+/\d+", low))

    snippets = re.findall(r"[A-Za-z0-9_\-./]{6,}", text)
    sig.string_snippets = snippets[:20]
    return sig


class WebDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0


def evaluate_web_decision(signals: WebArtifactSignals) -> WebDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []

    if signals.has_sql_hint:
        rules.append("sql_injection_hint")
        actions.extend(["sqli_probe", "error_based_probe"])
        techniques.append("sqli")

    if signals.has_template_hint:
        rules.append("template_injection_hint")
        actions.extend(["ssti_probe", "template_enumerate"])
        techniques.append("ssti")

    if signals.has_jwt:
        rules.append("jwt_detected")
        actions.extend(["jwt_decode", "jwt_alg_none", "jwt_secret_bruteforce"])
        techniques.append("jwt")

    if signals.has_deserialization_hint:
        rules.append("deserialization_hint")
        actions.extend(["deserialization_probe"])
        techniques.append("deserialization")

    if signals.has_path_param and not rules:
        rules.append("path_parameter_present")
        actions.extend(["path_traversal_probe", "sqli_probe"])
        techniques.extend(["path-traversal", "sqli"])

    if signals.has_form and not rules:
        rules.append("form_present")
        actions.extend(["form_fuzzing", "sqli_probe"])
        techniques.append("sqli")

    if signals.kind in ("url", "http_log") and not rules:
        rules.append("http_endpoint")
        actions.append("endpoint_enumeration")
        techniques.append("web-recon")

    confidence = min(0.9, 0.2 * len(rules)) if rules else 0.0
    return WebDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
    )
