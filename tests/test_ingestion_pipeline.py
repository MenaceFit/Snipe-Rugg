from __future__ import annotations

from snipe_rugg.core.dedup import InMemoryDeduplicator
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.ingestion.pipeline import TOPIC_NORMALIZED_EVENT, IngestionPipeline
from tests.helpers import raw_message_from_fixture, wait_until


async def _make_pipeline():
    bus = EventBus()
    pipeline = IngestionPipeline(deduplicator=InMemoryDeduplicator(), event_bus=bus)
    received = []

    async def on_normalized(event):
        received.append(event)

    bus.subscribe(TOPIC_NORMALIZED_EVENT, on_normalized)
    await pipeline.start()
    return pipeline, received


async def test_raw_message_is_normalized_and_published():
    pipeline, received = await _make_pipeline()
    try:
        msg = raw_message_from_fixture("logs_notification.json")
        await pipeline.handle_raw_message(msg)
        await wait_until(lambda: len(received) == 1)
        assert received[0].signature == msg.payload["value"]["signature"]
        assert received[0].latency.ingested_at is not None
        assert pipeline.stats()["ingested_total"] == 1
    finally:
        await pipeline.stop()


async def test_duplicate_signature_is_dropped():
    pipeline, received = await _make_pipeline()
    try:
        msg = raw_message_from_fixture("logs_notification.json")
        await pipeline.handle_raw_message(msg)
        await pipeline.handle_raw_message(msg)  # same signature
        await wait_until(lambda: len(received) == 1)
        assert pipeline.stats()["ingested_total"] == 1
        assert pipeline.stats()["deduped_total"] == 1
    finally:
        await pipeline.stop()


async def test_events_without_signature_are_not_deduped():
    pipeline, received = await _make_pipeline()
    try:
        msg = raw_message_from_fixture("slot_notification.json")
        await pipeline.handle_raw_message(msg)
        await pipeline.handle_raw_message(msg)
        await wait_until(lambda: len(received) == 2)
        assert pipeline.stats()["deduped_total"] == 0
    finally:
        await pipeline.stop()


async def test_unnormalizable_message_is_dropped_without_raising():
    pipeline, received = await _make_pipeline()
    try:
        msg = raw_message_from_fixture("logs_notification.json").model_copy(update={"method": "unknownNotification"})
        await pipeline.handle_raw_message(msg)  # must not raise
        assert pipeline.stats()["ingested_total"] == 0
        assert received == []
    finally:
        await pipeline.stop()
