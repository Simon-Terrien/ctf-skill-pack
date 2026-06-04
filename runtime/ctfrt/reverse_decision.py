"""Read-only reverse next-action evaluator backed by reverse/DECISION_TREE.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .reverse_tools import ReverseArtifactSummary
from .reverse_tool_registry import ReverseToolResult


class ReverseDecisionResult(BaseModel):
    matched_rules: list[str] = Field(default_factory=list)
    inferred_techniques: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    next_actions: list[str] = Field(default_factory=list)
    handoff_candidates: list[str] = Field(default_factory=list)
    dynamic_allowed: bool = False


class ReverseFactBundle(BaseModel):
    imported_symbols: list[str] = Field(default_factory=list)
    compare_imports: list[str] = Field(default_factory=list)
    input_imports: list[str] = Field(default_factory=list)
    section_names: list[str] = Field(default_factory=list)
    has_symtab: bool | None = None
    has_rodata: bool | None = None
    rodata_addresses: list[str] = Field(default_factory=list)
    ascii_hints: list[str] = Field(default_factory=list)
    function_labels: list[str] = Field(default_factory=list)
    instruction_kinds: list[str] = Field(default_factory=list)
    pie: bool | None = None
    dynamically_linked: bool | None = None


def _decision_tree_path() -> Path:
    return Path(__file__).resolve().parents[2] / "reverse" / "DECISION_TREE.yaml"


@lru_cache(maxsize=1)
def _load_tree() -> list[dict]:
    raw = yaml.safe_load(_decision_tree_path().read_text(encoding="utf-8")) or {}
    rules = raw.get("rules", [])
    return rules if isinstance(rules, list) else []


def _unique_append(values: list[str], seen: set[str], item: str) -> None:
    if item and item not in seen:
        seen.add(item)
        values.append(item)


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().lower().split("@", 1)[0]


def _merge_unique(values: list[str], items: list[str]) -> list[str]:
    seen = set(values)
    for item in items:
        if item and item not in seen:
            seen.add(item)
            values.append(item)
    return values


def _matches_strings_any(summary: ReverseArtifactSummary, values: list[str]) -> bool:
    haystack = [value.lower() for value in summary.strings]
    return any(needle.lower() in candidate for needle in values for candidate in haystack)


def _matches_imports_any(summary: ReverseArtifactSummary, values: list[str]) -> bool:
    imports = {_normalize_symbol(value) for value in summary.imports}
    return any(_normalize_symbol(value) in imports for value in values)


def _matches_sections_any(summary: ReverseArtifactSummary, values: list[str]) -> bool:
    sections = {value.lower() for value in summary.sections}
    return any(value.lower() in sections for value in values)


def _matches_sections_missing_any(summary: ReverseArtifactSummary, values: list[str]) -> bool:
    sections = {value.lower() for value in summary.sections}
    return any(value.lower() not in sections for value in values)


def _rule_matches(summary: ReverseArtifactSummary, when: dict) -> bool:
    for key, expected in when.items():
        if key == "strings_any":
            if not _matches_strings_any(summary, expected):
                return False
        elif key == "imports_any":
            if not _matches_imports_any(summary, expected):
                return False
        elif key == "sections_any":
            if not _matches_sections_any(summary, expected):
                return False
        elif key == "sections_missing_any":
            if not _matches_sections_missing_any(summary, expected):
                return False
        elif key == "stripped":
            if summary.stripped is not expected:
                return False
        elif key == "strings_empty":
            if (len(summary.strings) == 0) is not expected:
                return False
        elif key == "kinds_any":
            if summary.kind.lower() not in {value.lower() for value in expected}:
                return False
        else:
            return False
    return True


def evaluate_reverse_decision(summaries: list[ReverseArtifactSummary]) -> ReverseDecisionResult:
    if not summaries:
        return ReverseDecisionResult()

    matched_rules: list[str] = []
    inferred_techniques: list[str] = []
    next_actions: list[str] = []
    handoff_candidates: list[str] = []
    rule_seen: set[str] = set()
    technique_seen: set[str] = set()
    action_seen: set[str] = set()
    handoff_seen: set[str] = set()
    confidence = 0.0
    dynamic_allowed = False

    for rule in _load_tree():
        when = rule.get("when", {})
        if not isinstance(when, dict):
            continue
        if not any(_rule_matches(summary, when) for summary in summaries):
            continue

        _unique_append(matched_rules, rule_seen, str(rule.get("id", "")))
        infer = rule.get("infer", {})
        if isinstance(infer, dict):
            for technique in infer.get("technique", []) or []:
                _unique_append(inferred_techniques, technique_seen, str(technique))
            handoff = infer.get("handoff_candidate")
            if isinstance(handoff, str):
                _unique_append(handoff_candidates, handoff_seen, handoff)
            if isinstance(infer.get("confidence"), (int, float)):
                confidence = max(confidence, float(infer["confidence"]))

        action = rule.get("next_action")
        if isinstance(action, str):
            _unique_append(next_actions, action_seen, action)
        if bool(rule.get("dynamic_allowed")):
            dynamic_allowed = True

    return ReverseDecisionResult(
        matched_rules=matched_rules,
        inferred_techniques=inferred_techniques,
        confidence=confidence,
        next_actions=next_actions,
        handoff_candidates=handoff_candidates,
        dynamic_allowed=dynamic_allowed,
    )


def build_fact_bundle(tool_results: list[ReverseToolResult]) -> ReverseFactBundle:
    ctx = ReverseFactBundle()
    for result in tool_results:
        facts = result.facts or {}
        ctx.imported_symbols = _merge_unique(ctx.imported_symbols, list(facts.get("imported_symbols", []) or []))
        ctx.compare_imports = _merge_unique(ctx.compare_imports, list(facts.get("compare_imports", []) or []))
        ctx.input_imports = _merge_unique(ctx.input_imports, list(facts.get("input_imports", []) or []))
        ctx.section_names = _merge_unique(ctx.section_names, list(facts.get("section_names", []) or []))
        ctx.rodata_addresses = _merge_unique(ctx.rodata_addresses, list(facts.get("rodata_addresses", []) or []))
        ctx.ascii_hints = _merge_unique(ctx.ascii_hints, list(facts.get("ascii_hints", []) or []))
        ctx.function_labels = _merge_unique(ctx.function_labels, list(facts.get("function_labels", []) or []))
        ctx.instruction_kinds = _merge_unique(ctx.instruction_kinds, list(facts.get("instruction_kinds", []) or []))
        if "has_symtab" in facts:
            ctx.has_symtab = bool(facts["has_symtab"])
        if "has_rodata" in facts:
            ctx.has_rodata = bool(facts["has_rodata"])
        if "pie" in facts:
            ctx.pie = bool(facts["pie"])
        if "dynamically_linked" in facts:
            ctx.dynamically_linked = bool(facts["dynamically_linked"])
    return ctx


def refine_reverse_decision(
    initial: ReverseDecisionResult,
    facts: ReverseFactBundle,
) -> ReverseDecisionResult:
    refined = initial.model_copy(deep=True)
    matched_seen = set(refined.matched_rules)
    technique_seen = set(refined.inferred_techniques)
    action_seen = set(refined.next_actions)
    handoff_seen = set(refined.handoff_candidates)

    def add_rule(rule_id: str, *, techniques: list[str] | None = None, actions: list[str] | None = None,
                 handoffs: list[str] | None = None, confidence: float | None = None) -> None:
        if rule_id not in matched_seen:
            matched_seen.add(rule_id)
            refined.matched_rules.append(rule_id)
        for technique in techniques or []:
            if technique not in technique_seen:
                technique_seen.add(technique)
                refined.inferred_techniques.append(technique)
        for action in actions or []:
            if action not in action_seen:
                action_seen.add(action)
                refined.next_actions.append(action)
        for handoff in handoffs or []:
            if handoff not in handoff_seen:
                handoff_seen.add(handoff)
                refined.handoff_candidates.append(handoff)
        if confidence is not None:
            refined.confidence = max(refined.confidence, confidence)

    if facts.compare_imports:
        add_rule(
            "facts_compare_imports_present",
            techniques=["direct-compare"],
            actions=["string_reference_analysis"],
            confidence=0.7,
        )
    if facts.input_imports:
        add_rule(
            "facts_input_imports_present",
            actions=["input_path_analysis"],
            confidence=0.55,
        )
    if facts.has_rodata and facts.ascii_hints:
        add_rule(
            "facts_rodata_ascii_hints",
            techniques=["string-anchor-analysis"],
            actions=["follow_string_references"],
            confidence=0.72,
        )
    if facts.has_symtab is False:
        add_rule(
            "facts_symtab_missing",
            techniques=["stripped-binary"],
            actions=["disassembly_summary"],
            confidence=0.65,
        )
    if any(kind in {"cmp", "test", "call"} for kind in facts.instruction_kinds):
        add_rule(
            "facts_disassembly_compare_indicators",
            actions=["disassembly_summary"],
            confidence=0.6,
        )

    refined.dynamic_allowed = False

    return refined


def format_reverse_decision(result: ReverseDecisionResult) -> str:
    if not result.matched_rules:
        return "Reverse decision result:\n- matched_rules=(none)"

    lines = [
        "Reverse decision result:",
        "- matched_rules=" + ", ".join(result.matched_rules),
        "- inferred_techniques=" + (", ".join(result.inferred_techniques) or "(none)"),
        f"- confidence={result.confidence:.2f}",
        "- next_actions=" + (", ".join(result.next_actions) or "(none)"),
        "- handoff_candidates=" + (", ".join(result.handoff_candidates) or "(none)"),
        f"- dynamic_allowed={str(result.dynamic_allowed).lower()}",
    ]
    return "\n".join(lines)


def format_reverse_fact_bundle(bundle: ReverseFactBundle) -> str:
    lines = ["Reverse fact bundle:"]
    if bundle.imported_symbols:
        lines.append("- imported_symbols=" + ", ".join(bundle.imported_symbols[:16]))
    if bundle.compare_imports:
        lines.append("- compare_imports=" + ", ".join(bundle.compare_imports))
    if bundle.input_imports:
        lines.append("- input_imports=" + ", ".join(bundle.input_imports))
    if bundle.section_names:
        lines.append("- section_names=" + ", ".join(bundle.section_names[:16]))
    if bundle.has_symtab is not None:
        lines.append(f"- has_symtab={bundle.has_symtab}")
    if bundle.has_rodata is not None:
        lines.append(f"- has_rodata={bundle.has_rodata}")
    if bundle.pie is not None:
        lines.append(f"- pie={bundle.pie}")
    if bundle.dynamically_linked is not None:
        lines.append(f"- dynamically_linked={bundle.dynamically_linked}")
    if bundle.rodata_addresses:
        lines.append("- rodata_addresses=" + ", ".join(bundle.rodata_addresses[:8]))
    if bundle.ascii_hints:
        lines.append("- ascii_hints=" + " | ".join(bundle.ascii_hints[:8]))
    if bundle.function_labels:
        lines.append("- function_labels=" + ", ".join(bundle.function_labels[:8]))
    if bundle.instruction_kinds:
        lines.append("- instruction_kinds=" + ", ".join(bundle.instruction_kinds))
    return "\n".join(lines)
