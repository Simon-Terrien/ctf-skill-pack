"""Bounded static extractor for helper transform paths before compare checks."""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from .reverse_check_path import CheckPathSummary
from .reverse_tool_registry import ReverseToolResult


_MAX_BODY_LINES = 24


class TransformPathSummary(BaseModel):
    path: str
    helper_calls: list[str] = Field(default_factory=list)
    transform_functions: list[str] = Field(default_factory=list)
    operation_kinds: list[str] = Field(default_factory=list)
    loop_indicators: list[str] = Field(default_factory=list)
    rodata_loads: list[str] = Field(default_factory=list)
    body_windows: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    truncated: bool = False
    error: str | None = None


def _unique_append(values: list[str], item: str) -> None:
    if item and item not in values:
        values.append(item)


def _tool_result_by_name(tool_results: list[ReverseToolResult], tool_name: str) -> ReverseToolResult | None:
    for result in tool_results:
        if result.name == tool_name:
            return result
    return None


def _extract_function_label(line: str) -> str | None:
    if "<" not in line or ">" not in line:
        return None
    return line.split("<", 1)[1].split(">", 1)[0].strip()


def _extract_address_prefix(line: str) -> str | None:
    match = re.match(r"^\s*([0-9a-fA-F]+):", line)
    return match.group(1).lower() if match else None


def _extract_call_target_address(line: str) -> str | None:
    match = re.search(r"\bcall\s+([0-9a-fA-F]+)\b", line)
    return match.group(1).lower() if match else None


def _window(lines: list[str], start: int, end: int) -> str:
    return " | ".join(" ".join(lines[pos].split()) for pos in range(start, end) if lines[pos].strip())


def extract_transform_path(
    path: Path,
    tool_results: list[ReverseToolResult],
    check_path: CheckPathSummary,
) -> TransformPathSummary:
    summary = TransformPathSummary(path=str(path))
    disassembly = _tool_result_by_name(tool_results, "objdump_disassembly")
    if disassembly is None or not disassembly.stdout:
        summary.error = "no disassembly available"
        return summary

    lines = disassembly.stdout.splitlines()
    helper_targets: list[tuple[str | None, str | None]] = []
    for window in check_path.nearby_windows:
        for part in window.split(" | "):
            low = part.lower()
            if "call" not in low or "@plt>" in low or "<" not in part:
                continue
            label = _extract_function_label(part)
            addr = _extract_call_target_address(part)
            if label or addr:
                _unique_append(summary.helper_calls, part)
                target = (label, addr)
                if target not in helper_targets:
                    helper_targets.append(target)

    if not helper_targets:
        summary.error = "no helper transform call found"
        return summary

    for target_label, target_addr in helper_targets:
        entry_idx = None
        for idx, raw in enumerate(lines):
            if target_label and f"<{target_label}>:" in raw:
                entry_idx = idx
                break
            if target_addr and _extract_address_prefix(raw) == target_addr:
                entry_idx = idx
                break
        if entry_idx is None:
            continue

        _unique_append(summary.transform_functions, target_label or f"sub_{target_addr}")
        end_idx = min(len(lines), entry_idx + _MAX_BODY_LINES)
        for idx in range(entry_idx + 1, end_idx):
            compact = " ".join(lines[idx].split())
            low = compact.lower()
            if not compact:
                continue
            if any(op in low.split() for op in ("xor", "add", "sub", "rol", "ror")):
                for op in ("xor", "add", "sub", "rol", "ror"):
                    if op in low.split():
                        _unique_append(summary.operation_kinds, op)
            if any(op in low.split() for op in ("cmp", "test", "jne", "je", "jnz", "jz")):
                _unique_append(summary.loop_indicators, compact)
            if ("movdqa" in low or "movups" in low or "lea" in low) and "[rip+" in low:
                _unique_append(summary.rodata_loads, compact)
            if "call" in low and "<" in compact:
                label = _extract_function_label(compact)
                if label in {"malloc@plt", "strlen@plt"}:
                    _unique_append(summary.operation_kinds, label.split("@", 1)[0])

        body_end = min(len(lines), entry_idx + 12)
        _unique_append(summary.body_windows, _window(lines, entry_idx, body_end))

    signal_count = sum(
        1 for values in (
            summary.helper_calls,
            summary.transform_functions,
            summary.operation_kinds,
            summary.loop_indicators,
            summary.rodata_loads,
        ) if values
    )
    summary.confidence = min(0.9, 0.2 + (signal_count * 0.14))
    if not summary.transform_functions:
        summary.error = "helper transform function body not found"
    return summary


def format_transform_path_summary(summary: TransformPathSummary) -> str:
    lines = [
        f"- path={summary.path}",
        f"  confidence={summary.confidence:.2f}",
        f"  truncated={summary.truncated}",
    ]
    if summary.helper_calls:
        lines.append("  helper_calls=" + " | ".join(summary.helper_calls))
    if summary.transform_functions:
        lines.append("  transform_functions=" + ", ".join(summary.transform_functions))
    if summary.operation_kinds:
        lines.append("  operation_kinds=" + ", ".join(summary.operation_kinds))
    if summary.loop_indicators:
        lines.append("  loop_indicators=" + " | ".join(summary.loop_indicators[:8]))
    if summary.rodata_loads:
        lines.append("  rodata_loads=" + " | ".join(summary.rodata_loads[:8]))
    if summary.body_windows:
        lines.append("  body_windows=" + " | ".join(summary.body_windows))
    if summary.error:
        lines.append(f"  error={summary.error}")
    return "\n".join(lines)
