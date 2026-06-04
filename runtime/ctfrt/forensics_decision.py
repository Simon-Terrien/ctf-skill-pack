"""Decision rules for the forensics specialist.

Maps artifact magic / extension / content signals to next analysis actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class ForensicsArtifactSignals:
    kind: str = "unknown"           # pcap, disk, memory, log, archive, text
    file_size: int = 0
    magic_hex: str = ""
    extensions_found: list[str] = field(default_factory=list)
    string_snippets: list[str] = field(default_factory=list)
    has_timestamps: bool = False
    has_ip_addresses: bool = False
    has_http: bool = False


def analyze_forensics_artifact(path_bytes: bytes, filename: str = "") -> ForensicsArtifactSignals:
    import re
    sig = ForensicsArtifactSignals(file_size=len(path_bytes))
    head = path_bytes[:16]
    sig.magic_hex = head.hex()

    if head[:4] == b"\xd4\xc3\xb2\xa1" or head[:4] == b"\xa1\xb2\xc3\xd4":
        sig.kind = "pcap"
    elif head[:4] == b"\x0a\x0d\x0d\x0a":
        sig.kind = "pcapng"
    elif head[:8] in (b"NTFS    ", b"FAT32   ") or (len(path_bytes) % 512 == 0 and len(path_bytes) > 512 * 100):
        sig.kind = "disk"
    elif head[:4] == b"PAGEDUMP" or head[:4] == b"MDMP":
        sig.kind = "memory"
    elif filename.endswith((".log", ".txt")) or all(32 <= b <= 126 or b in (9, 10, 13) for b in path_bytes[:512]):
        sig.kind = "log"
    elif head[:2] in (b"PK", b"\x1f\x8b"):
        sig.kind = "archive"
    else:
        sig.kind = "binary"

    text = path_bytes.decode("latin-1", errors="ignore")
    sig.has_timestamps = bool(re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", text))
    sig.has_ip_addresses = bool(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text))
    sig.has_http = bool(re.search(r"HTTP/[12]\b|GET |POST |Host:", text))

    snippets = [s for s in re.findall(r"[A-Za-z0-9_\-./]{6,}", text) if len(s) < 80]
    sig.string_snippets = snippets[:20]
    return sig


class ForensicsDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0


def evaluate_forensics_decision(signals: ForensicsArtifactSignals) -> ForensicsDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []

    if signals.kind in ("pcap", "pcapng"):
        rules.append("pcap_detected")
        actions.extend(["pcap_summary", "extract_strings"])
        techniques.extend(["tshark", "pcap-analysis"])
        if signals.has_http:
            actions.append("extract_http_streams")
            techniques.append("http-forensics")
        if signals.has_ip_addresses:
            actions.append("extract_ip_list")

    elif signals.kind == "disk":
        rules.append("disk_image_detected")
        actions.extend(["strings_extraction", "file_carving"])
        techniques.extend(["binwalk", "disk-forensics"])

    elif signals.kind == "memory":
        rules.append("memory_dump_detected")
        actions.extend(["strings_extraction", "volatility_pslist"])
        techniques.extend(["volatility", "memory-forensics"])

    elif signals.kind == "archive":
        rules.append("archive_detected")
        actions.extend(["archive_listing", "strings_extraction"])
        techniques.append("archive-forensics")

    elif signals.kind == "log":
        rules.append("log_file_detected")
        actions.extend(["keyword_search", "timeline_extraction"])
        techniques.append("log-analysis")

    else:
        if signals.string_snippets:
            rules.append("generic_binary_with_strings")
            actions.extend(["strings_extraction", "xxd_head"])
            techniques.append("binary-forensics")

    confidence = min(0.9, 0.2 * len(rules)) if rules else 0.0
    return ForensicsDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
    )
