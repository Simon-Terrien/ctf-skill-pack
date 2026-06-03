"""Message bus abstraction.

KafkaBus is used for distributed runs. InMemoryBus is a single-process dev bus
that preserves the important semantics: fan-out across different groups and
load-balancing within one group.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from .config import settings


class Bus:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, msg: BaseModel, key: Optional[str] = None) -> None: ...
    def subscribe(self, topic: str, group: str) -> AsyncIterator[dict]: ...


class KafkaBus(Bus):
    """aiokafka-backed. `group` gives you a consumer group per logical consumer
    so specialists in the same category share a partitioned work queue."""

    def __init__(self, bootstrap: Optional[str] = None):
        from aiokafka import AIOKafkaProducer  # lazy import
        self._bootstrap = bootstrap or settings.kafka_bootstrap
        self._producer: Optional["AIOKafkaProducer"] = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer
        self._producer = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, msg: BaseModel, key: Optional[str] = None) -> None:
        assert self._producer, "bus not started"
        await self._producer.send_and_wait(
            topic,
            value=msg.model_dump_json().encode(),
            key=key.encode() if key else None,
        )

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[dict]:
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        try:
            async for record in consumer:
                yield json.loads(record.value)
        finally:
            await consumer.stop()


class InMemoryBus(Bus):
    """Single-process dev bus with Kafka-like group semantics.

    Different groups each receive a copy of every message. Subscribers sharing a
    group split the work in round-robin order. A small backlog is retained until
    the first subscriber for a group appears, avoiding startup races in tests.
    """

    def __init__(self, backlog_limit: int = 1000):
        self._groups: dict[str, dict[str, list[asyncio.Queue]]] = defaultdict(lambda: defaultdict(list))
        self._rr: dict[tuple[str, str], int] = defaultdict(int)
        self._backlog: dict[str, list[dict]] = defaultdict(list)
        self._seen_groups: set[tuple[str, str]] = set()
        self._backlog_limit = backlog_limit

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, topic: str, msg: BaseModel, key: Optional[str] = None) -> None:
        item = json.loads(msg.model_dump_json())
        groups = self._groups.get(topic, {})
        if not groups:
            self._backlog[topic].append(item)
            self._backlog[topic] = self._backlog[topic][-self._backlog_limit:]
            return
        for group, queues in groups.items():
            if not queues:
                continue
            idx_key = (topic, group)
            idx = self._rr[idx_key] % len(queues)
            self._rr[idx_key] += 1
            await queues[idx].put(item)

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._groups[topic][group].append(q)
        group_key = (topic, group)
        if group_key not in self._seen_groups:
            self._seen_groups.add(group_key)
            for item in self._backlog.get(topic, []):
                await q.put(item)
        try:
            while True:
                yield await q.get()
        finally:
            self._groups[topic][group].remove(q)


def make_bus() -> Bus:
    """InMemory for dev unless CTF_KAFKA is set to a real broker."""
    import os
    return KafkaBus() if os.getenv("CTF_KAFKA") else InMemoryBus()
