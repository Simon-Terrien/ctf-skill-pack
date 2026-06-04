"""
biobrain.memory.adapters.mempalace — MemPalace backend + protocol + NullBackend
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Protocol

from ...core.enums import TrustLevel
from ...core.signals import MemoryItem

logger = logging.getLogger("biobrain.memory.adapters.mempalace")


class MemoryBackend(Protocol):
    """Protocol for memory storage backends. Implement to swap providers."""

    def search(self, query: str, n_results: int = 5,
               wing: Optional[str] = None, room: Optional[str] = None) -> list[MemoryItem]: ...

    def store(self, content: str, wing: str, room: str,
              hall: str = "hall_events", metadata: Optional[dict[str, Any]] = None) -> Optional[str]: ...

    def query_entity(self, entity: str, as_of: Optional[str] = None) -> list[dict[str, Any]]: ...

    def wake_up(self, wing: Optional[str] = None) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


class MemPalaceBackend:
    """MemPalace-backed implementation of MemoryBackend."""

    def __init__(self, palace_path: str, kg_path: Optional[str] = None):
        self.palace_path = palace_path
        self.kg_path = kg_path or os.path.join(palace_path, "knowledge_graph.sqlite3")
        self._kg = None

    @property
    def kg(self):
        if self._kg is None:
            try:
                from mempalace.knowledge_graph import KnowledgeGraph
                self._kg = KnowledgeGraph(db_path=self.kg_path)
            except Exception as e:
                logger.warning("KG init failed: %s", e)
                return None
        return self._kg

    def search(self, query, n_results=5, wing=None, room=None) -> list[MemoryItem]:
        try:
            from mempalace.searcher import search_memories
            results = search_memories(
                query=query, palace_path=self.palace_path,
                wing=wing, room=room, n_results=n_results,
            )
            if "error" in results:
                return []
            return [
                MemoryItem(
                    text=h["text"], memory_type="mempalace",
                    source=h.get("source_file", ""), wing=h.get("wing", ""),
                    room=h.get("room", ""), trust=TrustLevel.TRUSTED,
                    similarity=h.get("similarity", 0.0),
                    provenance={"backend": "mempalace", "query": query},
                )
                for h in results.get("results", [])
            ]
        except Exception as e:
            logger.warning("MemPalace search failed: %s", e)
            return []

    def store(self, content, wing, room, hall="hall_events", metadata=None) -> Optional[str]:
        try:
            from mempalace.palace import get_collection
            import hashlib
            from datetime import datetime
            col = get_collection(self.palace_path, create=True)
            drawer_id = f"bb_{hashlib.sha256(content[:200].encode()).hexdigest()[:16]}"
            meta = {"wing": wing, "room": room, "hall": hall,
                    "filed_at": datetime.now().isoformat(), **(metadata or {})}
            col.upsert(documents=[content], ids=[drawer_id], metadatas=[meta])
            return drawer_id
        except Exception as e:
            logger.error("MemPalace store failed: %s", e)
            return None

    def query_entity(self, entity, as_of=None) -> list[dict[str, Any]]:
        if self.kg is None:
            return []
        try:
            return self.kg.query_entity(entity, as_of=as_of, direction="both")
        except Exception:
            return []

    def wake_up(self, wing=None) -> dict[str, Any]:
        try:
            from mempalace.layers import MemoryStack
            stack = MemoryStack(palace_path=self.palace_path)
            return {"wake_up_text": stack.wake_up(wing=wing), **stack.status()}
        except Exception as e:
            return {"error": str(e)}

    def status(self) -> dict[str, Any]:
        try:
            from mempalace.layers import MemoryStack
            return MemoryStack(palace_path=self.palace_path).status()
        except Exception as e:
            return {"error": str(e)}


class NullBackend:
    """No-op backend for testing without MemPalace."""

    def search(self, query, n_results=5, wing=None, room=None) -> list[MemoryItem]:
        return []

    def store(self, content, wing, room, hall="hall_events", metadata=None) -> Optional[str]:
        return "null_backend"

    def query_entity(self, entity, as_of=None) -> list[dict[str, Any]]:
        return []

    def wake_up(self, wing=None) -> dict[str, Any]:
        return {"wake_up_text": "", "backend": "null"}

    def status(self) -> dict[str, Any]:
        return {"backend": "null", "total_drawers": 0}
