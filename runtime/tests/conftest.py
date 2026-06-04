"""Shared pytest fixtures for ctfrt tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctfrt.bus import InMemoryBus
from ctfrt.memory import InMemoryWorkingMemory


@pytest.fixture
def _tmp_path(tmp_path: Path) -> Path:
    """Alias so tests that use _tmp_path (underscore convention) work under pytest."""
    return tmp_path


@pytest.fixture
def bus():
    return InMemoryBus()


@pytest.fixture
def mem():
    return InMemoryWorkingMemory()


# ── challenge fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def embedded_flag_artifact(tmp_path: Path) -> Path:
    art = tmp_path / "embedded_flag.txt"
    art.write_text("CTF{static_embedded_win} noise around the flag")
    return art


@pytest.fixture
def xor_crackme_artifact(tmp_path: Path) -> Path:
    flag = "CTF{xor_reversed}"
    key = 90
    blob = bytes(ord(c) ^ key for c in flag)
    art = tmp_path / "xor_crackme.json"
    art.write_text(json.dumps({
        "type": "xor-crackme",
        "xor_key": key,
        "blob_hex": blob.hex(),
    }))
    return art


@pytest.fixture
def fake_elf_strcmp_artifact(tmp_path: Path) -> Path:
    art = tmp_path / "fake_elf_strcmp.bin"
    art.write_bytes(
        b"\x7fELF"
        + b"\x00" * 60
        + b"usage: %s <password>\x00Wrong password\x00You cracked it\x00"
        + b"strcmp\x00"
    )
    return art


@pytest.fixture
def fake_png_artifact(tmp_path: Path) -> Path:
    art = tmp_path / "fake_png.bin"
    art.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 20
        + b"Wrong pass\x00CTF{stego_win}\x00"
    )
    return art


@pytest.fixture
def fake_pcap_artifact(tmp_path: Path) -> Path:
    art = tmp_path / "fake_pcap.bin"
    # PCAP global header magic (little-endian)
    art.write_bytes(b"\xd4\xc3\xb2\xa1" + b"\x00" * 20 + b"packet noise\x00")
    return art
