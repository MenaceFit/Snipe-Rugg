"""Signature-based deduplication (spec section 73): the same transaction can arrive
more than once — from a single provider's own redelivery, or because ProviderManager
deliberately races multiple providers for zero-detection-time failover (see
providers/manager.py). Transaction signature is the dedup key whenever one exists."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Protocol


class Deduplicator(Protocol):
    async def seen(self, key: str) -> bool:
        """Return True if key was already seen (and record it if not)."""
        ...


class InMemoryDeduplicator:
    """Single-process dedup with bounded memory: an LRU-ish window by count and age."""

    def __init__(self, *, maxlen: int = 200_000, ttl_seconds: float = 600.0) -> None:
        self._maxlen = maxlen
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}
        self._order: deque[tuple[str, float]] = deque()
        self._lock = asyncio.Lock()

    async def seen(self, key: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            self._evict(now)
            if key in self._seen:
                return True
            self._seen[key] = now
            self._order.append((key, now))
            self._evict(now)  # enforce maxlen now that the new entry has actually been added
            return False

    def _evict(self, now: float) -> None:
        while self._order and (
            now - self._order[0][1] > self._ttl or len(self._order) > self._maxlen
        ):
            key, _ = self._order.popleft()
            self._seen.pop(key, None)

    def __len__(self) -> int:
        return len(self._seen)


class RedisDeduplicator:
    """Multi-process dedup, backed by Redis SET NX EX (spec section 71)."""

    def __init__(self, redis_client: Any, *, namespace: str = "dedup:sig", ttl_seconds: int = 600) -> None:
        self._redis = redis_client
        self._namespace = namespace
        self._ttl = ttl_seconds

    async def seen(self, key: str) -> bool:
        was_new = await self._redis.set(f"{self._namespace}:{key}", "1", nx=True, ex=self._ttl)
        return not bool(was_new)
