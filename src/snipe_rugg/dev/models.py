"""Dev/creator profiling and pattern-signal contracts (spec section 20-37,
56-59, 84).

Every number on DevProfile is a real aggregate over persisted `tokens` /
`token_trades` rows — nothing here is estimated or guessed. RiskSignal is
deliberately never a fraud verdict: `label` uses "HIGH-RISK REPEATED PATTERN"
language (spec section 62), not "rugger" or "scam", and DevRiskAssessment's
`overall_severity` requires corroboration from more than one independent
signal before it reaches HIGH — see dev/patterns.py for why.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PatternType(StrEnum):
    SERIAL_LAUNCHER = "SERIAL_LAUNCHER"
    LOW_GRADUATION_RATE = "LOW_GRADUATION_RATE"
    RAPID_RELAUNCH = "RAPID_RELAUNCH"
    DEV_SOLD_OWN_LAUNCH = "DEV_SOLD_OWN_LAUNCH"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TokenSummary(BaseModel):
    mint: str
    launchpad: str
    status: str
    first_seen_at: datetime | None
    graduated_at: datetime | None


class DevProfile(BaseModel):
    creator_address: str
    total_launches: int
    graduated_count: int
    graduation_rate: float | None
    first_launch_at: datetime | None
    last_launch_at: datetime | None
    avg_seconds_between_launches: float | None
    avg_seconds_to_graduation: float | None
    early_sell_count: int
    """Number of the creator's own launches where the creator wallet sold that
    exact mint within EARLY_SELL_WINDOW_SECONDS of first_seen_at (dev/patterns.py)
    — computed from real TokenTrade rows, only ever non-zero once the creator
    wallet's own activity has actually been observed (see
    tracking/wallet_tracker.py's auto-tracking of creators)."""
    tokens: list[TokenSummary] = Field(default_factory=list)


class RiskSignal(BaseModel):
    pattern_type: PatternType
    severity: Severity
    label: str
    description: str
    evidence: dict[str, float | int | str | None] = Field(default_factory=dict)


class DevRiskAssessment(BaseModel):
    creator_address: str
    signals: list[RiskSignal] = Field(default_factory=list)
    overall_severity: Severity | None = None
