"""ManualExecutionProvider: spec section 95-98's "manual confirmation before
any real order," implemented as a confirmation gate wrapped around another
provider (normally LiveExecutionProvider) — approving a trade doesn't change
how it gets carried out, only whether it's attempted at all.

No Discord UI is wired to the `confirm` callback anywhere in this
repository: bot_main.py never constructs this class (only
PaperExecutionProvider is ever live), so there is nothing here for a human
to actually click yet. Building that confirmation UI (an embed with
buttons, a timeout, wiring through discord_bot/) is a real next step,
deliberately left for whoever actually enables live execution — not
bundled in speculatively ahead of there being a live provider to gate.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from snipe_rugg.db.models import PaperPosition
from snipe_rugg.execution.base import ExecutionProvider

ConfirmCallback = Callable[[str, str, Decimal], Awaitable[bool]]
"""(mint, "BUY"|"SELL", sol_amount) -> approved?"""


class ManualExecutionProvider:
    def __init__(self, *, underlying: ExecutionProvider, confirm: ConfirmCallback) -> None:
        self._underlying = underlying
        self._confirm = confirm

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
        if not await self._confirm(mint, "BUY", sol_amount):
            return None
        return await self._underlying.open_position(
            mint=mint,
            creator_address=creator_address,
            followed_wallet=followed_wallet,
            sol_amount=sol_amount,
            price_sol=price_sol,
            signature=signature,
            slot=slot,
            block_time=block_time,
        )

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
        if not await self._confirm(position.mint, "SELL", position.entry_sol_amount):
            return None
        return await self._underlying.close_position(
            position, price_sol=price_sol, signature=signature, slot=slot, block_time=block_time, reason=reason
        )
