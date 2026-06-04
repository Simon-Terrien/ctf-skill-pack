"""Read-only reverse pre-analysis for local artifacts."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field


_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")
_MAX_STRINGS = 24
_MAX_IMPORTS = 64
_MAX_SECTIONS = 64
_MAX_DETAIL_LINES = 80
_MAX_DETAIL_CHARS = 6000
_ANCHOR_WORDS = ("pass", "password", "wrong", "correct", "success", "fail", "usage", "key", "secret")
_COMPARE_IMPORTS = {"strcmp", "strncmp", "memcmp"}
_INPUT_IMPORTS = {"scanf", "fgets", "read", "gets", "getline", "getchar"}


class ReverseArtifactSummary(BaseModel):
    path: str
    kind: str
    magic: str
    size: int
    sha256: str
    strings: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    stripped: bool | None = None
    pie: bool | None = None
    dynamically_linked: bool | None = None
    tools_used: list[str] = Field(default_factory=list)


class StaticDetailSummary(BaseModel):
    path: str
    tool_used: str
    line_count: int
    truncated: bool
    interesting_strings: list[str] = Field(default_factory=list)
    imported_compare_symbols: list[str] = Field(default_factory=list)
    imported_input_symbols: list[str] = Field(default_factory=list)
    candidate_anchors: list[str] = Field(default_factory=list)
    disassembly_excerpt: str = ""


def _kind_and_magic(data: bytes) -> tuple[str, str]:
    head = data[:16]
    magic = head.hex()
    if head.startswith(b"\x7fELF"):
        return "elf", magic
    if head.startswith(b"MZ"):
        return "pe", magic
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return "mach-o", magic
    return "binary", magic


def _extract_strings(data: bytes) -> list[str]:
    strings: list[str] = []
    seen: set[str] = set()
    for raw in _PRINTABLE_RE.findall(data):
        text = raw.decode("latin-1", errors="ignore").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        strings.append(text)
        if len(strings) >= _MAX_STRINGS:
            break
    return strings


def _run_tool(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    return proc.stdout if proc.stdout else proc.stderr


def _parse_readelf_sections(text: str) -> list[str]:
    sections: list[str] = []
    for line in text.splitlines():
        match = re.search(r"\[\s*\d+\]\s+([.\w$@+-]+)", line)
        if match:
            sections.append(match.group(1))
        if len(sections) >= _MAX_SECTIONS:
            break
    return sections


def _parse_readelf_imports(text: str) -> list[str]:
    imports: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if " UND " not in f" {line} ":
            continue
        match = re.search(r"\b([A-Za-z_][A-Za-z0-9_@.]*)$", line.strip())
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        imports.append(name)
        if len(imports) >= _MAX_IMPORTS:
            break
    return imports


def _parse_readelf_header(text: str) -> tuple[bool | None, bool | None]:
    pie = None
    dynamically_linked = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Type:"):
            if "DYN" in stripped:
                pie = True
            elif "EXEC" in stripped:
                pie = False
        if "Requesting program interpreter:" in stripped or stripped.startswith("INTERP"):
            dynamically_linked = True
    return pie, dynamically_linked


def _parse_objdump_sections(text: str) -> list[str]:
    sections: list[str] = []
    for line in text.splitlines():
        match = re.match(r"\s*\d+\s+([.\w$@+-]+)\s+[0-9a-fA-F]+", line)
        if match:
            sections.append(match.group(1))
        if len(sections) >= _MAX_SECTIONS:
            break
    return sections


def _parse_objdump_imports(text: str) -> list[str]:
    imports: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "*UND*" not in line:
            continue
        match = re.search(r"\*UND\*\s+([A-Za-z_][A-Za-z0-9_@.]*)$", line.strip())
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        imports.append(name)
        if len(imports) >= _MAX_IMPORTS:
            break
    return imports


def analyze_artifact(path: Path) -> ReverseArtifactSummary:
    data = path.read_bytes()
    kind, magic = _kind_and_magic(data)
    sha256 = hashlib.sha256(data).hexdigest()
    strings = _extract_strings(data)
    imports: list[str] = []
    sections: list[str] = []
    stripped: bool | None = None
    pie: bool | None = None
    dynamically_linked: bool | None = None
    tools_used = ["embedded_strings"]

    if kind == "elf":
        readelf = shutil.which("readelf")
        objdump = shutil.which("objdump")
        if readelf:
            tools_used.append("readelf")
            header_text = _run_tool([readelf, "-h", "-l", str(path)])
            pie, dynamically_linked = _parse_readelf_header(header_text)
            section_text = _run_tool([readelf, "-S", str(path)])
            sections = _parse_readelf_sections(section_text)
            symbol_text = _run_tool([readelf, "-Ws", str(path)])
            imports = _parse_readelf_imports(symbol_text)
            stripped = ".symtab" not in sections if sections else None
        elif objdump:
            tools_used.append("objdump")
            dump_text = _run_tool([objdump, "-x", str(path)])
            sections = _parse_objdump_sections(dump_text)
            imports = _parse_objdump_imports(dump_text)
            stripped = ".symtab" not in sections if sections else None

    return ReverseArtifactSummary(
        path=str(path),
        kind=kind,
        magic=magic,
        size=len(data),
        sha256=sha256,
        strings=strings,
        imports=imports,
        sections=sections,
        stripped=stripped,
        pie=pie,
        dynamically_linked=dynamically_linked,
        tools_used=tools_used,
    )


def format_reverse_summary(summary: ReverseArtifactSummary) -> str:
    lines = [
        f"- path={summary.path}",
        f"  kind={summary.kind}",
        f"  size={summary.size}",
        f"  sha256={summary.sha256}",
        f"  magic={summary.magic}",
    ]
    if summary.stripped is not None:
        lines.append(f"  stripped={summary.stripped}")
    if summary.pie is not None:
        lines.append(f"  pie={summary.pie}")
    if summary.dynamically_linked is not None:
        lines.append(f"  dynamically_linked={summary.dynamically_linked}")
    if summary.sections:
        lines.append("  sections=" + ", ".join(summary.sections[:_MAX_SECTIONS]))
    if summary.imports:
        lines.append("  imports=" + ", ".join(summary.imports[:_MAX_IMPORTS]))
    if summary.strings:
        lines.append("  strings=" + " | ".join(summary.strings[:_MAX_STRINGS]))
    lines.append("  tools_used=" + ",".join(summary.tools_used))
    return "\n".join(lines)


def _interesting_strings(strings: list[str]) -> list[str]:
    picked: list[str] = []
    seen: set[str] = set()
    for value in strings:
        low = value.lower()
        if not any(anchor in low for anchor in _ANCHOR_WORDS):
            continue
        if value in seen:
            continue
        seen.add(value)
        picked.append(value)
        if len(picked) >= 12:
            break
    return picked


def _candidate_anchors(strings: list[str]) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()
    for value in strings:
        low = value.lower()
        if not any(anchor in low for anchor in _ANCHOR_WORDS):
            continue
        compact = value.strip()
        if compact in seen:
            continue
        seen.add(compact)
        anchors.append(compact)
        if len(anchors) >= 12:
            break
    return anchors


def _classify_imports(imports: list[str], wanted: set[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for name in imports:
        base = name.split("@", 1)[0]
        if base not in wanted or base in seen:
            continue
        seen.add(base)
        found.append(base)
    return found


def _cap_disassembly_excerpt(text: str) -> tuple[str, int, bool]:
    lines = text.splitlines()
    kept: list[str] = []
    size = 0
    truncated = False
    for line in lines:
        next_size = size + len(line) + 1
        if len(kept) >= _MAX_DETAIL_LINES or next_size > _MAX_DETAIL_CHARS:
            truncated = True
            break
        kept.append(line)
        size = next_size
    excerpt = "\n".join(kept)
    return excerpt, len(kept), truncated or len(kept) < len(lines)


def collect_static_detail(
    path: Path,
    preanalysis: ReverseArtifactSummary | None = None,
) -> StaticDetailSummary:
    summary = preanalysis or analyze_artifact(path)
    interesting_strings = _interesting_strings(summary.strings)
    candidate_anchors = _candidate_anchors(summary.strings)
    imported_compare_symbols = _classify_imports(summary.imports, _COMPARE_IMPORTS)
    imported_input_symbols = _classify_imports(summary.imports, _INPUT_IMPORTS)

    tool_used = "none"
    line_count = 0
    truncated = False
    disassembly_excerpt = ""

    if summary.kind == "elf":
        objdump = shutil.which("objdump")
        if objdump:
            tool_used = "objdump"
            dump_text = _run_tool([objdump, "-d", "-M", "intel", str(path)])
            disassembly_excerpt, line_count, truncated = _cap_disassembly_excerpt(dump_text)

    return StaticDetailSummary(
        path=str(path),
        tool_used=tool_used,
        line_count=line_count,
        truncated=truncated,
        interesting_strings=interesting_strings,
        imported_compare_symbols=imported_compare_symbols,
        imported_input_symbols=imported_input_symbols,
        candidate_anchors=candidate_anchors,
        disassembly_excerpt=disassembly_excerpt,
    )


def format_static_detail(summary: StaticDetailSummary) -> str:
    lines = [
        f"- path={summary.path}",
        f"  tool_used={summary.tool_used}",
        f"  line_count={summary.line_count}",
        f"  truncated={summary.truncated}",
    ]
    if summary.candidate_anchors:
        lines.append("  candidate_anchors=" + " | ".join(summary.candidate_anchors))
    if summary.interesting_strings:
        lines.append("  interesting_strings=" + " | ".join(summary.interesting_strings))
    if summary.imported_compare_symbols:
        lines.append("  compare_imports=" + ",".join(summary.imported_compare_symbols))
    if summary.imported_input_symbols:
        lines.append("  input_imports=" + ",".join(summary.imported_input_symbols))
    if summary.disassembly_excerpt:
        lines.append("  disassembly_excerpt:")
        lines.append(summary.disassembly_excerpt)
    return "\n".join(lines)
