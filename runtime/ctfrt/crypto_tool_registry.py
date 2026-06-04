"""Read-only tool registry for crypto-attack analysis.

All tools are read-only (no execution of attacker-controlled code).
"""
from __future__ import annotations

import base64
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from typing import Optional


@dataclass
class CryptoToolResult:
    tool: str
    path: str
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: Optional[str] = None
    facts: dict = field(default_factory=dict)


_TIMEOUT_S = 4.0
_MAX_CHARS = 8000


def _run(cmd: list[str], *, timeout: float = _TIMEOUT_S) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            check=False, timeout=timeout, shell=False,
        )
        return proc.stdout[:_MAX_CHARS], proc.stderr[:_MAX_CHARS], proc.returncode
    except subprocess.TimeoutExpired:
        return "", "timed out", -1
    except OSError as exc:
        return "", str(exc), -2


# ── individual tool runners ───────────────────────────────────────────────────

def run_openssl_asn1parse(path: Path) -> CryptoToolResult:
    if not which("openssl"):
        return CryptoToolResult(tool="openssl_asn1parse", path=str(path),
                                error="openssl not found")
    stdout, stderr, code = _run(["openssl", "asn1parse", "-in", str(path)])
    facts: dict = {}
    if "INTEGER" in stdout:
        facts["has_integers"] = True
    if "BIT STRING" in stdout or "SEQUENCE" in stdout:
        facts["likely_key_structure"] = True
    return CryptoToolResult(tool="openssl_asn1parse", path=str(path),
                            stdout=stdout, stderr=stderr, exit_code=code, facts=facts)


def run_frequency_analysis(text: str) -> CryptoToolResult:
    """Letter frequency count — useful for classical cipher identification."""
    alpha = re.sub(r"[^A-Za-z]", "", text).upper()
    counts: dict[str, int] = {}
    for ch in alpha:
        counts[ch] = counts.get(ch, 0) + 1
    total = max(len(alpha), 1)
    freq = {ch: round(counts[ch] / total, 4) for ch in sorted(counts, key=counts.get, reverse=True)}
    top5 = list(freq.keys())[:5]
    facts = {"top_letters": top5, "unique_letters": len(counts), "total_alpha": len(alpha)}
    out = " ".join(f"{k}:{v:.3f}" for k, v in list(freq.items())[:10])
    return CryptoToolResult(tool="frequency_analysis", path="",
                            stdout=out, facts=facts)


def decode_base64_layers(text: str, max_rounds: int = 5) -> CryptoToolResult:
    """Repeatedly try to base64-decode until non-base64 or max rounds."""
    current = text.strip()
    rounds = 0
    history: list[str] = [current]
    for _ in range(max_rounds):
        padded = current + "=" * (-len(current) % 4)
        try:
            decoded = base64.b64decode(padded).decode("latin-1")
        except Exception:
            break
        if decoded == current:
            break
        rounds += 1
        current = decoded.strip()
        history.append(current)
        if not re.match(r"^[A-Za-z0-9+/=\s]+$", current):
            break
    facts = {"rounds": rounds, "final": current[:200]}
    return CryptoToolResult(tool="base64_decode", path="",
                            stdout=current, facts=facts)


def xor_single_byte_brute(data: bytes, printable_only: bool = True) -> CryptoToolResult:
    """Try all 256 single-byte XOR keys; return first printable result."""
    candidates: list[tuple[int, str]] = []
    for key in range(256):
        trial = bytes(b ^ key for b in data)
        try:
            s = trial.decode("utf-8")
        except UnicodeDecodeError:
            s = trial.decode("latin-1")
        if printable_only and not all(32 <= ord(c) <= 126 or c in "\n\r\t" for c in s):
            continue
        candidates.append((key, s))
        if len(candidates) >= 3:
            break
    facts = {"candidates": len(candidates)}
    if candidates:
        best_key, best_str = candidates[0]
        facts["key"] = best_key
        facts["candidate"] = best_str[:200]
        return CryptoToolResult(tool="xor_brute", path="",
                                stdout=best_str, facts=facts)
    return CryptoToolResult(tool="xor_brute", path="",
                            stdout="", facts=facts, error="no printable candidate found")


def caesar_brute_force(text: str) -> list[tuple[int, str]]:
    """Return all 26 Caesar shifts as (shift, plaintext) pairs."""
    results = []
    alpha_only = re.sub(r"[^A-Za-z]", "", text).upper()
    for shift in range(26):
        plain = "".join(
            chr((ord(c) - ord("A") - shift) % 26 + ord("A")) if c.isupper()
            else chr((ord(c) - ord("a") - shift) % 26 + ord("a")) if c.islower()
            else c
            for c in text
        )
        results.append((shift, plain))
    return results


# ── tool selection ────────────────────────────────────────────────────────────

_ACTION_TOOLS: dict[str, list[str]] = {
    "rsa_analysis": ["openssl_asn1parse"],
    "factor_modulus": [],             # placeholder: sympy/sage when available
    "xor_brute_force": ["xor_brute"],
    "decode_base64": ["base64_decode"],
    "caesar_brute_force": ["caesar_brute"],
    "decode_hex": [],                 # inline; no external tool
    "frequency_analysis": ["frequency_analysis"],
    "classical-cipher": ["frequency_analysis"],
}


def select_crypto_tools(actions: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for action in actions:
        for tool in _ACTION_TOOLS.get(action, []):
            if tool not in seen:
                seen.add(tool)
                result.append(tool)
    return result
