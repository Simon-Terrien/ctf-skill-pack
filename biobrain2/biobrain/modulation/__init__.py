"""
biobrain.modulation — Global operational mode management
==========================================================
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..core.enums import SystemMode
from ..core.signals import ModeState

logger = logging.getLogger("biobrain.modulation")


class ModeManager:
    """Manages global system mode with transition logic."""

    def __init__(self):
        self._state = ModeState()
        self._history: list[dict] = []
        self._transition_time: float = time.time()

    @property
    def state(self) -> ModeState:
        return self._state

    def transition(
        self, new_mode: SystemMode, reason: str,
        risk_level: Optional[float] = None,
        constraints: Optional[list[str]] = None,
    ) -> ModeState:
        old = self._state.mode
        if old == new_mode:
            return self._state

        self._history.append({
            "from": old.value, "to": new_mode.value,
            "reason": reason, "timestamp": time.time(),
        })
        logger.info("MODE: %s → %s (%s)", old.value, new_mode.value, reason)

        self._state.mode = new_mode
        self._state.mode_reason = reason
        self._transition_time = time.time()
        if risk_level is not None:
            self._state.risk_level = risk_level
        if constraints:
            self._state.active_constraints = constraints

        _DEFAULTS = {
            SystemMode.NORMAL:             (0.3, 0.8),
            SystemMode.RISK:               (0.5, 0.5),
            SystemMode.LOW_CONFIDENCE:     (0.6, 0.4),
            SystemMode.INCIDENT:           (0.2, 0.9),
            SystemMode.AUDIT:              (0.6, 0.3),
            SystemMode.BUDGET_CONSTRAINED: (0.3, 0.7),
            SystemMode.AUTONOMOUS:         (0.2, 1.0),
            SystemMode.USER_FACING:        (0.4, 0.6),
        }
        floor, ceiling = _DEFAULTS.get(new_mode, (0.3, 0.8))
        self._state.confidence_floor = floor
        self._state.autonomy_ceiling = ceiling

        return self._state

    def auto_escalate(self, risk_score: float, confidence: float) -> Optional[SystemMode]:
        current = self._state.mode
        if risk_score >= 0.8 and current not in (SystemMode.INCIDENT, SystemMode.RISK):
            self.transition(SystemMode.RISK, f"auto:risk={risk_score}")
            return SystemMode.RISK
        if confidence < 0.25 and current != SystemMode.LOW_CONFIDENCE:
            self.transition(SystemMode.LOW_CONFIDENCE, f"auto:confidence={confidence}")
            return SystemMode.LOW_CONFIDENCE
        return None

    def reset(self, reason: str = "manual_reset") -> ModeState:
        return self.transition(SystemMode.NORMAL, reason)

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def time_in_current_mode(self) -> float:
        return time.time() - self._transition_time
