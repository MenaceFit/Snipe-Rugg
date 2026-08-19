"""Backtest performance metrics (spec section 41-47, 82-83, 91-94, 134-136),
computed only from real closed PaperPosition rows produced by a replay — no
synthetic benchmark, no assumed baseline.

Fee and slippage notes (spec explicitly asks for fee/slippage simulation):
entry/exit prices here are always a real observed trade's own rate (see
strategy/engine.py), so whatever slippage that real trader actually
experienced is already baked into the price used — there's nothing extra to
simulate on top of a number that's already real. Transaction fees are a
different, genuinely unimplemented gap: DecodedTransaction already carries a
real `fee_lamports` per transaction, but it isn't threaded through
NormalizedTrade/TokenTrade yet, so it isn't available here to subtract from
PnL. That's a documented gap, not a silent zero-fee assumption presented as
accurate — see docs/ROADMAP.md's Phase 7 section.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from snipe_rugg.db.models import PaperPosition


@dataclass
class BacktestMetrics:
    total_trades: int
    wins: int
    losses: int
    win_rate: float | None
    total_pnl_sol: Decimal
    max_drawdown_sol: Decimal
    avg_hold_seconds: float | None


def compute_metrics(closed_positions: list[PaperPosition]) -> BacktestMetrics:
    total = len(closed_positions)
    if total == 0:
        return BacktestMetrics(
            total_trades=0, wins=0, losses=0, win_rate=None,
            total_pnl_sol=Decimal(0), max_drawdown_sol=Decimal(0), avg_hold_seconds=None,
        )

    pnls = [p.realized_pnl_sol or Decimal(0) for p in closed_positions]
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = total - wins

    running = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    hold_seconds: list[float] = []
    # Chronological order (by exit slot) matters here: a running-equity
    # drawdown curve is only meaningful walked forward through time.
    for position in sorted(closed_positions, key=lambda p: p.exit_slot or 0):
        running += position.realized_pnl_sol or Decimal(0)
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        if position.entry_block_time is not None and position.exit_block_time is not None:
            hold_seconds.append((position.exit_block_time - position.entry_block_time).total_seconds())

    return BacktestMetrics(
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=wins / total,
        total_pnl_sol=sum(pnls, Decimal(0)),
        max_drawdown_sol=max_drawdown,
        avg_hold_seconds=(sum(hold_seconds) / len(hold_seconds)) if hold_seconds else None,
    )
