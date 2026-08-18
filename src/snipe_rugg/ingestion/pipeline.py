"""Event Ingestion Layer + Event Queue (spec section 5): raw provider notifications
become NormalizedChainEvent, deduplicated by signature, queued, then fanned out on
the event bus for independent consumers (decoder, wallet state, alerting, ... —
none of which exist yet in Phase 1)."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.dedup import Deduplicator
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.core.events import NormalizedChainEvent, RawStreamMessage
from snipe_rugg.core.normalize import normalize

logger = logging.getLogger(__name__)

TOPIC_NORMALIZED_EVENT = "chain.normalized_event"


class IngestionPipeline:
    def __init__(self, *, deduplicator: Deduplicator, event_bus: EventBus, queue_maxsize: int = 10_000) -> None:
        self._dedup = deduplicator
        self._bus = event_bus
        self._queue: asyncio.Queue[NormalizedChainEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._consumer_task: asyncio.Task[None] | None = None
        self._dropped_count = 0
        self._deduped_count = 0
        self._ingested_count = 0

    async def start(self) -> None:
        if self._consumer_task is None:
            self._consumer_task = asyncio.create_task(self._consume_loop(), name="ingestion-consumer")

    async def stop(self) -> None:
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
            self._consumer_task = None

    async def handle_raw_message(self, message: RawStreamMessage) -> None:
        try:
            event = normalize(message)
        except Exception:
            logger.exception(
                "normalize_failed", extra={"fields": {"provider": message.provider, "method": message.method}}
            )
            return
        await self.ingest(event)

    async def ingest(self, event: NormalizedChainEvent) -> None:
        if event.signature is not None and await self._dedup.seen(event.signature):
            self._deduped_count += 1
            return
        event.latency.ingested_at = utc_now()
        try:
            self._queue.put_nowait(event)
            self._ingested_count += 1
        except asyncio.QueueFull:
            self._dropped_count += 1
            logger.warning("ingestion_queue_full", extra={"fields": {"dropped_total": self._dropped_count}})

    async def _consume_loop(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._bus.publish(TOPIC_NORMALIZED_EVENT, event)
            finally:
                self._queue.task_done()

    def stats(self) -> dict[str, int]:
        return {
            "queue_size": self._queue.qsize(),
            "ingested_total": self._ingested_count,
            "deduped_total": self._deduped_count,
            "dropped_total": self._dropped_count,
        }
