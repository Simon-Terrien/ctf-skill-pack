"""
biobrain.ingest.intake — Source-aware signal ingestion
=======================================================

Every input enters through ingest_input() which routes based on
InputSource to apply correct trust levels. This is the P0 fix
from the review: the pipeline now honors the source parameter.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.enums import InputSource, TrustLevel
from ..core.signals import RawInput

logger = logging.getLogger("biobrain.ingest")


# ─── Trust policy: source → default trust ─────────────────────────────────────

DEFAULT_TRUST: dict[InputSource, TrustLevel] = {
    InputSource.USER: TrustLevel.TRUSTED,
    InputSource.DOCUMENT: TrustLevel.TRUSTED,
    InputSource.TOOL_RESULT: TrustLevel.TRUSTED,
    InputSource.API_RESPONSE: TrustLevel.UNTRUSTED,
    InputSource.LOG: TrustLevel.TRUSTED,
    InputSource.WEB: TrustLevel.UNTRUSTED,
    InputSource.INTERNAL: TrustLevel.VERIFIED,
}


def ingest_input(
    content: str,
    source: InputSource,
    metadata: Optional[dict[str, Any]] = None,
) -> RawInput:
    """Unified input ingestion. Routes to correct trust level by source.

    This is the single entry point. The pipeline calls this, not
    source-specific functions directly.
    """
    trust = DEFAULT_TRUST.get(source, TrustLevel.UNTRUSTED)
    return RawInput(
        content=content,
        source=source,
        trust=trust,
        metadata=metadata or {},
    )


def override_trust(raw: RawInput, trust: TrustLevel, reason: str) -> RawInput:
    """Explicitly override trust level with audit trail."""
    logger.info("Trust override: %s → %s (reason: %s)", raw.trust.value, trust.value, reason)
    raw.trust = trust
    raw.metadata["trust_override"] = {"new_trust": trust.value, "reason": reason}
    return raw
