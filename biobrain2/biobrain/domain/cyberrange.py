"""
biobrain.domain.cyberrange — CyberRange exercise runner
==========================================================

Drives CyberRange AI training exercises through the BioBrain
orchestrator, using OWASP playbooks as the step plan and
pentest tools as the action layer.

Integration:
  - PlaybookEngine provides the plan (WSTG steps)
  - Orchestrator executes with guards
  - Export module formats results for the CyberRange UI
  - Event bus feeds the training room dashboard

Exercise rooms (from AISEC curriculum):
  - Room 01: Prompt failure analysis
  - Room 02: Broken RAG patterns
  - Room 03: Unsafe agent design

Usage:
    from biobrain.domain.cyberrange import ExerciseRunner

    runner = ExerciseRunner(brain, playbook_dir="./configs/playbooks")
    result = runner.run_exercise(
        exercise_id="EX-AUTH-001",
        playbook="owasp_auth_testing",
        target="https://target.example.com",
        room="room_03",
    )
    print(result["report_md"])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..core.enums import InputSource, SystemMode
from ..runtime.orchestrator import Orchestrator, OrchestrationResult
from ..ops.export import orchestration_to_markdown, to_aisec_exercise
from .playbooks import PlaybookEngine
from .pentest_tools import register_pentest_tools

logger = logging.getLogger("biobrain.domain.cyberrange")


class ExerciseRunner:
    """Runs CyberRange exercises through BioBrain.

    Combines:
      - PlaybookEngine for structured WSTG step plans
      - Orchestrator for bounded execution
      - Pentest tools for security actions
      - Export for training room rendering
    """

    def __init__(
        self,
        brain,
        playbook_dir: str = "./configs/playbooks",
        max_steps: int = 15,
        timeout_seconds: float = 600.0,
    ):
        self.brain = brain
        self.playbook_engine = PlaybookEngine(playbook_dir)
        self.playbook_engine.load_all()
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

        # Register pentest tools
        self._tools_registered = register_pentest_tools()
        logger.info(
            "ExerciseRunner ready: %d playbooks, %d tools",
            len(self.playbook_engine.available),
            self._tools_registered,
        )

    def run_exercise(
        self,
        exercise_id: str,
        playbook: str,
        target: str = "",
        room: str = "room_03",
        difficulty: str = "intermediate",
        mode: SystemMode = SystemMode.AUDIT,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run a single CyberRange exercise.

        Args:
            exercise_id: Unique exercise identifier (e.g. "EX-AUTH-001")
            playbook: Name of the OWASP playbook to use
            target: Target URL/host for the exercise
            room: CyberRange room (room_01, room_02, room_03)
            difficulty: Exercise difficulty level
            mode: System mode for the exercise (default: AUDIT)
            metadata: Additional metadata to pass through

        Returns:
            dict with exercise results, markdown report, and AISEC format
        """
        start = time.time()

        # Set mode for exercise
        self.brain.mode_manager.transition(mode, f"exercise:{exercise_id}")

        # Get playbook steps
        steps = self.playbook_engine.to_orchestrator_steps(playbook)
        if not steps:
            return {
                "exercise_id": exercise_id,
                "error": f"Playbook '{playbook}' not found or empty",
                "available_playbooks": self.playbook_engine.available,
            }

        # Inject target into steps
        if target:
            steps = [f"{step} [target: {target}]" for step in steps]

        # Create a planner that returns the playbook steps
        playbook_planner = lambda goal: steps

        # Build and run orchestrator
        orch = Orchestrator(
            self.brain,
            max_steps=self.max_steps,
            timeout_seconds=self.timeout_seconds,
            halt_on_escalation=False,  # exercises should continue through escalations
            planner=playbook_planner,
            wing=f"wing_exercise_{exercise_id}",
            room=room,
        )

        goal = f"Execute {playbook} against {target or 'target'}"
        orch_result = orch.run(goal, metadata=metadata or {})

        # Generate outputs
        report_md = orchestration_to_markdown(orch_result)
        aisec_data = to_aisec_exercise(
            orch_result,
            exercise_id=exercise_id,
            room=room,
            difficulty=difficulty,
        )

        # Get procedural memory items for the training room
        memory_items = self.playbook_engine.to_memory_items(playbook)

        elapsed = time.time() - start

        return {
            "exercise_id": exercise_id,
            "playbook": playbook,
            "target": target,
            "room": room,
            "difficulty": difficulty,
            "completed": orch_result.completed,
            "halt_reason": orch_result.halt_reason,
            "total_steps": orch_result.total_steps,
            "total_replans": orch_result.total_replans,
            "elapsed_s": round(elapsed, 2),
            "report_md": report_md,
            "aisec_exercise": aisec_data,
            "orchestration": orch_result,
            "playbook_items": len(memory_items),
        }

    def list_exercises(self) -> list[dict[str, Any]]:
        """List available exercises based on loaded playbooks."""
        exercises = []
        for name in self.playbook_engine.available:
            pb = self.playbook_engine.get(name)
            if pb:
                steps = pb.get("steps", [])
                exercises.append({
                    "playbook": name,
                    "description": pb.get("description", ""),
                    "steps_count": len(steps),
                    "wstg_ids": [s.get("id", "") for s in steps],
                    "triggers": pb.get("triggers", []),
                })
        return exercises

    def run_by_trigger(
        self,
        trigger_query: str,
        target: str = "",
        exercise_id: Optional[str] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Find and run an exercise by matching trigger keywords.

        Example: runner.run_by_trigger("authentication bypass", target="https://...")
        """
        pb = self.playbook_engine.match(trigger_query)
        if not pb:
            return {
                "error": f"No playbook matched trigger: '{trigger_query}'",
                "available_playbooks": self.playbook_engine.available,
            }

        eid = exercise_id or f"EX-{pb['name'][:8].upper()}-{int(time.time()) % 10000:04d}"

        return self.run_exercise(
            exercise_id=eid,
            playbook=pb["name"],
            target=target,
            **kwargs,
        )
