"""Wallet activity tracking: consumes Phase 1's normalized chain-event stream,
fetches and decodes the full transaction for each tracked wallet's logs
notification, classifies it, persists it, and routes qualifying events to an
AlertSink.

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
from snipe_rugg.db.models import TrackedWallet, WalletStatus
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.classifier import classify
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.providers.base import TransactionProvider

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
    async def send(
        self, *, wallet: TrackedWallet, event: BusinessEvent, latency: LatencyTrace
    ) -> str | None:
        """Deliver one alert; return a provider-side message id if available."""
        ...


class WalletTracker:
    def __init__(
        self,
        *,
        rpc: TransactionProvider,
        session_factory: async_sessionmaker[AsyncSession],
        alert_sink: AlertSink,
    ) -> None:
        self._rpc = rpc
        self._session_factory = session_factory
        self._alert_sink = alert_sink
        self._decoder = TransactionDecoder()

    async def handle_normalized_event(self, event: NormalizedChainEvent) -> None:
        if event.kind is not SubscriptionKind.LOGS or not event.signature or event.err is not None:
            return
        address = wallet_address_from_key(event.subscription_key)
        if address is None:
            return

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
            event.latency.persisted_at = utc_now()
            await session.commit()

        for business_event in business_events:
            if self._should_alert(wallet, business_event):
                await self._dispatch_alert(wallet, business_event, event)

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
