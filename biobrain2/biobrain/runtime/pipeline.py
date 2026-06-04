"""
biobrain.runtime.pipeline — The full biological processing loop
=================================================================

v0.4: Event bus wired into every pipeline stage.
Every stage emits typed events for observability, dashboards, and replay.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..core.enums import InputSource, ReflexVerdict, ActionType, SystemMode
from ..core.signals import (
    RawInput, MemoryQuery, MemoryResult,
    ActionRequest, ModeState, IdentityState,
)
from ..core.trace import PipelineTrace, ReflexResponse
from ..core.events import EventBus
from ..ingest import ingest_input
from ..perception import perceive
from ..attention import score_salience
from ..safety import check_reflexes
from ..memory import MemoryManager, WorkingMemory
from ..identity import load_identity
from ..executive import decide
from ..cognition import reason
from ..action import execute
from ..feedback import verify, feedback_to_episodic
from ..modulation import ModeManager

logger = logging.getLogger("biobrain.runtime")

MAX_ACTIONS_PER_CYCLE = 5


class BioBrain:
    """The complete biologically-inspired processing pipeline.

    Architecture:
        Sense → Perceive → Attend → [Reflex] → Recall → Decide → Think → Act(s) → Verify → Adapt

    All stages emit events on the event bus for external observability.
    """

    def __init__(
        self,
        palace_path: str,
        kg_path: Optional[str] = None,
        playbook_dir: Optional[str] = None,
        identity_config: Optional[str] = None,
        mempalace_identity: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.memory = MemoryManager(
            palace_path=palace_path, kg_path=kg_path, playbook_dir=playbook_dir,
        )
        self.identity = load_identity(
            config_path=identity_config, mempalace_identity_path=mempalace_identity,
        )
        self.mode_manager = ModeManager()
        self.bus = event_bus or EventBus()
        self._traces: list[PipelineTrace] = []

    @property
    def mode(self) -> ModeState:
        return self.mode_manager.state

    def process(
        self,
        content: str,
        source: InputSource = InputSource.USER,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PipelineTrace:
        """Process a single input through the full pipeline."""
        start = time.time()
        trace = PipelineTrace(mode_at_processing=self.mode)
        metadata = metadata or {}
        sid = metadata.get("session_id", "")

        try:
            # ── 1. SENSE ──────────────────────────────────────────────
            raw = ingest_input(content, source, metadata)
            trace.raw_input = raw
            self.bus.emit_simple("ingest", "input", {
                "source": source.value, "trust": raw.trust.value,
                "length": len(content),
            }, sid)

            # ── 2. PERCEIVE ───────────────────────────────────────────
            perceived = perceive(raw, self.mode)
            trace.perceived = perceived
            self.bus.emit_simple("perception", "classified", {
                "intent": perceived.intent,
                "classification": perceived.classification,
                "operation": perceived.operation_class.value,
                "entities": perceived.entities[:5],
                "risks": perceived.risk_indicators,
            }, sid)

            # ── 3. ATTEND ─────────────────────────────────────────────
            salience = score_salience(perceived, self.mode)
            trace.salience = salience
            self.bus.emit_simple("attention", "scored", {
                "priority": salience.priority.name,
                "risk": salience.risk_score,
                "confidence": salience.confidence,
                "suggested_reasoning": salience.suggested_reasoning.value,
            }, sid)

            # Auto-escalate mode if needed
            new_mode = self.mode_manager.auto_escalate(salience.risk_score, salience.confidence)
            if new_mode:
                self.bus.emit_simple("modulation", "auto_escalated", {
                    "new_mode": new_mode.value,
                }, sid)

            # ── 4. REFLEX ─────────────────────────────────────────────
            reflex = check_reflexes(salience, self.mode)
            trace.reflex = reflex

            if reflex.verdict != ReflexVerdict.PASS:
                self.bus.emit_simple("reflex", reflex.verdict.value, {
                    "rule": reflex.rule_triggered,
                    "reason": reflex.reason,
                }, sid)

            if reflex.verdict == ReflexVerdict.BLOCK:
                trace.halted_at = "reflex_block"
                trace.halt_reason = reflex.reason
                return self._finalize(trace, start, sid)

            if reflex.verdict == ReflexVerdict.ESCALATE:
                trace.halted_at = "reflex_escalate"
                trace.halt_reason = reflex.reason
                return self._finalize(trace, start, sid)

            if reflex.verdict == ReflexVerdict.SANITIZE:
                sanitized = reflex.sanitized_content or ""
                raw = ingest_input(sanitized, source, metadata)
                trace.raw_input = raw
                perceived = perceive(raw, self.mode)
                trace.perceived = perceived
                salience = score_salience(perceived, self.mode)
                trace.salience = salience

            if reflex.verdict == ReflexVerdict.ROUTE:
                trace.halted_at = f"route:{reflex.route_target}"
                trace.halt_reason = reflex.reason
                return self._finalize(trace, start, sid)

            # ── 5. RECALL ─────────────────────────────────────────────
            mem_query = MemoryQuery(
                query=perceived.normalized_content or content,
                wing=metadata.get("wing"),
                room=metadata.get("room"),
            )
            memory_result = self.memory.recall(mem_query, self.mode)
            trace.memory = memory_result
            self.bus.emit_simple("memory", "recalled", {
                "working": len(memory_result.working),
                "episodic": len(memory_result.episodic),
                "semantic": len(memory_result.semantic),
                "procedural": len(memory_result.procedural),
                "kg_facts": len(memory_result.kg_facts),
            }, sid)

            self.memory.working.put(
                key=f"input_{raw.signal_id}",
                value={"content": content, "intent": perceived.intent},
                category="input",
            )

            # ── 6. DECIDE ─────────────────────────────────────────────
            decision = decide(salience, memory_result, self.mode, self.identity)
            trace.decision = decision
            self.bus.emit_simple("executive", "decided", {
                "reasoning": decision.chosen_reasoning.value,
                "actions": [a.value for a in decision.chosen_actions],
                "inhibited": decision.inhibited_actions,
                "policy_notes": decision.policy_notes,
            }, sid)

            if decision.chosen_actions == [ActionType.NO_ACTION] and decision.inhibited_actions:
                trace.halted_at = "executive_inhibition"
                trace.halt_reason = "; ".join(decision.policy_notes)
                return self._finalize(trace, start, sid)

            # ── 7. THINK ──────────────────────────────────────────────
            cognitive = reason(decision, self.mode)
            trace.cognitive = cognitive
            self.bus.emit_simple("cognition", "reasoned", {
                "mode": cognitive.reasoning_mode_used.value,
                "confidence": cognitive.confidence,
                "evidence_count": len(cognitive.evidence),
                "result_length": len(cognitive.result),
            }, sid)

            # ── 8–10. ACT → VERIFY → ADAPT ───────────────────────────
            actions_executed = 0
            for action_type in decision.chosen_actions:
                if action_type == ActionType.NO_ACTION:
                    continue
                if actions_executed >= MAX_ACTIONS_PER_CYCLE:
                    break

                action_req = ActionRequest(
                    action_type=action_type,
                    cognitive_result=cognitive,
                    parameters=metadata,
                    requires_confirmation=metadata.get("confirmed", False),
                )
                action_result = execute(action_req, self.mode)
                trace.action_results.append(action_result)
                self.bus.emit_simple("action", "executed", {
                    "type": action_type.value,
                    "success": action_result.success,
                    "tool": action_result.tool_name,
                    "time_ms": action_result.execution_time_ms,
                    "error": action_result.error,
                }, sid)

                feedback = verify(action_result, self.mode)
                trace.feedback_results.append(feedback)
                if not feedback.expectation_met:
                    self.bus.emit_simple("feedback", "mismatch", {
                        "prediction_error": feedback.prediction_error,
                        "corrections": feedback.corrections,
                        "should_retry": feedback.should_retry,
                    }, sid)

                self.memory.working.put(
                    key=f"fb_{action_result.signal_id}",
                    value={"success": action_result.success, "error": feedback.prediction_error},
                    category="feedback",
                )

                episodic_entry = feedback_to_episodic(feedback)
                if episodic_entry:
                    wing = metadata.get("wing", "wing_general")
                    room = perceived.classification or "general"
                    self.memory.store_episodic(
                        content=episodic_entry["content"], wing=wing, room=room,
                        hall=episodic_entry.get("hall", "hall_events"),
                    )

                actions_executed += 1
                if action_type == ActionType.ESCALATION:
                    break
                if not action_result.success and not feedback.should_retry:
                    break

        except Exception as e:
            trace.halted_at = f"exception:{type(e).__name__}"
            trace.halt_reason = str(e)
            self.bus.emit_simple("pipeline", "exception", {
                "type": type(e).__name__, "message": str(e),
            }, sid)
            logger.error("Pipeline exception: %s", e, exc_info=True)

        return self._finalize(trace, start, sid)

    def _finalize(self, trace: PipelineTrace, start: float, session_id: str = "") -> PipelineTrace:
        trace.elapsed_ms = (time.time() - start) * 1000
        self._traces.append(trace)
        if len(self._traces) > 100:
            self._traces = self._traces[-100:]
        self.bus.emit_simple("pipeline", "finalized", {
            "elapsed_ms": trace.elapsed_ms,
            "halted_at": trace.halted_at,
            "summary": trace.audit_summary,
        }, session_id)
        logger.info("Trace: %s", trace.audit_summary)
        return trace

    def wake_up(self, wing: Optional[str] = None) -> dict[str, Any]:
        return self.memory.wake_up(wing=wing)

    @property
    def traces(self) -> list[PipelineTrace]:
        return list(self._traces)

    @property
    def last_trace(self) -> Optional[PipelineTrace]:
        return self._traces[-1] if self._traces else None
