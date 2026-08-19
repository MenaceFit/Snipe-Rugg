"""LiveExecutionProvider (spec section 50-53, 95-98): the only
ExecutionProvider that can ever submit a real transaction, and the reason
this is Phase 8, built last, only after paper trading (Phases 6-7) proved
out. Three invariants are structural here, not just configuration:

1. Disabled by default. `LiveExecutionConfig.enabled` defaults to False, and
   every method rejects immediately when it is — bot_main.py never
   constructs this class today, so no code path in this repository can
   currently place a real order.
2. Never touches a private key. This class holds no signing material and
   never will: submission goes through an externally-injected `Signer`, a
   Protocol this repository ships zero implementations of. Wiring a real
   signer (a hardware wallet daemon, a remote signing service, an operator's
   own keystore tooling) is the deploying operator's decision, made outside
   this codebase — not something handed to Claude Code or any automated
   agent to fill in. `/wallet import <private_key>` (or any command like it)
   must never be implemented; see docs/ARCHITECTURE.md's "Safety posture".
3. Hard limits checked before every submission: max trade size, a trailing
   24h realized-loss cap (queried from real closed positions — never
   estimated), and a max open-position count. Two spec-named limits —
   slippage ceiling and a minimum-liquidity floor — are accepted as config
   fields for shape-completeness but are not enforced: this codebase has no
   live price feed or DEX liquidity read to check them against, the same
   documented gap strategy/engine.py's own docstring already applies to
   entry pricing. Not silently ignored — this docstring says so outright.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.models import PaperPosition
from snipe_rugg.db.repository import WalletRepository

logger = logging.getLogger(__name__)


class Signer(Protocol):
    """An external signing backend. This repository ships no implementation
    of this Protocol — see this module's docstring for why that's permanent,
    not an oversight."""

    async def sign_and_send(self, *, mint: str, side: str, sol_amount: Decimal) -> str:
        """Submits a real transaction and returns its signature. Must raise
        on failure — LiveExecutionProvider never retries or guesses an
        outcome it didn't observe."""
        ...


class LiveExecutionConfig(BaseModel):
    enabled: bool = False
    max_trade_sol: Decimal = Decimal("0.5")
    daily_loss_limit_sol: Decimal = Decimal("1.0")
    max_open_positions: int = 3
    slippage_bps_max: int = 300
    """Accepted for spec-shape completeness; not enforced — see module docstring."""
    min_liquidity_sol: Decimal = Decimal("5.0")
    """Accepted for spec-shape completeness; not enforced — see module docstring."""


class LiveExecutionProvider:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        signer: Signer | None,
        config: LiveExecutionConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._signer = signer
        self._config = config or LiveExecutionConfig()

    async def open_position(
        self,
        *,
        mint: str,
        creator_address: str,
        followed_wallet: str,
        sol_amount: Decimal,
        price_sol: Decimal,
        signature: str,
        slot: int,
        block_time: datetime | None,
    ) -> PaperPosition | None:
        if not self._config.enabled or self._signer is None:
            return None
        if sol_amount > self._config.max_trade_sol:
            return None

        async with self._session_factory() as session:
            repo = WalletRepository(session)
            if await repo.count_open_positions() >= self._config.max_open_positions:
                return None
            if await self._trailing_24h_realized_loss(repo) >= self._config.daily_loss_limit_sol:
                return None

        try:
            # Deliberately broad: a real signing backend can raise anything,
            # and a failed live submission must be reported and refused, not
            # allowed to propagate into an unhandled crash.
            live_signature = await self._signer.sign_and_send(mint=mint, side="BUY", sol_amount=sol_amount)
        except Exception:
            logger.exception("live_buy_submission_failed", extra={"fields": {"mint": mint}})
            return None

        async with self._session_factory() as session:
            repo = WalletRepository(session)
            position = await repo.open_paper_position(
                mint=mint,
                creator_address=creator_address,
                followed_wallet=followed_wallet,
                entry_signature=live_signature,
                entry_slot=slot,
                entry_block_time=block_time,
                entry_sol_amount=sol_amount,
                entry_token_amount=sol_amount / price_sol,
                entry_price_sol=price_sol,
            )
            await session.commit()
        return position

    async def close_position(
        self,
        position: PaperPosition,
        *,
        price_sol: Decimal,
        signature: str,
        slot: int,
        block_time: datetime | None,
        reason: str,
    ) -> PaperPosition | None:
        if not self._config.enabled or self._signer is None:
            return None

        try:
            live_signature = await self._signer.sign_and_send(
                mint=position.mint, side="SELL", sol_amount=position.entry_sol_amount
            )
        except Exception:
            logger.exception("live_sell_submission_failed", extra={"fields": {"mint": position.mint}})
            return None

        realized_pnl = position.entry_token_amount * price_sol - position.entry_sol_amount
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            closed = await repo.close_paper_position(
                position.id,
                exit_signature=live_signature,
                exit_slot=slot,
                exit_block_time=block_time,
                exit_price_sol=price_sol,
                exit_reason=reason,
                realized_pnl_sol=realized_pnl,
            )
            await session.commit()
        return closed

    async def _trailing_24h_realized_loss(self, repo: WalletRepository) -> Decimal:
        cutoff = utc_now() - timedelta(hours=24)
        losses = [
            -position.realized_pnl_sol
            for position in await repo.list_closed_positions()
            if position.realized_pnl_sol is not None
            and position.realized_pnl_sol < 0
            and position.exit_block_time is not None
            and position.exit_block_time >= cutoff
        ]
        return sum(losses, Decimal(0))
