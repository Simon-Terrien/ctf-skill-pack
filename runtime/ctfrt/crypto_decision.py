"""Decision rules for the crypto-attack specialist.

Maps artifact signals (primitive hints, text patterns, imports) to next actions.
Mirrors the structure of reverse_decision.py so both follow the same rule-engine pattern.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel


# ── signal models ─────────────────────────────────────────────────────────────

@dataclass
class CryptoArtifactSignals:
    """Signals extracted from the raw artifact text/bytes."""
    text: str = ""                   # full decoded text of the artifact
    has_base64: bool = False
    has_hex_blob: bool = False
    rsa_fields: list[str] = field(default_factory=list)   # n, e, c, p, q, d
    xor_pattern: bool = False
    caesar_hint: bool = False        # uppercase-only text, shifted alphabet
    vigenere_hint: bool = False      # repeated key hints in repeating pattern
    frequency_skewed: bool = False   # letter frequency far from English
    has_ciphertext_label: bool = False  # keywords: "encrypted", "ciphertext"


def analyze_crypto_artifact(text: str) -> CryptoArtifactSignals:
    """Derive CryptoArtifactSignals from raw artifact text."""
    sig = CryptoArtifactSignals(text=text)
    low = text.lower()

    # Base64: long runs of base64 chars with = padding
    if re.search(r"[A-Za-z0-9+/]{32,}={0,2}", text):
        sig.has_base64 = True

    # Hex blob: long hex string
    if re.search(r"(?:0x)?[0-9a-fA-F]{32,}", text):
        sig.has_hex_blob = True

    # RSA fields
    for field_name in ("n", "e", "c", "p", "q", "d", "phi", "dp", "dq", "qinv"):
        if re.search(rf"\b{field_name}\s*[=:]\s*\d{{6,}}", text):
            sig.rsa_fields.append(field_name)
    if re.search(r"\bpublic.?key\b|\bprivate.?key\b|\bmodulus\b|\bexponent\b", low):
        sig.rsa_fields.append("keyword")

    # Single-byte XOR: numeric key in small range, hex blob
    if re.search(r"\bkey\s*[=:]\s*(?:0x[0-9a-f]{1,2}|\d{1,3})\b", low):
        sig.xor_pattern = True
    if "xor" in low and sig.has_hex_blob:
        sig.xor_pattern = True

    # Caesar: all-caps message with no punctuation other than spaces
    words = re.findall(r"[A-Z]{3,}", text)
    if len(words) >= 5 and not re.search(r"[a-z]", text):
        sig.caesar_hint = True

    # Ciphertext label
    if re.search(r"\b(?:encrypted|ciphertext|cipher|encoded)\b", low):
        sig.has_ciphertext_label = True

    return sig


# ── rule result ───────────────────────────────────────────────────────────────

class CryptoDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0
    handoff_candidates: list[str] = []


_ENGLISH_FREQ_TOP = set("etaoinshrdlcumwfgypbvkjxqz")


def evaluate_crypto_decision(signals: CryptoArtifactSignals) -> CryptoDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []
    handoffs: list[str] = []

    if signals.rsa_fields:
        rules.append("rsa_fields_present")
        actions.append("rsa_analysis")
        techniques.append("rsa")
        if "n" in signals.rsa_fields and "e" in signals.rsa_fields:
            actions.append("factor_modulus")

    if signals.xor_pattern:
        rules.append("xor_pattern_detected")
        actions.append("xor_brute_force")
        techniques.append("xor")

    if signals.has_base64 and not signals.rsa_fields:
        rules.append("base64_blob_present")
        actions.append("decode_base64")
        techniques.append("encoding")

    if signals.caesar_hint:
        rules.append("caesar_hint_detected")
        actions.append("caesar_brute_force")
        techniques.append("caesar")

    if signals.has_hex_blob and not signals.xor_pattern and not signals.rsa_fields:
        rules.append("hex_blob_present")
        actions.append("decode_hex")
        techniques.append("encoding")

    if signals.has_ciphertext_label and not rules:
        rules.append("ciphertext_label_only")
        actions.append("frequency_analysis")
        techniques.append("classical-cipher")

    confidence = min(0.9, 0.15 * len(rules)) if rules else 0.0

    return CryptoDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
        handoff_candidates=handoffs,
    )
