"""
biobrain.memory.adapters — Memory backend protocol and implementations
========================================================================

MemoryBackend protocol: implement to swap storage providers.
Included: MemPalaceBackend (production), NullBackend (testing).
"""

from .mempalace import MemoryBackend, MemPalaceBackend, NullBackend

__all__ = ["MemoryBackend", "MemPalaceBackend", "NullBackend"]
