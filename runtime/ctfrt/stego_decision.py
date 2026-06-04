"""Decision rules for the steganography specialist.

Maps image/audio artifact signals to analysis actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class StegoArtifactSignals:
    kind: str = "unknown"       # png, jpeg, gif, bmp, wav, mp3, text
    file_size: int = 0
    magic_hex: str = ""
    has_exif: bool = False
    has_comment_chunk: bool = False
    has_icc_profile: bool = False
    lsb_entropy_hint: bool = False  # placeholder for future LSB analysis
    string_snippets: list[str] = field(default_factory=list)


def analyze_stego_artifact(data: bytes, filename: str = "") -> StegoArtifactSignals:
    import re
    sig = StegoArtifactSignals(file_size=len(data))
    head = data[:16]
    sig.magic_hex = head.hex()

    if head[:8] == b"\x89PNG\r\n\x1a\n":
        sig.kind = "png"
        if b"tEXt" in data or b"iTXt" in data or b"zTXt" in data:
            sig.has_comment_chunk = True
        if b"iCCP" in data:
            sig.has_icc_profile = True
    elif head[:3] == b"\xff\xd8\xff":
        sig.kind = "jpeg"
        if b"Exif" in data[:64]:
            sig.has_exif = True
        if b"\xff\xfe" in data or b"\xff\xc0" in data:
            sig.has_comment_chunk = True
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        sig.kind = "gif"
        if b"\x21\xfe" in data:
            sig.has_comment_chunk = True
    elif head[:2] == b"BM":
        sig.kind = "bmp"
    elif head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        sig.kind = "wav"
    elif head[:3] == b"ID3" or head[:2] == b"\xff\xfb":
        sig.kind = "mp3"
    else:
        text = data.decode("latin-1", errors="ignore")
        if all(32 <= ord(c) <= 126 or c in "\n\r\t" for c in text[:512]):
            sig.kind = "text"

    text = data.decode("latin-1", errors="ignore")
    snippets = [s for s in re.findall(r"[A-Za-z0-9_\-./]{6,}", text) if len(s) < 80]
    sig.string_snippets = snippets[:20]
    return sig


class StegoDecision(BaseModel):
    matched_rules: list[str] = []
    next_actions: list[str] = []
    inferred_techniques: list[str] = []
    confidence: float = 0.0


def evaluate_stego_decision(signals: StegoArtifactSignals) -> StegoDecision:
    rules: list[str] = []
    actions: list[str] = []
    techniques: list[str] = []

    if signals.kind in ("png", "jpeg", "bmp", "gif"):
        rules.append(f"{signals.kind}_image_detected")
        actions.extend(["extract_metadata", "strings_extraction", "lsb_scan"])
        techniques.extend(["metadata", "lsb"])
        if signals.has_exif:
            actions.append("exif_dump")
            techniques.append("exif")
        if signals.has_comment_chunk:
            actions.append("extract_comment_chunk")
            techniques.append("metadata")
        if signals.has_icc_profile:
            actions.append("extract_icc_profile")

    elif signals.kind == "wav":
        rules.append("wav_audio_detected")
        actions.extend(["spectrogram_analysis", "strings_extraction"])
        techniques.extend(["spectrogram", "audio-stego"])

    elif signals.kind == "mp3":
        rules.append("mp3_audio_detected")
        actions.extend(["extract_id3_tags", "strings_extraction"])
        techniques.extend(["metadata", "audio-stego"])

    elif signals.kind == "text":
        rules.append("text_artifact")
        actions.extend(["whitespace_analysis", "unicode_hidden_chars"])
        techniques.extend(["whitespace-stego", "unicode-stego"])

    if signals.string_snippets:
        rules.append("strings_present")
        if "strings_extraction" not in actions:
            actions.append("strings_extraction")

    confidence = min(0.9, 0.2 * len(rules)) if rules else 0.0
    return StegoDecision(
        matched_rules=rules,
        next_actions=list(dict.fromkeys(actions)),
        inferred_techniques=list(dict.fromkeys(techniques)),
        confidence=confidence,
    )
