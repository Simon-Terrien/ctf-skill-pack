"""
biobrain.domain.playbooks — OWASP playbook engine
====================================================

Loads YAML playbooks (WSTG-mapped) and provides them as structured
procedural memory that the checklist reasoner can follow step by step.

Integration with cognition:
  The ChecklistReasoner reads procedural memory items. This module
  converts OWASP playbook YAML into MemoryItem objects that the
  reasoner can iterate over.

Usage:
    from biobrain.domain.playbooks import PlaybookEngine

    engine = PlaybookEngine("./configs/playbooks")
    engine.load_all()

    # Match a playbook to an intent
    pb = engine.match("authentication bypass")
    # pb = {"name": "owasp_auth_testing", "steps": [...], ...}

    # Get procedural memory items for the checklist reasoner
    items = engine.to_memory_items("owasp_auth_testing")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..core.enums import TrustLevel
from ..core.signals import MemoryItem

logger = logging.getLogger("biobrain.domain.playbooks")


class PlaybookEngine:
    """Loads, indexes, and serves OWASP WSTG playbooks."""

    def __init__(self, playbook_dir: str):
        self.playbook_dir = Path(playbook_dir)
        self._playbooks: dict[str, dict[str, Any]] = {}

    def load_all(self) -> int:
        """Load all YAML playbooks from the directory."""
        if not self.playbook_dir.exists():
            logger.warning("Playbook directory not found: %s", self.playbook_dir)
            return 0

        import yaml
        count = 0
        for f in sorted(self.playbook_dir.glob("*.yaml")):
            try:
                with open(f) as fh:
                    data = yaml.safe_load(fh)
                if data and isinstance(data, dict) and "name" in data:
                    self._playbooks[data["name"]] = data
                    count += 1
            except Exception as e:
                logger.warning("Failed to load playbook %s: %s", f, e)

        logger.info("Loaded %d playbooks from %s", count, self.playbook_dir)
        return count

    @property
    def available(self) -> list[str]:
        """List available playbook names."""
        return list(self._playbooks.keys())

    def get(self, name: str) -> Optional[dict[str, Any]]:
        """Get a playbook by name."""
        return self._playbooks.get(name)

    def match(self, query: str) -> Optional[dict[str, Any]]:
        """Find a playbook whose triggers match the query."""
        query_lower = query.lower()
        for name, pb in self._playbooks.items():
            triggers = pb.get("triggers", [])
            if any(t.lower() in query_lower for t in triggers):
                return {"name": name, **pb}
        return None

    def to_memory_items(self, name: str) -> list[MemoryItem]:
        """Convert a playbook into MemoryItem list for the checklist reasoner.

        Each step becomes a MemoryItem with the checks embedded as text.
        The reasoner can iterate through these sequentially.
        """
        pb = self._playbooks.get(name)
        if not pb:
            return []

        items = []

        # First item: playbook overview
        items.append(MemoryItem(
            text=f"PLAYBOOK: {pb.get('name', name)}\n{pb.get('description', '')}",
            memory_type="procedural",
            source=f"playbook:{name}",
            trust=TrustLevel.VERIFIED,
            provenance={"playbook": name, "type": "overview"},
        ))

        # Each step as a memory item
        for step in pb.get("steps", []):
            step_id = step.get("id", "")
            step_name = step.get("name", "")
            checks = step.get("checks", [])

            text_parts = [f"[{step_id}] {step_name}"]
            for check in checks:
                text_parts.append(f"  ☐ {check}")

            items.append(MemoryItem(
                text="\n".join(text_parts),
                memory_type="procedural",
                source=f"playbook:{name}:{step_id}",
                trust=TrustLevel.VERIFIED,
                provenance={
                    "playbook": name,
                    "step_id": step_id,
                    "type": "checklist_step",
                    "checks_count": len(checks),
                },
            ))

        return items

    def to_orchestrator_steps(self, name: str) -> list[str]:
        """Convert a playbook into orchestrator step strings.

        Each WSTG step becomes an orchestrator step that can be
        fed to Orchestrator.run() or used as a planner output.
        """
        pb = self._playbooks.get(name)
        if not pb:
            return []

        steps = []
        for step in pb.get("steps", []):
            step_id = step.get("id", "")
            step_name = step.get("name", "")
            checks = step.get("checks", [])
            checks_text = "; ".join(checks[:3])
            steps.append(f"{step_id}: {step_name} — {checks_text}")

        return steps
