"""Best-effort reconciliation after a reconnect: replays any signatures missed for
tracked addresses via RPC HTTP backfill, so a brief WebSocket outage doesn't
silently drop transactions (spec section 103-105)."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.ingestion.pipeline import IngestionPipeline
from snipe_rugg.providers.base import TransactionProvider

logger = logging.getLogger(__name__)


class GapRecoveryService:
    def __init__(self, rpc: TransactionProvider, pipeline: IngestionPipeline, *, max_backfill: int = 1000) -> None:
        self._rpc = rpc
        self._pipeline = pipeline
        self._max_backfill = max_backfill
        self._last_signature: dict[str, str] = {}

    def note_signature(self, address: str, signature: str) -> None:
        self._last_signature[address] = signature

    async def recover(self, addresses: Iterable[str]) -> None:
        for address in addresses:
            await self._recover_one(address)

    async def _recover_one(self, address: str) -> None:
        until = self._last_signature.get(address)
        try:
            entries = await self._rpc.get_signatures_for_address(address, until=until, limit=self._max_backfill)
        except Exception:
            logger.exception("gap_recovery_fetch_failed", extra={"fields": {"address": address}})
            return
        if not entries:
            return
        for entry in reversed(entries):  # oldest first, so downstream sees real chronological order
            event = NormalizedChainEvent(
                kind=SubscriptionKind.LOGS,
                provider="rpc_backfill",
                subscription_key=f"backfill:{address}",
                slot=entry.get("slot"),
                signature=entry.get("signature"),
                err=entry.get("err"),
                accounts=[address],
                source="backfill",
                latency=LatencyTrace(provider_received_at=utc_now(), chain_slot=entry.get("slot")),
                raw=entry,
            )
            await self._pipeline.ingest(event)
        self._last_signature[address] = entries[0]["signature"]
        logger.info("gap_recovery_replayed", extra={"fields": {"address": address, "count": len(entries)}})
