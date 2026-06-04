"""Bounded static extractor for likely reverse input/check paths."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .reverse_tool_registry import ReverseToolResult


_COMPARE_SYMBOLS = {"strcmp", "strncmp", "memcmp"}
_INPUT_SYMBOLS = {"scanf", "fgets", "read", "gets", "getline", "getchar"}
_BRANCH_TOKENS = {"je", "jne", "jz", "jnz"}
_CHECK_TOKENS = {"cmp", "test"}
_WINDOW_RADIUS = 1
_MAX_WINDOWS = 12
_MAX_CALL_WINDOWS = 8


class CheckPathSummary(BaseModel):
    path: str
    input_symbols: list[str] = Field(default_factory=list)
    compare_symbols: list[str] = Field(default_factory=list)
    candidate_calls: list[str] = Field(default_factory=list)
    candidate_branches: list[str] = Field(default_factory=list)
    nearby_windows: list[str] = Field(default_factory=list)
    rodata_hints: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    truncated: bool = False
    error: str | None = None


def _unique_append(values: list[str], item: str) -> bool:
    if not item or item in values:
        return False
    values.append(item)
    return True


def _tool_result_by_name(tool_results: list[ReverseToolResult], tool_name: str) -> ReverseToolResult | None:
    for result in tool_results:
        if result.name == tool_name:
            return result
    return None


def _window(lines: list[str], center: int, radius: int = 2) -> str:
    start = max(0, center - radius)
    end = min(len(lines), center + radius + 1)
    return " | ".join(" ".join(lines[pos].split()) for pos in range(start, end) if lines[pos].strip())


def _collect_rodata_hints(tool_results: list[ReverseToolResult]) -> list[str]:
    hints: list[str] = []
    for result in tool_results:
        facts = result.facts or {}
        for hint in list(facts.get("ascii_hints", []) or []):
            _unique_append(hints, str(hint))
            if len(hints) >= 8:
                return hints
    return hints


def _infer_called_symbol(disassembly_line: str) -> str | None:
    if "<" not in disassembly_line or ">" not in disassembly_line:
        return None
    symbol = disassembly_line.split("<", 1)[1].split(">", 1)[0]
    symbol = symbol.split("@", 1)[0].strip().lower()
    return symbol or None


def extract_check_path(path: Path, tool_results: list[ReverseToolResult]) -> CheckPathSummary:
    summary = CheckPathSummary(path=str(path))
    rodata_hints = _collect_rodata_hints(tool_results)
    summary.rodata_hints = rodata_hints

    for result in tool_results:
        facts = result.facts or {}
        for symbol in list(facts.get("input_imports", []) or []):
            _unique_append(summary.input_symbols, str(symbol))
        for symbol in list(facts.get("compare_imports", []) or []):
            _unique_append(summary.compare_symbols, str(symbol))

    disassembly = _tool_result_by_name(tool_results, "objdump_disassembly")
    if disassembly is None or not disassembly.stdout:
        summary.confidence = 0.2 if (summary.input_symbols or summary.compare_symbols or summary.rodata_hints) else 0.0
        summary.error = "no disassembly available"
        return summary

    lines = disassembly.stdout.splitlines()
    for idx, raw in enumerate(lines):
        compact = " ".join(raw.split())
        low = compact.lower()
        if not compact:
            continue

        matched_call = None
        for symbol in summary.compare_symbols + summary.input_symbols:
            if f"<{symbol}@" in low or f"<{symbol}>" in low or low.endswith(f" {symbol}") or f" {symbol}@plt" in low:
                matched_call = symbol
                break
        inferred_symbol = _infer_called_symbol(low) if "call" in low else None
        if inferred_symbol in _COMPARE_SYMBOLS:
            _unique_append(summary.compare_symbols, inferred_symbol)
            matched_call = inferred_symbol
        elif inferred_symbol in _INPUT_SYMBOLS:
            _unique_append(summary.input_symbols, inferred_symbol)
            matched_call = inferred_symbol
        if matched_call and "call" in low:
            _unique_append(summary.candidate_calls, compact)
            if len(summary.nearby_windows) >= _MAX_WINDOWS:
                summary.truncated = True
            else:
                _unique_append(summary.nearby_windows, _window(lines, idx, radius=2))
            for back in range(max(0, idx - 4), idx):
                prev = " ".join(lines[back].split()).lower()
                if "call" in prev and "<" in prev and "@plt" not in prev:
                    if len(summary.nearby_windows) >= _MAX_WINDOWS:
                        summary.truncated = True
                    else:
                        _unique_append(summary.nearby_windows, _window(lines, back, radius=2))
                    break

        if any(token in low.split() for token in _CHECK_TOKENS):
            branch_line = ""
            for next_idx in range(idx + 1, min(len(lines), idx + 3)):
                next_compact = " ".join(lines[next_idx].split()).lower()
                if any(token in next_compact.split() for token in _BRANCH_TOKENS):
                    branch_line = " ".join(lines[next_idx].split())
                    break
            if branch_line:
                _unique_append(summary.candidate_branches, branch_line)
                start = max(0, idx - _WINDOW_RADIUS)
                end = min(len(lines), idx + _WINDOW_RADIUS + 2)
                window = " | ".join(" ".join(lines[pos].split()) for pos in range(start, end) if lines[pos].strip())
                if len(summary.nearby_windows) >= _MAX_WINDOWS:
                    summary.truncated = True
                else:
                    _unique_append(summary.nearby_windows, window)

        if ("movdqa" in low or "movups" in low or "lea" in low) and "[rip+" in low:
            for hint in summary.rodata_hints:
                if len(summary.nearby_windows) >= _MAX_WINDOWS:
                    summary.truncated = True
                    break
                if hint and len(summary.nearby_windows) < _MAX_CALL_WINDOWS:
                    _unique_append(summary.nearby_windows, _window(lines, idx, radius=2))
                    break

        if len(summary.candidate_calls) >= _MAX_WINDOWS:
            summary.truncated = True
            break

    signal_count = sum(
        1 for values in (
            summary.input_symbols,
            summary.compare_symbols,
            summary.candidate_calls,
            summary.candidate_branches,
            summary.rodata_hints,
        ) if values
    )
    summary.confidence = min(0.9, 0.2 + (signal_count * 0.12))
    if not summary.candidate_calls and not summary.candidate_branches:
        summary.error = "no named check path found"
    return summary


def format_check_path_summary(summary: CheckPathSummary) -> str:
    lines = [
        f"- path={summary.path}",
        f"  confidence={summary.confidence:.2f}",
        f"  truncated={summary.truncated}",
    ]
    if summary.input_symbols:
        lines.append("  input_symbols=" + ", ".join(summary.input_symbols))
    if summary.compare_symbols:
        lines.append("  compare_symbols=" + ", ".join(summary.compare_symbols))
    if summary.candidate_calls:
        lines.append("  candidate_calls=" + " | ".join(summary.candidate_calls))
    if summary.candidate_branches:
        lines.append("  candidate_branches=" + " | ".join(summary.candidate_branches))
    if summary.nearby_windows:
        lines.append("  nearby_windows=" + " | ".join(summary.nearby_windows))
    if summary.rodata_hints:
        lines.append("  rodata_hints=" + " | ".join(summary.rodata_hints))
    if summary.error:
        lines.append(f"  error={summary.error}")
    return "\n".join(lines)
