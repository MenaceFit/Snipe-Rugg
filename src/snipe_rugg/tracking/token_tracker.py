"""Watches tokens launched by tracked wallets (auto-subscribed by WalletTracker
— see wallet_tracker.py) for graduation from Pump.fun to PumpSwap (spec section
60). Graduation is detectable within a single transaction — a Pump.fun migrate
instruction is followed by a PumpSwap create_pool in the same instruction list
— so this only needs to notice both program IDs in one decoded transaction,
not correlate state across several.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.constants import PUMP_FUN_PROGRAM, PUMPSWAP_PROGRAM
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.launchpad.models import LaunchpadStatus
from snipe_rugg.providers.base import TransactionProvider
from snipe_rugg.tracking.wallet_tracker import AlertSink, token_mint_from_key

logger = logging.getLogger(__name__)


class TokenTracker:
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
        mint = token_mint_from_key(event.subscription_key)
        if mint is None:
            return

        graduated_token = None
        wallet = None
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            token = await repo.get_token(mint)
            if token is None or token.status == LaunchpadStatus.GRADUATED.value:
                return

            raw_tx = await self._rpc.get_transaction(event.signature)
            event.latency.decoded_at = utc_now()
            if raw_tx is None:
                logger.warning("transaction_not_found", extra={"fields": {"signature": event.signature}})
                return

            decoded = self._decoder.decode(raw_tx)
            event.latency.classified_at = utc_now()

            if not (PUMP_FUN_PROGRAM in decoded.programs and PUMPSWAP_PROGRAM in decoded.programs):
                return

            graduated_token = await repo.mark_graduated(
                mint, slot=decoded.slot, block_time=decoded.block_time, signature=decoded.signature
            )
            if graduated_token is not None:
                wallet = await repo.get_wallet(graduated_token.creator_address)
            event.latency.persisted_at = utc_now()
            await session.commit()

        if graduated_token is None or (wallet is not None and not wallet.alert_launches):
            return

        message_id = await self._alert_sink.send_graduation(token=graduated_token, wallet=wallet, latency=event.latency)
        event.latency.alerted_at = utc_now()
        async with self._session_factory() as session:
            await WalletRepository(session).record_alert(
                wallet_address=graduated_token.creator_address,
                signature=event.signature or "",
                event_type="GRADUATED",
                discord_message_id=message_id,
                total_latency_ms=event.latency.total_latency_ms,
            )
            await session.commit()
        logger.info(
            "graduation_detected",
            extra={
                "fields": {
                    "mint": mint,
                    "signature": event.signature,
                    "total_latency_ms": event.latency.total_latency_ms,
                }
            },
        )
