"""Wallet activity tracking: consumes Phase 1's normalized chain-event stream,
fetches and decodes the full transaction for each tracked wallet's logs
notification, classifies it, persists it, and routes qualifying events to an
AlertSink. Also runs launch detection (spec section 16-18): when a tracked
wallet's transaction creates a new token, this records it and starts watching
that mint for graduation (see tracking/token_tracker.py) regardless of whether
the launch alert itself is sent — watching is a data-completeness concern,
alerting is a notification preference, and they're gated independently, the
same way BUY/SELL/TRANSFER are always persisted but only conditionally alerted.

Deliberately decoupled from discord.py — this module never imports `discord`.
AlertSink is a Protocol; discord_bot/bot.py provides the concrete
implementation that builds an embed (alerts/embeds.py) and posts it to a
channel. That split is what makes this whole pipeline testable without a live
Discord connection: tests here use a fake sink that just records calls.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.models import Token, TrackedWallet, WalletStatus
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.classifier import classify
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.launchpad.detector import detect_launch
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.providers.base import StreamingProvider, TransactionProvider

logger = logging.getLogger(__name__)

_WALLET_SUBSCRIPTION_PREFIX = "wallet:"

BusinessEvent = NormalizedTrade | NormalizedActivity


def wallet_subscription_key(address: str) -> str:
    return f"{_WALLET_SUBSCRIPTION_PREFIX}{address}"


def wallet_address_from_key(key: str) -> str | None:
    if not key.startswith(_WALLET_SUBSCRIPTION_PREFIX):
        return None
    return key[len(_WALLET_SUBSCRIPTION_PREFIX) :]


def event_type_of(event: BusinessEvent) -> EventType:
    return event.side if isinstance(event, NormalizedTrade) else event.event_type


def sol_denominated_amount(trade: NormalizedTrade) -> Decimal | None:
    if trade.token_in == "SOL":
        return trade.amount_in
    if trade.token_out == "SOL":
        return trade.amount_out
    return None


class AlertSink(Protocol):
    async def send(self, *, wallet: TrackedWallet, event: BusinessEvent, latency: LatencyTrace) -> str | None:
        """Deliver one trade/activity alert; return a message id if available."""
        ...

    async def send_launch(self, *, wallet: TrackedWallet, launch: LaunchEvent, latency: LatencyTrace) -> str | None:
        """Deliver a "new token launched by a tracked wallet" alert."""
        ...

    async def send_graduation(self, *, token: Token, wallet: TrackedWallet | None, latency: LatencyTrace) -> str | None:
        """Deliver a "token graduated to PumpSwap" alert (tracking/token_tracker.py)."""
        ...


class WalletTracker:
    def __init__(
        self,
        *,
        rpc: TransactionProvider,
        provider: StreamingProvider,
        session_factory: async_sessionmaker[AsyncSession],
        alert_sink: AlertSink,
    ) -> None:
        self._rpc = rpc
        self._provider = provider
        self._session_factory = session_factory
        self._alert_sink = alert_sink
        self._decoder = TransactionDecoder()

    async def handle_normalized_event(self, event: NormalizedChainEvent) -> None:
        if event.kind is not SubscriptionKind.LOGS or not event.signature or event.err is not None:
            return
        address = wallet_address_from_key(event.subscription_key)
        if address is None:
            return

        launch: LaunchEvent | None = None
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            wallet = await repo.get_wallet(address)
            if wallet is None or wallet.status != WalletStatus.ACTIVE.value:
                return

            raw_tx = await self._rpc.get_transaction(event.signature)
            event.latency.decoded_at = utc_now()
            if raw_tx is None:
                logger.warning("transaction_not_found", extra={"fields": {"signature": event.signature}})
                return

            decoded = self._decoder.decode(raw_tx)
            business_events = classify(decoded, address)
            event.latency.classified_at = utc_now()

            for business_event in business_events:
                if isinstance(business_event, NormalizedTrade):
                    await repo.record_trade(business_event)
                else:
                    await repo.record_activity(business_event)

            detected = detect_launch(decoded)
            if detected is not None and detected.creator == address:
                launch = detected
                await repo.record_token_launch(launch)

            event.latency.persisted_at = utc_now()
            await session.commit()

        for business_event in business_events:
            if self._should_alert(wallet, business_event):
                await self._dispatch_alert(wallet, business_event, event)

        if launch is not None:
            await self._provider.subscribe_logs(mentions=[launch.mint], key=token_subscription_key(launch.mint))
            if wallet.alert_launches:
                await self._dispatch_launch_alert(wallet, launch, event)

    async def _dispatch_alert(
        self, wallet: TrackedWallet, business_event: BusinessEvent, chain_event: NormalizedChainEvent
    ) -> None:
        message_id = await self._alert_sink.send(wallet=wallet, event=business_event, latency=chain_event.latency)
        chain_event.latency.alerted_at = utc_now()
        async with self._session_factory() as session:
            await WalletRepository(session).record_alert(
                wallet_address=wallet.address,
                signature=chain_event.signature or "",
                event_type=event_type_of(business_event).value,
                discord_message_id=message_id,
                total_latency_ms=chain_event.latency.total_latency_ms,
            )
            await session.commit()
        logger.info(
            "alert_sent",
            extra={
                "fields": {
                    "wallet": wallet.address,
                    "event_type": event_type_of(business_event).value,
                    "signature": chain_event.signature,
                    "detection_latency_ms": chain_event.latency.detection_latency_ms,
                    "decode_latency_ms": chain_event.latency.decode_latency_ms,
                    "classification_latency_ms": chain_event.latency.classification_latency_ms,
                    "persistence_latency_ms": chain_event.latency.persistence_latency_ms,
                    "notification_latency_ms": chain_event.latency.notification_latency_ms,
                    "total_latency_ms": chain_event.latency.total_latency_ms,
                }
            },
        )

    async def _dispatch_launch_alert(
        self, wallet: TrackedWallet, launch: LaunchEvent, chain_event: NormalizedChainEvent
    ) -> None:
        message_id = await self._alert_sink.send_launch(wallet=wallet, launch=launch, latency=chain_event.latency)
        chain_event.latency.alerted_at = utc_now()
        async with self._session_factory() as session:
            await WalletRepository(session).record_alert(
                wallet_address=wallet.address,
                signature=chain_event.signature or "",
                event_type=EventType.TOKEN_CREATE.value,
                discord_message_id=message_id,
                total_latency_ms=chain_event.latency.total_latency_ms,
            )
            await session.commit()
        logger.info(
            "launch_detected",
            extra={
                "fields": {
                    "wallet": wallet.address,
                    "mint": launch.mint,
                    "launchpad": launch.launchpad,
                    "signature": chain_event.signature,
                    "total_latency_ms": chain_event.latency.total_latency_ms,
                }
            },
        )

    @staticmethod
    def _should_alert(wallet: TrackedWallet, event: BusinessEvent) -> bool:
        event_type = event_type_of(event)
        if event_type is EventType.BUY and not wallet.alert_buys:
            return False
        if event_type is EventType.SELL and not wallet.alert_sells:
            return False
        if event_type is EventType.TRANSFER and not wallet.alert_transfers:
            return False
        if event_type is EventType.TOKEN_CREATE and not wallet.alert_launches:
            return False
        if wallet.min_alert_sol is not None and isinstance(event, NormalizedTrade):
            sol_amount = sol_denominated_amount(event)
            if sol_amount is not None and abs(sol_amount) < wallet.min_alert_sol:
                return False
        return True


_TOKEN_SUBSCRIPTION_PREFIX = "token:"


def token_subscription_key(mint: str) -> str:
    return f"{_TOKEN_SUBSCRIPTION_PREFIX}{mint}"


def token_mint_from_key(key: str) -> str | None:
    if not key.startswith(_TOKEN_SUBSCRIPTION_PREFIX):
        return None
    return key[len(_TOKEN_SUBSCRIPTION_PREFIX) :]
