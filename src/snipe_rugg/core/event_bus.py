"""Minimal async pub/sub used to fan normalized events out to independent consumers
(alerting, persistence, strategy engine, ...) without coupling the ingestion pipeline
to any of them. Topics are plain strings; each phase introduces the ones it needs —
Phase 1 only publishes "chain.normalized_event" (see ingestion/pipeline.py)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        handlers = self._subscribers.get(topic)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = list(self._subscribers.get(topic, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(payload) for handler in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    "event_bus_handler_error",
                    extra={"fields": {"topic": topic, "handler": getattr(handler, "__qualname__", repr(handler))}},
                    exc_info=result,
                )

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, ()))
