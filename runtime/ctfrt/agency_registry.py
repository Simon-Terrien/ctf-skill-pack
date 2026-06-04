"""Registry for optional advisory intelligence services.

The runtime defaults to null services so ctfrt can boot without any external
intelligence repositories present.
"""
from __future__ import annotations

import os

from .intelligence import NullIntelligenceService


def _enabled(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def build_internal_knowledge_agency():
    if not _enabled("CTF_INTELLIGENCE_INTERNAL"):
        return NullIntelligenceService()
    return NullIntelligenceService("internal_intelligence_adapter_unavailable")


def build_external_intelligence_agency():
    if not _enabled("CTF_INTELLIGENCE_EXTERNAL"):
        return NullIntelligenceService()
    return NullIntelligenceService("external_intelligence_adapter_unavailable")
