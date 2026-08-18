"""Fans subscriptions out to every configured streaming provider and races them:
whichever provider's connection is healthiest delivers first, and the ingestion
pipeline's signature-based dedup collapses duplicate deliveries from the rest.
This buys zero-detection-time failover (spec section 74/76) at the cost of some
duplicate bandwidth when more than one provider is healthy at once — a deliberate
tradeoff given the priority on latency over efficiency (spec section 3/4)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from snipe_rugg.providers.base import (
    ConnectionState,
    EventHandler,
    ReconnectListener,
    StreamingProvider,
)

logger = logging.getLogger(__name__)


class ProviderManager(StreamingProvider):
    def __init__(self, providers: list[StreamingProvider], *, name: str = "provider_manager") -> None:
        if not providers:
            raise ValueError("ProviderManager requires at least one provider")
        self.name = name
        self._providers = providers

    @property
    def state(self) -> ConnectionState:
        states = [p.state for p in self._providers]
        if all(s == ConnectionState.CONNECTED for s in states):
            return ConnectionState.CONNECTED
        if any(s == ConnectionState.CONNECTED for s in states):
            return ConnectionState.DEGRADED
        return ConnectionState.DISCONNECTED

    @property
    def last_seen_slot(self) -> int | None:
        slots = [p.last_seen_slot for p in self._providers if p.last_seen_slot is not None]
        return max(slots) if slots else None

    def provider_statuses(self) -> dict[str, ConnectionState]:
        return {p.name: p.state for p in self._providers}

    def set_event_handler(self, handler: EventHandler) -> None:
        for p in self._providers:
            p.set_event_handler(handler)

    def add_reconnect_listener(self, listener: ReconnectListener) -> None:
        for p in self._providers:
            p.add_reconnect_listener(listener)

    async def start(self) -> None:
        results = await asyncio.gather(*(p.start() for p in self._providers), return_exceptions=True)
        for provider, result in zip(self._providers, results):
            if isinstance(result, Exception):
                logger.error(
                    "provider_start_failed", extra={"fields": {"provider": provider.name, "error": str(result)}}
                )

    async def stop(self) -> None:
        await asyncio.gather(*(p.stop() for p in self._providers), return_exceptions=True)

    async def subscribe_logs(
        self, *, mentions: list[str] | None = None, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        key = key or f"logs:{mentions[0] if mentions else 'all'}"
        await self._fan_out("subscribe_logs", mentions=mentions, commitment=commitment, key=key)
        return key

    async def subscribe_account(
        self, pubkey: str, *, commitment: str = "finalized", encoding: str = "jsonParsed", key: str | None = None
    ) -> str:
        key = key or f"account:{pubkey}"
        await self._fan_out("subscribe_account", pubkey, commitment=commitment, encoding=encoding, key=key)
        return key

    async def subscribe_program(
        self,
        program_id: str,
        *,
        commitment: str = "finalized",
        encoding: str = "base64",
        filters: list[dict[str, Any]] | None = None,
        key: str | None = None,
    ) -> str:
        key = key or f"program:{program_id}"
        await self._fan_out(
            "subscribe_program", program_id, commitment=commitment, encoding=encoding, filters=filters, key=key
        )
        return key

    async def subscribe_slot(self, *, key: str | None = None) -> str:
        key = key or "slot:all"
        await self._fan_out("subscribe_slot", key=key)
        return key

    async def subscribe_signature(
        self, signature: str, *, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        key = key or f"signature:{signature}"
        await self._fan_out("subscribe_signature", signature, commitment=commitment, key=key)
        return key

    async def unsubscribe(self, key: str) -> None:
        results = await asyncio.gather(
            *(p.unsubscribe(key) for p in self._providers), return_exceptions=True
        )
        for provider, result in zip(self._providers, results):
            if isinstance(result, Exception):
                logger.warning("unsubscribe_failed", extra={"fields": {"provider": provider.name, "key": key}})

    async def _fan_out(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        results = await asyncio.gather(
            *(getattr(p, method_name)(*args, **kwargs) for p in self._providers), return_exceptions=True
        )
        healthy = 0
        for provider, result in zip(self._providers, results):
            if isinstance(result, Exception):
                logger.warning(
                    "subscribe_failed",
                    extra={"fields": {"provider": provider.name, "method": method_name, "error": str(result)}},
                )
            else:
                healthy += 1
        if healthy == 0:
            raise RuntimeError(f"{method_name} failed on every provider")
