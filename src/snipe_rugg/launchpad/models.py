"""Launch detection output contract (spec section 16-18)."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class LaunchpadStatus(StrEnum):
    BONDING_CURVE = "bonding_curve"
    GRADUATED = "graduated"


class LaunchEvent(BaseModel):
    mint: str
    creator: str
    launchpad: str
    pair: str | None
    slot: int
    block_time: datetime | None
    signature: str
