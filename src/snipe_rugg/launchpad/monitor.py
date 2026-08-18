"""Network-wide Pump.fun launch monitoring (spec section 16-18, priority 1),
independent of whether the creator wallet was ever explicitly tracked.

This closes a real gap Phase 3 left: WalletTracker's launch detection only
fires for a launch made *by an already-tracked wallet* (useful for "did one of
my tracked wallets deploy a token," see tracking/wallet_tracker.py), which
can't discover a brand-new dev nobody added yet — and discovering unknown devs
is the actual point of a "dev launch monitor." logsSubscribe's `mentions`
filter takes exactly one address (see ARCHITECTURE.md's "Known scaling
limit"), but that one address can just as well be a *program* ID as a wallet:
Pump.fun's `create` instruction is invoked by every launch on the platform, so
one subscription to PUMP_FUN_PROGRAM's own logs sees literally every Pump.fun
launch network-wide — not one subscription per wallet.

If the creator happens to already be tracked (WalletTracker got there first,
in the same or an earlier transaction), `record_token_launch`'s existing
idempotency means this is a harmless no-op re-confirmation, not a duplicate.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.alerts.sink import AlertSink
from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.constants import PUMP_FUN_PROGRAM
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.dev.alerts import refresh_and_maybe_alert
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.detector import detect_launch
from snipe_rugg.providers.base import StreamingProvider, TransactionProvider
from snipe_rugg.tracking.wallet_tracker import token_subscription_key, wallet_subscription_key

logger = logging.getLogger(__name__)

LAUNCH_FIREHOSE_KEY = "launch-firehose:pumpfun"


class LaunchMonitor:
    def __init__(
        self,
        *,
        rpc: TransactionProvider,
        provider: StreamingProvider,
        session_factory: async_sessionmaker[AsyncSession],
        alert_sink: AlertSink,
        dev_monitor: DevMonitorService,
    ) -> None:
        self._rpc = rpc
        self._provider = provider
        self._session_factory = session_factory
        self._alert_sink = alert_sink
        self._dev_monitor = dev_monitor
        self._decoder = TransactionDecoder()

    async def subscribe(self) -> None:
        await self._provider.subscribe_logs(mentions=[PUMP_FUN_PROGRAM], key=LAUNCH_FIREHOSE_KEY)

    async def handle_normalized_event(self, event: NormalizedChainEvent) -> None:
        if (
            event.kind is not SubscriptionKind.LOGS
            or event.subscription_key != LAUNCH_FIREHOSE_KEY
            or not event.signature
            or event.err is not None
        ):
            return

        is_new_token = False
        wallet_created = False
        launch = None
        async with self._session_factory() as session:
            repo = WalletRepository(session)

            raw_tx = await self._rpc.get_transaction(event.signature)
            event.latency.decoded_at = utc_now()
            if raw_tx is None:
                logger.warning("transaction_not_found", extra={"fields": {"signature": event.signature}})
                return

            decoded = self._decoder.decode(raw_tx)
            event.latency.classified_at = utc_now()

            launch = detect_launch(decoded)
            if launch is None:
                return

            existing = await repo.get_token(launch.mint)
            is_new_token = existing is None
            await repo.record_token_launch(launch)
            wallet, wallet_created = await repo.get_or_create_dev_wallet(launch.creator)
            event.latency.persisted_at = utc_now()
            await session.commit()

        if not is_new_token:
            # WalletTracker (or an earlier delivery of this same firehose
            # notification via provider redundancy - see ProviderManager)
            # already recorded this exact launch; record_token_launch's
            # idempotency means `wallet`/`launch` above are still correct, just
            # not worth re-subscribing or re-alerting for.
            return

        await self._provider.subscribe_logs(mentions=[launch.mint], key=token_subscription_key(launch.mint))
        if wallet_created:
            await self._provider.subscribe_logs(
                mentions=[launch.creator], key=wallet_subscription_key(launch.creator)
            )

        if wallet.alert_launches:
            message_id = await self._alert_sink.send_launch(wallet=wallet, launch=launch, latency=event.latency)
            event.latency.alerted_at = utc_now()
            async with self._session_factory() as session:
                await WalletRepository(session).record_alert(
                    wallet_address=launch.creator,
                    signature=event.signature or "",
                    event_type="TOKEN_CREATE",
                    discord_message_id=message_id,
                    total_latency_ms=event.latency.total_latency_ms,
                )
                await session.commit()

        await refresh_and_maybe_alert(
            dev_monitor=self._dev_monitor,
            alert_sink=self._alert_sink,
            session_factory=self._session_factory,
            creator_address=launch.creator,
            latency=event.latency,
        )
        logger.info(
            "launch_detected_firehose",
            extra={
                "fields": {
                    "creator": launch.creator,
                    "mint": launch.mint,
                    "signature": event.signature,
                    "total_latency_ms": event.latency.total_latency_ms,
                }
            },
        )
