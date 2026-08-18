"""Strategy engine contracts (spec section 38-39, 54-55, 88, 119-121,
126-140). MODE=PAPER is the only mode that exists through this phase (spec
section 126) — StrategyConfig has no live/execution fields at all, not even
a disabled one; that's Phase 8's job, built only after paper trading is
validated.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel


class ExitReason(StrEnum):
    CREATOR_SOLD = "CREATOR_SOLD"
    MANUAL = "MANUAL"


class StrategyConfig(BaseModel):
    enabled: bool = True
    position_size_sol: Decimal = Decimal("0.1")
    max_open_positions: int = 5
    skip_high_risk_creators: bool = True
