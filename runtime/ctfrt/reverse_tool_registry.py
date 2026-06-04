"""Bounded read-only registry for reverse-analysis helper commands."""
from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

from pydantic import BaseModel, Field


class ReverseToolSpec(BaseModel):
    name: str
    command_template: list[str]
    requires_tool: str
    read_only: bool
    sandbox_required: bool
    timeout_s: float
    max_output_chars: int
    allowed_next_actions: list[str] = Field(default_factory=list)


class ReverseToolResult(BaseModel):
    name: str
    path: str
    command: list[str] = Field(default_factory=list)
    read_only: bool
    sandbox_required: bool
    timeout_s: float
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    summary_lines: list[str] = Field(default_factory=list)
    facts: dict = Field(default_factory=dict)
    truncated: bool = False
    timed_out: bool = False
    tool_missing: bool = False
    error: str | None = None


_DEFAULT_TIMEOUT_S = 1.0
_DEFAULT_MAX_OUTPUT_CHARS = 4000

_SPECS = [
    ReverseToolSpec(
        name="file_summary",
        command_template=["file", "--brief", "{path}"],
        requires_tool="file",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["static_detail"],
    ),
    ReverseToolSpec(
        name="readelf_header",
        command_template=["readelf", "-h", "-l", "{path}"],
        requires_tool="readelf",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["static_detail"],
    ),
    ReverseToolSpec(
        name="readelf_sections",
        command_template=["readelf", "-W", "-S", "{path}"],
        requires_tool="readelf",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["static_detail"],
    ),
    ReverseToolSpec(
        name="readelf_symbols",
        command_template=["readelf", "-Ws", "{path}"],
        requires_tool="readelf",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["input_path_analysis", "static_detail"],
    ),
    ReverseToolSpec(
        name="objdump_rodata",
        command_template=["objdump", "-s", "-j", ".rodata", "{path}"],
        requires_tool="objdump",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["follow_string_references", "string_reference_analysis", "static_detail"],
    ),
    ReverseToolSpec(
        name="objdump_disassembly",
        command_template=["objdump", "-d", "-M", "intel", "{path}"],
        requires_tool="objdump",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=16000,
        allowed_next_actions=["follow_string_references", "string_reference_analysis", "input_path_analysis", "disassembly_summary"],
    ),
    ReverseToolSpec(
        name="checksec_summary",
        command_template=["checksec", "--file={path}"],
        requires_tool="checksec",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["static_detail"],
    ),
    ReverseToolSpec(
        name="xxd_head",
        command_template=["xxd", "-g1", "-l", "256", "{path}"],
        requires_tool="xxd",
        read_only=True,
        sandbox_required=False,
        timeout_s=_DEFAULT_TIMEOUT_S,
        max_output_chars=_DEFAULT_MAX_OUTPUT_CHARS,
        allowed_next_actions=["static_detail"],
    ),
]

REVERSE_TOOL_REGISTRY = {spec.name: spec for spec in _SPECS}

REVERSE_ACTION_TOOL_MAP = {
    "follow_string_references": ["objdump_rodata", "objdump_disassembly"],
    "string_reference_analysis": ["objdump_rodata", "objdump_disassembly"],
    "input_path_analysis": ["readelf_symbols", "objdump_disassembly"],
    "disassembly_summary": ["objdump_disassembly"],
    "static_detail": ["readelf_header", "readelf_sections", "readelf_symbols", "objdump_rodata"],
}


def get_reverse_tool(name: str) -> ReverseToolSpec:
    return REVERSE_TOOL_REGISTRY[name]


