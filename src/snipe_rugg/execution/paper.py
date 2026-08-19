"""The only ExecutionProvider bot_main.py ever constructs. Records a
simulated fill exactly as strategy/engine.py did inline before Phase 8
introduced this abstraction — a pure extraction, not new behavior, so
Phase 6-7's tests kept passing unchanged.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.models import PaperPosition
from snipe_rugg.db.repository import WalletRepository


class PaperExecutionProvider:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            position = await repo.open_paper_position(
                mint=mint,
                creator_address=creator_address,
                followed_wallet=followed_wallet,
                entry_signature=signature,
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
        realized_pnl = position.entry_token_amount * price_sol - position.entry_sol_amount
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            closed = await repo.close_paper_position(
                position.id,
                exit_signature=signature,
                exit_slot=slot,
                exit_block_time=block_time,
                exit_price_sol=price_sol,
                exit_reason=reason,
                realized_pnl_sol=realized_pnl,
            )
            await session.commit()
        return closed
