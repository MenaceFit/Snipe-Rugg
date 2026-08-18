from __future__ import annotations

import asyncio

from snipe_rugg.core.dedup import InMemoryDeduplicator


async def test_first_occurrence_is_not_seen():
    dedup = InMemoryDeduplicator()
    assert await dedup.seen("sig-1") is False


async def test_repeated_signature_is_seen():
    dedup = InMemoryDeduplicator()
    assert await dedup.seen("sig-1") is False
    assert await dedup.seen("sig-1") is True


async def test_distinct_signatures_are_independent():
    dedup = InMemoryDeduplicator()
    assert await dedup.seen("sig-1") is False
    assert await dedup.seen("sig-2") is False
    assert await dedup.seen("sig-1") is True
    assert await dedup.seen("sig-2") is True


async def test_ttl_expiry_allows_reuse():
    dedup = InMemoryDeduplicator(ttl_seconds=0.05)
    assert await dedup.seen("sig-1") is False
    await asyncio.sleep(0.1)
    assert await dedup.seen("sig-1") is False


async def test_maxlen_evicts_oldest():
    dedup = InMemoryDeduplicator(maxlen=2, ttl_seconds=999)
    await dedup.seen("sig-1")
    await dedup.seen("sig-2")
    await dedup.seen("sig-3")  # evicts sig-1
    assert len(dedup) == 2
    assert await dedup.seen("sig-1") is False
