"""Registry for optional advisory intelligence services.

The runtime defaults to null services so ctfrt can boot without any external
intelligence repositories present. Set environment variables to enable real adapters:

    CTF_INTELLIGENCE_INTERNAL=agentic_rag     → loads integrations/agentic_rag/
    CTF_INTELLIGENCE_EXTERNAL=enhanced_deep_search → loads integrations/enhanced_deep_search/
"""
from __future__ import annotations

import os

from .intelligence import NullIntelligenceService
from .log import get_logger, kv

log = get_logger(__name__)


def _env(key: str) -> str:
    return os.getenv(key, "").strip().lower()


def build_internal_knowledge_agency():
    adapter = _env("CTF_INTELLIGENCE_INTERNAL")
    if not adapter or adapter in ("0", "false", "none"):
        return NullIntelligenceService()
    if adapter == "agentic_rag":
        try:
            from integrations.agentic_rag import AgenticRagService  # lazy
            log.info("intelligence adapter loaded", extra=kv(adapter="agentic_rag"))
            return AgenticRagService()
        except ImportError as exc:
            log.warning("agentic_rag adapter unavailable", extra=kv(error=repr(exc)))
    return NullIntelligenceService("internal_intelligence_adapter_unavailable")


def build_external_intelligence_agency():
    adapter = _env("CTF_INTELLIGENCE_EXTERNAL")
    if not adapter or adapter in ("0", "false", "none"):
        return NullIntelligenceService()
    if adapter == "enhanced_deep_search":
        try:
            from integrations.enhanced_deep_search import EnhancedDeepSearchService  # lazy
            log.info("intelligence adapter loaded", extra=kv(adapter="enhanced_deep_search"))
            return EnhancedDeepSearchService()
        except ImportError as exc:
            log.warning("enhanced_deep_search adapter unavailable", extra=kv(error=repr(exc)))
    return NullIntelligenceService("external_intelligence_adapter_unavailable")
