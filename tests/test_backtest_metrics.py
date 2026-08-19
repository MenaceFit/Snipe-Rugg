from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from snipe_rugg.backtest.metrics import compute_metrics
from snipe_rugg.db.models import PaperPosition

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _closed(*, pnl, exit_slot, entry_at=None, exit_at=None) -> PaperPosition:
    return PaperPosition(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        status="closed", entry_signature="sig-buy", entry_slot=1, entry_block_time=entry_at,
        entry_sol_amount=Decimal("0.1"), entry_token_amount=Decimal(100), entry_price_sol=Decimal("0.001"),
        exit_signature="sig-sell", exit_slot=exit_slot, exit_block_time=exit_at,
        exit_price_sol=Decimal("0.002"), exit_reason="CREATOR_SOLD", realized_pnl_sol=pnl,
    )


def test_empty_metrics_has_no_win_rate():
    metrics = compute_metrics([])
    assert metrics.total_trades == 0
    assert metrics.win_rate is None
    assert metrics.total_pnl_sol == Decimal(0)
    assert metrics.avg_hold_seconds is None


def test_win_loss_counts_and_total_pnl():
    positions = [
        _closed(pnl=Decimal("0.5"), exit_slot=1),
        _closed(pnl=Decimal("-0.2"), exit_slot=2),
        _closed(pnl=Decimal("0.3"), exit_slot=3),
    ]
    metrics = compute_metrics(positions)
    assert metrics.total_trades == 3
    assert metrics.wins == 2
    assert metrics.losses == 1
    assert metrics.win_rate == 2 / 3
    assert metrics.total_pnl_sol == Decimal("0.6")


def test_zero_pnl_counts_as_a_loss_not_a_win():
    metrics = compute_metrics([_closed(pnl=Decimal(0), exit_slot=1)])
    assert metrics.wins == 0
    assert metrics.losses == 1


def test_max_drawdown_from_a_running_equity_curve():
    # +1 -> peak 1; -0.5 -> equity 0.5, drawdown 0.5; +0.1 -> equity 0.6, still down 0.4 from peak
    positions = [
        _closed(pnl=Decimal("1.0"), exit_slot=1),
        _closed(pnl=Decimal("-0.5"), exit_slot=2),
        _closed(pnl=Decimal("0.1"), exit_slot=3),
    ]
    metrics = compute_metrics(positions)
    assert metrics.max_drawdown_sol == Decimal("0.5")


def test_drawdown_uses_exit_slot_order_not_list_order():
    positions = [
        _closed(pnl=Decimal("-0.5"), exit_slot=2),
        _closed(pnl=Decimal("1.0"), exit_slot=1),
    ]
    metrics = compute_metrics(positions)
    # chronological (by exit_slot): +1.0 first (peak 1.0), then -0.5 (equity 0.5, drawdown 0.5)
    assert metrics.max_drawdown_sol == Decimal("0.5")


def test_avg_hold_seconds_only_counts_positions_with_both_timestamps():
    positions = [
        _closed(pnl=Decimal("0.1"), exit_slot=1, entry_at=T0, exit_at=T0 + timedelta(seconds=60)),
        _closed(pnl=Decimal("0.1"), exit_slot=2, entry_at=T0, exit_at=T0 + timedelta(seconds=120)),
        _closed(pnl=Decimal("0.1"), exit_slot=3, entry_at=None, exit_at=None),
    ]
    metrics = compute_metrics(positions)
    assert metrics.avg_hold_seconds == 90.0
