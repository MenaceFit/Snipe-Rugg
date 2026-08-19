"""ExecutionProvider abstraction (spec section 50-53, 95-98): the seam
between "the strategy decided to act" (strategy/engine.py) and "how that
action actually gets carried out." Built last (Phase 8), after paper
trading (Phases 6-7) is validated — not alongside it. `StrategyEngine`
defaults to `PaperExecutionProvider` when no provider is given, so every
Phase 6-7 call site kept working unchanged.

Three implementations: `execution/paper.py`'s `PaperExecutionProvider`
(records a simulated fill — the only one `bot_main.py` ever constructs),
`execution/manual.py`'s `ManualExecutionProvider` (requires an external
confirmation before anything is submitted), and `execution/live.py`'s
`LiveExecutionProvider` (submits through an externally-injected signer —
disabled by default, hard limits, structurally incapable of ever holding a
private key; see its own docstring for exactly how).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from snipe_rugg.db.models import PaperPosition


class ExecutionProvider(Protocol):
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
        """Returns the opened position, or None if rejected (a risk limit,
        a declined manual confirmation, a live submission failure, ...)."""
        ...

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
        """Returns the closed position, or None if rejected."""
        ...
