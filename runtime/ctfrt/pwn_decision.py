"""Decision rules for the binary-pwn specialist.

Maps ELF/binary artifact signals to exploitation technique next-actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class PwnArtifactSignals:
    kind: str = "unknown"   # elf, pe, script, text
    is_elf: bool = False
    has_stack_strings: bool = False   # gets, strcpy, scanf patterns
    has_format_string: bool = False   # printf with user input
    has_nx: bool = False
    has_pie: bool = False
    has_canary: bool = False
    imported_funcs: list[str] = field(default_factory=list)
    string_snippets: list[str] = field(default_factory=list)


def analyze_pwn_artifact(data: bytes, filename: str = "") -> PwnArtifactSignals:
    sig = PwnArtifactSignals()
    head = data[:16]
    text = data.decode("latin-1", errors="ignore")
    low = text.lower()

    if head.startswith(b"\x7fELF"):
        sig.kind = "elf"
        sig.is_elf = True
    elif head.startswith(b"MZ"):
        sig.kind = "pe"
    elif filename.endswith((".py", ".sh", ".rb", ".pl")):
        sig.kind = "script"
    else:
        sig.kind = "binary"

    dangerous_imports = {"gets", "strcpy", "scanf", "read", "fgets", "strcat", "sprintf"}
    for func in dangerous_imports:
        if func in low:
            sig.imported_funcs.append(func)

    sig.has_stack_strings = any(f in low for f in ("gets", "strcpy", "scanf", "strcat"))
    sig.has_format_string = bool(re.search(r"printf|fprintf|sprintf", low))

    snippets = re.findall(r"[A-Za-z0-9_\-./]{6,}", text)
    sig.string_snippets = snippets[:20]
    return sig


class PwnDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0


def evaluate_pwn_decision(signals: PwnArtifactSignals) -> PwnDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []

    if signals.has_stack_strings:
        rules.append("dangerous_string_functions")
        actions.extend(["overflow_probe", "rop_gadget_search"])
        techniques.extend(["buffer-overflow", "rop"])

    if signals.has_format_string:
        rules.append("format_string_candidate")
        actions.extend(["format_string_probe", "leak_stack"])
        techniques.append("format-string")

    if signals.is_elf and not rules:
        rules.append("elf_binary")
        actions.extend(["checksec", "strings_extraction", "disassembly_summary"])
        techniques.append("binary-analysis")

    if signals.imported_funcs:
        rules.append("dangerous_imports_present")
        if "rop_gadget_search" not in actions:
            actions.append("rop_gadget_search")

    confidence = min(0.9, 0.2 * len(rules)) if rules else 0.0
    return PwnDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
    )
