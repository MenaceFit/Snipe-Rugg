"""Entry risk gates (spec section 119-121, 126-140): pure functions over
already-computed data, no I/O. The exit side (CreatorExitRule) lives in
strategy/engine.py instead, because exiting means matching a new SELL
against a specific *open position's* stored creator_address — there's no
useful standalone "should I exit" function independent of the position it
would close.
"""
from __future__ import annotations

from snipe_rugg.dev.models import DevRiskAssessment, Severity
from snipe_rugg.strategy.models import StrategyConfig


def should_enter(assessment: DevRiskAssessment, *, config: StrategyConfig) -> bool:
    """spec section 62 applies here too: this only ever gates on the
    *combined* assessment (already corroboration-checked by dev/patterns.py),
    never a single raw signal."""
    return not (config.skip_high_risk_creators and assessment.overall_severity is Severity.HIGH)
