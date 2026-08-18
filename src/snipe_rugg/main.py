"""Phase 1 demo entrypoint: connects to Solana (and Helius, if configured), subscribes
to slot updates and optional wallet addresses, and logs each normalized event with its
measured latency. This is the real-time core the later phases build on — nothing here
decodes transactions or classifies BUY/SELL yet (that's Phase 2).

    python -m snipe_rugg.main --wallet <address> [--wallet <address> ...]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from snipe_rugg.config import get_settings
from snipe_rugg.core.dedup import InMemoryDeduplicator
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.core.events import NormalizedChainEvent
from snipe_rugg.ingestion.gap_recovery import GapRecoveryService
from snipe_rugg.ingestion.pipeline import TOPIC_NORMALIZED_EVENT, IngestionPipeline
from snipe_rugg.logging_setup import configure_logging
from snipe_rugg.providers.base import StreamingProvider
from snipe_rugg.providers.helius_ws import HeliusWebSocketProvider
from snipe_rugg.providers.manager import ProviderManager
from snipe_rugg.providers.rpc_http import SolanaRpcHttpClient
from snipe_rugg.providers.solana_ws import SolanaWebSocketProvider

logger = logging.getLogger("snipe_rugg.demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snipe-Rugg Phase 1 real-time core demo")
    parser.add_argument(
        "--wallet", action="append", default=[], help="Wallet address to logsSubscribe on (repeatable)"
    )
    parser.add_argument("--no-slot", action="store_true", help="Skip the slotSubscribe heartbeat")
    parser.add_argument("--stats-interval", type=float, default=15.0)
    return parser.parse_args()


async def _log_normalized_event(event: NormalizedChainEvent) -> None:
    fields = {
        "kind": event.kind.value,
        "provider": event.provider,
        "slot": event.slot,
        "signature": event.signature,
        "source": event.source,
        "detection_latency_ms": event.latency.detection_latency_ms,
    }
    logger.info("chain_event", extra={"fields": fields})


async def _report_stats(pipeline: IngestionPipeline, manager: ProviderManager, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        logger.info(
            "stats",
            extra={
                "fields": {
                    **pipeline.stats(),
                    "providers": {name: state.value for name, state in manager.provider_statuses().items()},
                    "last_seen_slot": manager.last_seen_slot,
                }
            },
        )


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()

    providers: list[StreamingProvider] = [SolanaWebSocketProvider(settings.solana_rpc_ws)]
    if settings.helius_api_key:
        providers.append(HeliusWebSocketProvider(settings.helius_api_key))
    manager = ProviderManager(providers)

    bus = EventBus()
    pipeline = IngestionPipeline(deduplicator=InMemoryDeduplicator(), event_bus=bus)
    bus.subscribe(TOPIC_NORMALIZED_EVENT, _log_normalized_event)

    rpc = SolanaRpcHttpClient(settings.solana_rpc_http)
    gap_recovery = GapRecoveryService(rpc, pipeline)

    async def on_reconnect(_: StreamingProvider) -> None:
        if args.wallet:
            logger.info("gap_recovery_start", extra={"fields": {"wallets": args.wallet}})
            await gap_recovery.recover(args.wallet)

    manager.set_event_handler(pipeline.handle_raw_message)
    manager.add_reconnect_listener(on_reconnect)

    await rpc.start()
    await pipeline.start()
    await manager.start()

    for address in args.wallet:
        await manager.subscribe_logs(mentions=[address])
    if not args.no_slot:
        await manager.subscribe_slot()

    stats_task = asyncio.create_task(_report_stats(pipeline, manager, args.stats_interval))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # e.g. Windows
    try:
        await stop_event.wait()
    finally:
        stats_task.cancel()
        await pipeline.stop()
        await manager.stop()
        await rpc.stop()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
