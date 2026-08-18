"""Full bot entrypoint: Phase 1's real-time core wired into Phase 2's wallet
tracking, Phase 3's launch/graduation tracking, and Discord alerting. This is
what actually satisfies spec section 152's success criteria end-to-end (add a
wallet, see its activity, get an alert) — main.py stays the Phase-1-only demo
it always was.

Requires DISCORD_TOKEN and DISCORD_ALERT_CHANNEL_ID; see .env.example.

    python -m snipe_rugg.bot_main
"""
from __future__ import annotations

import asyncio
import logging

from snipe_rugg.config import get_settings
from snipe_rugg.core.dedup import InMemoryDeduplicator
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.discord_bot.alert_sink import DiscordAlertSink
from snipe_rugg.discord_bot.bot import build_bot
from snipe_rugg.discord_bot.services import (
    DevCommandService,
    GraphCommandService,
    WalletCommandService,
    WatchlistCommandService,
)
from snipe_rugg.graph.service import GraphService
from snipe_rugg.ingestion.gap_recovery import GapRecoveryService
from snipe_rugg.ingestion.pipeline import TOPIC_NORMALIZED_EVENT, IngestionPipeline
from snipe_rugg.launchpad.monitor import LaunchMonitor
from snipe_rugg.logging_setup import configure_logging
from snipe_rugg.providers.base import StreamingProvider
from snipe_rugg.providers.helius_ws import HeliusWebSocketProvider
from snipe_rugg.providers.manager import ProviderManager
from snipe_rugg.providers.rpc_http import SolanaRpcHttpClient
from snipe_rugg.providers.solana_ws import SolanaWebSocketProvider
from snipe_rugg.tracking.token_tracker import TokenTracker
from snipe_rugg.tracking.wallet_tracker import (
    WalletTracker,
    token_subscription_key,
    wallet_subscription_key,
)

logger = logging.getLogger("snipe_rugg.bot_main")

DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///snipe_rugg.db"


async def run() -> None:
    settings = get_settings()
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required to run the bot")
    if not settings.discord_alert_channel_id:
        raise RuntimeError("DISCORD_ALERT_CHANNEL_ID is required to run the bot")

    engine = create_engine(settings.database_url or DEFAULT_SQLITE_URL)
    await init_models(engine)
    session_factory = create_session_factory(engine)

    providers: list[StreamingProvider] = [SolanaWebSocketProvider(settings.solana_rpc_ws)]
    if settings.helius_api_key:
        providers.append(HeliusWebSocketProvider(settings.helius_api_key))
    manager = ProviderManager(providers)

    rpc = SolanaRpcHttpClient(settings.solana_rpc_http)
    bus = EventBus()
    pipeline = IngestionPipeline(deduplicator=InMemoryDeduplicator(), event_bus=bus)
    gap_recovery = GapRecoveryService(rpc, pipeline)

    dev_monitor = DevMonitorService(session_factory)
    bot = build_bot(
        wallet_service=WalletCommandService(session_factory=session_factory, provider=manager),
        watchlist_service=WatchlistCommandService(session_factory=session_factory),
        dev_service=DevCommandService(dev_monitor=dev_monitor),
        graph_service=GraphCommandService(graph_service=GraphService(session_factory)),
    )
    alert_sink = DiscordAlertSink(bot, channel_id=settings.discord_alert_channel_id)
    wallet_tracker = WalletTracker(
        rpc=rpc, provider=manager, session_factory=session_factory, alert_sink=alert_sink, dev_monitor=dev_monitor
    )
    token_tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=alert_sink, dev_monitor=dev_monitor
    )
    launch_monitor = LaunchMonitor(
        rpc=rpc, provider=manager, session_factory=session_factory, alert_sink=alert_sink, dev_monitor=dev_monitor
    )
    bus.subscribe(TOPIC_NORMALIZED_EVENT, wallet_tracker.handle_normalized_event)
    bus.subscribe(TOPIC_NORMALIZED_EVENT, token_tracker.handle_normalized_event)
    bus.subscribe(TOPIC_NORMALIZED_EVENT, launch_monitor.handle_normalized_event)

    async def on_reconnect(_: StreamingProvider) -> None:
        async with session_factory() as session:
            active = await WalletRepository(session).list_active_wallets()
        if active:
            await gap_recovery.recover([w.address for w in active])

    manager.set_event_handler(pipeline.handle_raw_message)
    manager.add_reconnect_listener(on_reconnect)

    await rpc.start()
    await pipeline.start()
    await manager.start()

    await launch_monitor.subscribe()

    async with session_factory() as session:
        repo = WalletRepository(session)
        active_wallets = await repo.list_active_wallets()
        ungraduated_tokens = await repo.list_ungraduated_tokens()
    for wallet in active_wallets:
        await manager.subscribe_logs(mentions=[wallet.address], key=wallet_subscription_key(wallet.address))
    for token in ungraduated_tokens:
        await manager.subscribe_logs(mentions=[token.mint], key=token_subscription_key(token.mint))
    logger.info(
        "resumed_tracking",
        extra={"fields": {"wallet_count": len(active_wallets), "token_count": len(ungraduated_tokens)}},
    )

    try:
        await bot.start(settings.discord_token)
    finally:
        await pipeline.stop()
        await manager.stop()
        await rpc.stop()
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