def select_tools_for_next_actions(next_actions: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for action in next_actions:
        for tool_name in REVERSE_ACTION_TOOL_MAP.get(action, []):
            if tool_name in seen:
                continue
            seen.add(tool_name)
            selected.append(tool_name)
    return selected


def _cap_output(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _first_nonempty_lines(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        compact = line.strip()
        if not compact:
            continue
        lines.append(compact)
        if len(lines) >= limit:
            break
    return lines


def _normalize_file_summary(stdout: str) -> list[str]:
    return _first_nonempty_lines(stdout, 1)


def _facts_file_summary(stdout: str) -> dict:
    line = next(iter(_first_nonempty_lines(stdout, 1)), "")
    low = line.lower()
    return {
        "description": line,
        "is_elf": "elf" in low,
        "is_pie_hint": "pie executable" in low,
    }


def _normalize_readelf_header(stdout: str) -> list[str]:
    wanted = ("Class:", "Type:", "Machine:", "Entry point address:", "Requesting program interpreter:")
    lines: list[str] = []
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if any(stripped.startswith(prefix) for prefix in wanted):
            lines.append(stripped)
        if len(lines) >= 5:
            break
    return lines


def _facts_readelf_header(stdout: str) -> dict:
    facts: dict[str, str | bool] = {}
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip()
        if key == "Class":
            facts["elf_class"] = value
        elif key == "Type":
            facts["elf_type"] = value
            facts["pie"] = "DYN" in value
        elif key == "Machine":
            facts["machine"] = value
        elif key == "Entry point address":
            facts["entry_point"] = value
        elif key == "Requesting program interpreter":
            facts["interpreter"] = value
            facts["dynamically_linked"] = True
    return facts


def _normalize_readelf_sections(stdout: str) -> list[str]:
    sections: list[str] = []
    for raw in stdout.splitlines():
        if "[" not in raw or "]" not in raw:
            continue
        compact = " ".join(raw.split())
        if compact.startswith("[Nr]"):
            continue
        sections.append(compact)
        if len(sections) >= 6:
            break
    return sections


def _facts_readelf_sections(stdout: str) -> dict:
    names: list[str] = []
    seen: set[str] = set()
    for raw in stdout.splitlines():
        parts = raw.split()
        if len(parts) < 2 or not parts[0].startswith("["):
            continue
        name = parts[1]
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return {
        "section_names": names[:32],
        "has_symtab": ".symtab" in seen,
        "has_rodata": ".rodata" in seen,
    }


def _normalize_readelf_symbols(stdout: str) -> list[str]:
    lines: list[str] = []
    for raw in stdout.splitlines():
        compact = " ".join(raw.split())
        if " UND " not in f" {compact} ":
            continue
        lines.append(compact)
        if len(lines) >= 8:
            break
    return lines


def _facts_readelf_symbols(stdout: str) -> dict:
    imported_symbols: list[str] = []
    compare_imports: list[str] = []
    input_imports: list[str] = []
    seen_imports: set[str] = set()
    compare_set = {"strcmp", "strncmp", "memcmp"}
    input_set = {"scanf", "fgets", "read", "gets", "getline", "getchar"}
    for raw in stdout.splitlines():
        compact = " ".join(raw.split())
        if " UND " not in f" {compact} ":
            continue
        symbol = compact.rsplit(" ", 1)[-1].split("@", 1)[0]
        if symbol in seen_imports:
            continue
        seen_imports.add(symbol)
        imported_symbols.append(symbol)
        if symbol in compare_set:
            compare_imports.append(symbol)
        if symbol in input_set:
            input_imports.append(symbol)
    return {
        "imported_symbols": imported_symbols[:32],
        "compare_imports": compare_imports,
        "input_imports": input_imports,
    }


def _normalize_objdump_rodata(stdout: str) -> list[str]:
    return _first_nonempty_lines(stdout, 8)


def _facts_objdump_rodata(stdout: str) -> dict:
    addresses: list[str] = []
    ascii_hints: list[str] = []
    for raw in stdout.splitlines():
        parts = raw.split()
        if not parts:
            continue
        token = parts[0]
        if all(ch in "0123456789abcdefABCDEF" for ch in token):
            addresses.append(f"0x{token.lower()}")
        if len(parts) >= 2:
            tail = " ".join(parts[1:])
            if any(ch.isalpha() for ch in tail):
                ascii_hints.append(tail)
        if len(addresses) >= 8 and len(ascii_hints) >= 8:
            break
    return {
        "rodata_addresses": addresses[:16],
        "ascii_hints": ascii_hints[:8],
    }


def _normalize_objdump_disassembly(stdout: str) -> list[str]:
    lines: list[str] = []
    for raw in stdout.splitlines():
        compact = " ".join(raw.split())
        if not compact:
            continue
        if "<" in compact and ">:" in compact:
            lines.append(compact)
        elif any(token in compact for token in (" call ", "\tcall", " lea ", "\tlea", " cmp ", "\tcmp", " test ", "\ttest")):
            lines.append(compact)
        if len(lines) >= 10:
            break
    return lines or _first_nonempty_lines(stdout, 8)


def _facts_objdump_disassembly(stdout: str) -> dict:
    function_labels: list[str] = []
    instruction_kinds: list[str] = []
    seen_functions: set[str] = set()
    seen_instr: set[str] = set()
    interesting = ("call", "lea", "cmp", "test", "movzx", "jne", "je")
    for raw in stdout.splitlines():
        compact = " ".join(raw.split())
        if "<" in compact and ">:" in compact:
            label = compact.split("<", 1)[1].split(">:", 1)[0]
            if label not in seen_functions:
                seen_functions.add(label)
                function_labels.append(label)
        parts = compact.split()
        if len(parts) >= 3:
            for token in parts:
                if token in interesting and token not in seen_instr:
                    seen_instr.add(token)
                    instruction_kinds.append(token)
                    break
        if len(function_labels) >= 12 and len(instruction_kinds) >= 8:
            break
    return {
        "function_labels": function_labels[:12],
        "instruction_kinds": instruction_kinds[:8],
    }


def _normalize_checksec_summary(stdout: str, stderr: str) -> list[str]:
    return _first_nonempty_lines(stdout or stderr, 6)


def _facts_checksec_summary(stdout: str, stderr: str) -> dict:
    text = stdout or stderr
    low = text.lower()
    return {
        "relro": "full relro" if "full relro" in low else "partial relro" if "partial relro" in low else "",
        "canary": "canary found" in low,
        "nx": "nx enabled" in low,
        "pie": "pie enabled" in low,
    }


def _normalize_xxd_head(stdout: str) -> list[str]:
    return _first_nonempty_lines(stdout, 8)


def _facts_xxd_head(stdout: str) -> dict:
    lines = _first_nonempty_lines(stdout, 8)
    return {
        "hex_rows": lines,
        "row_count": len(lines),
    }


def _normalize_tool_output(name: str, stdout: str, stderr: str) -> list[str]:
    if name == "file_summary":
        return _normalize_file_summary(stdout)
    if name == "readelf_header":
        return _normalize_readelf_header(stdout)
    if name == "readelf_sections":
        return _normalize_readelf_sections(stdout)
    if name == "readelf_symbols":
        return _normalize_readelf_symbols(stdout)
    if name == "objdump_rodata":
        return _normalize_objdump_rodata(stdout)
    if name == "objdump_disassembly":
        return _normalize_objdump_disassembly(stdout)
    if name == "checksec_summary":
        return _normalize_checksec_summary(stdout, stderr)
    if name == "xxd_head":
        return _normalize_xxd_head(stdout)
    return _first_nonempty_lines(stdout or stderr, 8)


def _extract_tool_facts(name: str, stdout: str, stderr: str) -> dict:
    if name == "file_summary":
        return _facts_file_summary(stdout)
    if name == "readelf_header":
        return _facts_readelf_header(stdout)
    if name == "readelf_sections":
        return _facts_readelf_sections(stdout)
    if name == "readelf_symbols":
        return _facts_readelf_symbols(stdout)
    if name == "objdump_rodata":
        return _facts_objdump_rodata(stdout)
    if name == "objdump_disassembly":
        return _facts_objdump_disassembly(stdout)
    if name == "checksec_summary":
        return _facts_checksec_summary(stdout, stderr)
    if name == "xxd_head":
        return _facts_xxd_head(stdout)
    return {}


def _materialize_command(spec: ReverseToolSpec, path: Path, resolved_tool: str) -> list[str]:
    command: list[str] = []
    for idx, part in enumerate(spec.command_template):
        if idx == 0:
            part = resolved_tool
        command.append(part.format(path=str(path)))
    return command


def run_reverse_tool(path: Path, tool_name: str) -> ReverseToolResult:
    spec = get_reverse_tool(tool_name)
    resolved_tool = which(spec.requires_tool)
    if not resolved_tool:
        return ReverseToolResult(
            name=spec.name,
            path=str(path),
            read_only=spec.read_only,
            sandbox_required=spec.sandbox_required,
            timeout_s=spec.timeout_s,
            tool_missing=True,
            summary_lines=[],
            facts={},
            error=f"missing tool: {spec.requires_tool}",
        )

    command = _materialize_command(spec, path, resolved_tool)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=spec.timeout_s,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _cap_output(exc.stdout or "", spec.max_output_chars)
        stderr, stderr_truncated = _cap_output(exc.stderr or "", spec.max_output_chars)
        return ReverseToolResult(
            name=spec.name,
            path=str(path),
            command=command,
            read_only=spec.read_only,
            sandbox_required=spec.sandbox_required,
            timeout_s=spec.timeout_s,
            stdout=stdout,
            stderr=stderr,
            summary_lines=_normalize_tool_output(spec.name, stdout, stderr),
            facts=_extract_tool_facts(spec.name, stdout, stderr),
            truncated=stdout_truncated or stderr_truncated,
            timed_out=True,
            error=f"timed out after {spec.timeout_s:g}s",
        )
    except OSError as exc:
        return ReverseToolResult(
            name=spec.name,
            path=str(path),
            command=command,
            read_only=spec.read_only,
            sandbox_required=spec.sandbox_required,
            timeout_s=spec.timeout_s,
            summary_lines=[],
            facts={},
            error=str(exc),
        )

    stdout, stdout_truncated = _cap_output(proc.stdout or "", spec.max_output_chars)
    stderr, stderr_truncated = _cap_output(proc.stderr or "", spec.max_output_chars)
    return ReverseToolResult(
        name=spec.name,
        path=str(path),
        command=command,
        read_only=spec.read_only,
        sandbox_required=spec.sandbox_required,
        timeout_s=spec.timeout_s,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        summary_lines=_normalize_tool_output(spec.name, stdout, stderr),
        facts=_extract_tool_facts(spec.name, stdout, stderr),
        truncated=stdout_truncated or stderr_truncated,
    )


def format_reverse_tool_result(result: ReverseToolResult) -> str:
    lines = [
        f"- tool={result.name}",
        f"  path={result.path}",
        f"  command={' '.join(result.command) if result.command else '(not run)'}",
        f"  read_only={result.read_only}",
        f"  sandbox_required={result.sandbox_required}",
        f"  timeout_s={result.timeout_s:g}",
    ]
    if result.exit_code is not None:
        lines.append(f"  exit_code={result.exit_code}")
    if result.tool_missing:
        lines.append("  tool_missing=true")
    if result.timed_out:
        lines.append("  timed_out=true")
    lines.append(f"  truncated={result.truncated}")
    if result.summary_lines:
        lines.append("  summary:")
        lines.extend(result.summary_lines)
    if result.facts:
        lines.append(f"  facts={result.facts}")
    if result.stdout:
        lines.append("  stdout_excerpt:")
        lines.append(result.stdout)
    if result.stderr:
        lines.append("  stderr_excerpt:")
        lines.append(result.stderr)
    if result.error:
        lines.append(f"  error={result.error}")
    return "\n".join(lines)
