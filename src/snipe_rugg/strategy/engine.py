"""Paper strategy engine (spec section 38-39, 54-55, 88, 119-121, 126-140).
Decoupled from Discord and from tracking/* internals — the only input is
NormalizedTrade objects arriving over the EventBus's TOPIC_NEW_TRADE (see
core/topics.py), published by WalletTracker for every BUY/SELL it persists
for any tracked wallet (manually added, or auto-discovered as a dev by
launchpad/monitor.py — see ROADMAP.md's Phase 4 section).

Entry: a tracked wallet's own BUY becomes a paper entry signal (subject to
the risk gates in strategy/rules.py), priced at that exact transaction's own
observed rate (amount_in / amount_out) — a real, on-chain price, not a
fabricated fill. This deliberately does *not* snipe a token the instant its
launch is detected: pricing an instant entry would need either a continuous
market-data feed (not integrated — see docs/API_MATRIX.md) or hand-decoding
Pump.fun's bonding-curve account layout without a verified spec (something
launchpad/detector.py already declines to do for the same reason, spec
section 101). Following a tracked wallet's own real trade sidesteps that
without inventing a number.

Exit: CreatorExitRule (spec's own name for this) — closes a position the
moment the token's own creator address sells that same mint, priced the same
way from that SELL's own observed rate. No other exit path exists yet.

Every price used here is real. The one modeling assumption — that the paper
position's full size could be filled at the same observed price with no
slippage or latency-driven movement — is a documented simplification, stated
here rather than hidden, since no continuous price feed exists to model
against.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.constants import WRAPPED_SOL_MINT
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.strategy.models import ExitReason, StrategyConfig
from snipe_rugg.strategy.rules import should_enter

logger = logging.getLogger(__name__)

_SOL_EQUIVALENT = frozenset({"SOL", WRAPPED_SOL_MINT})


def _entry_price(trade: NormalizedTrade) -> Decimal | None:
    if trade.token_in not in _SOL_EQUIVALENT or trade.amount_in is None or not trade.amount_out:
        return None
    return trade.amount_in / trade.amount_out


def _exit_price(trade: NormalizedTrade) -> Decimal | None:
    if trade.token_out not in _SOL_EQUIVALENT or trade.amount_out is None or not trade.amount_in:
        return None
    return trade.amount_out / trade.amount_in


class StrategyEngine:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        dev_monitor: DevMonitorService,
        config: StrategyConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._dev_monitor = dev_monitor
        self._config = config or StrategyConfig()

    async def handle_trade(self, trade: NormalizedTrade) -> None:
        if not self._config.enabled:
            return
        if trade.side is EventType.BUY:
            await self._maybe_enter(trade)
        elif trade.side is EventType.SELL:
            await self._maybe_exit_on_creator_sell(trade)

    async def _maybe_enter(self, trade: NormalizedTrade) -> None:
        mint = trade.token_out
        if not mint or mint in _SOL_EQUIVALENT:
            return
        entry_price = _entry_price(trade)
        if entry_price is None or entry_price <= 0:
            return

        async with self._session_factory() as session:
            repo = WalletRepository(session)
            token = await repo.get_token(mint)
            if token is None:
                return  # only follow trades on tokens whose launch we actually saw
            if await repo.get_open_position(mint) is not None:
                return
            if await repo.count_open_positions() >= self._config.max_open_positions:
                return

            assessment = await self._dev_monitor.assess(token.creator_address)
            if not should_enter(assessment, config=self._config):
                return

            entry_token_amount = self._config.position_size_sol / entry_price
            position = await repo.open_paper_position(
                mint=mint,
                creator_address=token.creator_address,
                followed_wallet=trade.wallet,
                entry_signature=trade.signature,
                entry_slot=trade.slot,
                entry_block_time=trade.block_time,
                entry_sol_amount=self._config.position_size_sol,
                entry_token_amount=entry_token_amount,
                entry_price_sol=entry_price,
            )
            await session.commit()

        logger.info(
            "paper_position_opened",
            extra={
                "fields": {
                    "position_id": position.id,
                    "mint": mint,
                    "followed_wallet": trade.wallet,
                    "entry_price_sol": str(entry_price),
                    "entry_sol_amount": str(self._config.position_size_sol),
                }
            },
        )

    async def _maybe_exit_on_creator_sell(self, trade: NormalizedTrade) -> None:
        mint = trade.token_in
        if not mint:
            return
        exit_price = _exit_price(trade)
        if exit_price is None or exit_price < 0:
            return

        async with self._session_factory() as session:
            repo = WalletRepository(session)
            position = await repo.get_open_position(mint)
            if position is None or position.creator_address != trade.wallet:
                return  # only the token's own creator selling triggers CreatorExitRule

            realized_pnl = position.entry_token_amount * exit_price - position.entry_sol_amount
            closed = await repo.close_paper_position(
                position.id,
                exit_signature=trade.signature,
                exit_slot=trade.slot,
                exit_block_time=trade.block_time,
                exit_price_sol=exit_price,
                exit_reason=ExitReason.CREATOR_SOLD.value,
                realized_pnl_sol=realized_pnl,
            )
            await session.commit()

        if closed is None:
            return
        logger.info(
            "paper_position_closed",
            extra={
                "fields": {
                    "position_id": closed.id,
                    "mint": mint,
                    "exit_reason": ExitReason.CREATOR_SOLD.value,
                    "exit_price_sol": str(exit_price),
                    "realized_pnl_sol": str(realized_pnl),
                }
            },
        )
