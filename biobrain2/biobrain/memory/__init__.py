"""
biobrain.memory — Four biological memory types over MemPalace
===============================================================

MemPalace is isolated behind an adapter. All returned items include
provenance metadata (source, trust, timestamp, freshness).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..core.enums import TrustLevel, SystemMode
from ..core.signals import MemoryQuery, MemoryResult, MemoryItem, ModeState

logger = logging.getLogger("biobrain.memory")


class WorkingMemory:
    """In-process state. Capacity-limited with LRU eviction."""

    def __init__(self, max_items: int = 50):
        self.max_items = max_items
        self._store: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    def put(self, key: str, value: Any, category: str = "general") -> None:
        entry = {"value": value, "category": category, "stored_at": time.time(), "access_count": 0}
        if key in self._store:
            self._order.remove(key)
        self._store[key] = entry
        self._order.append(key)
        while len(self._store) > self.max_items:
            evict_key = self._order.pop(0)
            self._store.pop(evict_key, None)

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        entry["access_count"] += 1
        return entry["value"]

    def get_by_category(self, category: str) -> list[dict[str, Any]]:
        return [{"key": k, **v} for k, v in self._store.items() if v["category"] == category]

    def get_recent(self, n: int = 10) -> list[MemoryItem]:
        keys = self._order[-n:]
        items = []
        for k in reversed(keys):
            if k in self._store:
                entry = self._store[k]
                items.append(MemoryItem(
                    text=str(entry["value"]),
                    memory_type="working",
                    trust=TrustLevel.VERIFIED,
                    provenance={"key": k, "category": entry["category"]},
                ))
        return items

    def clear(self, category: Optional[str] = None) -> int:
        if category is None:
            count = len(self._store)
            self._store.clear()
            self._order.clear()
            return count
        keys = [k for k, v in self._store.items() if v["category"] == category]
        for k in keys:
            self._store.pop(k)
            self._order.remove(k)
        return len(keys)

    @property
    def size(self) -> int:
        return len(self._store)


class MemoryManager:
    """Unified memory interface. Routes queries to appropriate subsystems.

    The ONLY module that touches MemPalace directly (via adapters/).
    """

    def __init__(
        self,
        palace_path: str,
        kg_path: Optional[str] = None,
        playbook_dir: Optional[str] = None,
        working_memory_capacity: int = 50,
    ):
        self.palace_path = palace_path
        self.kg_path = kg_path
        self.playbook_dir = playbook_dir
        self.working = WorkingMemory(max_items=working_memory_capacity)
        self._playbooks: dict[str, dict] = {}

        if playbook_dir:
            self._load_playbooks()

    def recall(self, query: MemoryQuery, mode: Optional[ModeState] = None) -> MemoryResult:
        """Execute a memory query across requested memory types."""
        mode = mode or ModeState()
        n = query.n_results
        if mode.mode == SystemMode.INCIDENT:
            n = min(n, 3)
        elif mode.mode in (SystemMode.LOW_CONFIDENCE, SystemMode.AUDIT):
            n = n + 3

        types = set(query.memory_types)
        all_types = "all" in types
        result = MemoryResult(query=query)

        if all_types or "working" in types:
            result.working = self.working.get_recent(n=n)

        if all_types or "episodic" in types:
            result.episodic = self._search_palace(query, n, "episodic")

        if all_types or "semantic" in types:
            result.semantic = self._search_palace(query, n, "semantic")
            result.kg_facts = self._query_kg(query.query)

        if all_types or "procedural" in types:
            result.procedural = self._search_palace(query, n, "procedural")

        return result

    def _search_palace(
        self, query: MemoryQuery, n: int, memory_type: str,
    ) -> list[MemoryItem]:
        """Search MemPalace and return items with provenance."""
        try:
            from mempalace.searcher import search_memories
            results = search_memories(
                query=query.query, palace_path=self.palace_path,
                wing=query.wing, room=query.room, n_results=n,
            )
            if "error" in results:
                return []
            return [
                MemoryItem(
                    text=h["text"],
                    memory_type=memory_type,
                    source=h.get("source_file", ""),
                    wing=h.get("wing", ""),
                    room=h.get("room", ""),
                    trust=TrustLevel.TRUSTED,
                    similarity=h.get("similarity", 0.0),
                    provenance={"backend": "mempalace", "query": query.query},
                )
                for h in results.get("results", [])
            ]
        except Exception as e:
            logger.warning("MemPalace search failed: %s", e)
            return []

    def _query_kg(self, query: str) -> list[dict[str, Any]]:
        """Query MemPalace Knowledge Graph for entity facts."""
        try:
            from mempalace.knowledge_graph import KnowledgeGraph
            import os
            kg_path = self.kg_path or os.path.join(self.palace_path, "knowledge_graph.sqlite3")
            kg = KnowledgeGraph(db_path=kg_path)
            entities = [w.strip(".,!?") for w in query.split() if w[0:1].isupper() and len(w) > 1]
            facts = []
            for entity in entities[:5]:
                for f in kg.query_entity(entity, direction="both"):
                    facts.append({**f, "memory_type": "kg"})
            return facts
        except Exception:
            return []

    def store_episodic(self, content: str, wing: str, room: str, **kwargs) -> Optional[str]:
        """Store an episodic memory to MemPalace."""
        try:
            from mempalace.palace import get_collection
            import hashlib
            from datetime import datetime
            col = get_collection(self.palace_path, create=True)
            drawer_id = f"ep_{hashlib.sha256(content[:200].encode()).hexdigest()[:16]}"
            meta = {
                "wing": wing, "room": room, "hall": kwargs.get("hall", "hall_events"),
                "filed_at": datetime.now().isoformat(), "memory_type": "episodic",
            }
            col.upsert(documents=[content], ids=[drawer_id], metadatas=[meta])
            return drawer_id
        except Exception as e:
            logger.error("Failed to store episodic memory: %s", e)
            return None

    def wake_up(self, wing: Optional[str] = None) -> dict[str, Any]:
        """Load MemPalace L0+L1 context."""
        try:
            from mempalace.layers import MemoryStack
            stack = MemoryStack(palace_path=self.palace_path)
            return {"wake_up_text": stack.wake_up(wing=wing), **stack.status()}
        except Exception as e:
            return {"error": str(e)}

    def _load_playbooks(self) -> None:
        from pathlib import Path
        pb_path = Path(self.playbook_dir)
        if not pb_path.exists():
            return
        for f in pb_path.glob("*.yaml"):
            try:
                with open(f) as fh:
                    pb = yaml.safe_load(fh)
                    if pb and isinstance(pb, dict):
                        self._playbooks[f.stem] = pb
            except Exception:
                pass


# Avoid import error when yaml not available at module level
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore
