"""BacktestService: sources events from a live database's history but must
never write into it — every run happens in its own scratch database (spec
section 41-47's isolation requirement, see backtest/service.py's docstring).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.backtest.service import BacktestService
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.strategy.models import StrategyConfig

CREATOR = "DevWallet111"
TRADER = "TraderWallet111"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def _seed_round_trip(session_factory, *, mint="MintA") -> None:
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(mint=mint, creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1, block_time=T0, signature="sig-launch")
        )
        await repo.record_trade(
            NormalizedTrade(
                wallet=TRADER, token_in="SOL", token_out=mint, amount_in=Decimal("1.0"), amount_out=Decimal(1000),
                side=EventType.BUY, program="PumpSwap", slot=2, block_time=T0 + timedelta(minutes=1), signature="sig-buy",
                confidence="high",
            )
        )
        await repo.record_trade(
            NormalizedTrade(
                wallet=CREATOR, token_in=mint, token_out="SOL", amount_in=Decimal(1000), amount_out=Decimal("2.0"),
                side=EventType.SELL, program="PumpSwap", slot=3, block_time=T0 + timedelta(minutes=5), signature="sig-sell",
                confidence="high",
            )
        )
        await session.commit()


async def test_run_with_no_history_reports_zero_trades(session_factory):
    metrics = await BacktestService(session_factory).run()
    assert metrics.total_trades == 0


async def test_run_computes_metrics_without_mutating_the_source_database(session_factory):
    await _seed_round_trip(session_factory)

    metrics = await BacktestService(session_factory).run()

    assert metrics.total_trades == 1
    assert metrics.wins == 1
    assert metrics.total_pnl_sol == Decimal("0.1")  # 0.1 SOL in @ 0.001, out @ 0.002 -> 0.2 - 0.1

    async with session_factory() as session:
        source_positions = await WalletRepository(session).list_closed_positions()
    assert source_positions == []  # the replay never touched the source database


async def test_compare_shows_different_outcomes_for_different_risk_gates(session_factory):
    # 10 tightly-spaced, ungraduated launches -> creator reaches overall HIGH
    # dev-risk (see test_dev_alerts.py for the exact thresholds this mirrors).
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(10):
            await repo.record_token_launch(
                LaunchEvent(
                    mint=f"RiskyMint{i}", creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1,
                    block_time=T0 + timedelta(minutes=20 * i), signature=f"sig-risky-{i}",
                )
            )
        await repo.record_trade(
            NormalizedTrade(
                wallet=TRADER, token_in="SOL", token_out="RiskyMint0", amount_in=Decimal("1.0"), amount_out=Decimal(1000),
                side=EventType.BUY, program="PumpSwap", slot=2, block_time=T0 + timedelta(minutes=200), signature="sig-buy",
                confidence="high",
            )
        )
        await repo.record_trade(
            NormalizedTrade(
                wallet=CREATOR, token_in="RiskyMint0", token_out="SOL", amount_in=Decimal(1000), amount_out=Decimal("2.0"),
                side=EventType.SELL, program="PumpSwap", slot=3, block_time=T0 + timedelta(minutes=205), signature="sig-sell",
                confidence="high",
            )
        )
        await session.commit()

    results = await BacktestService(session_factory).compare(
        {
            "cautious": StrategyConfig(skip_high_risk_creators=True),
            "aggressive": StrategyConfig(skip_high_risk_creators=False),
        }
    )

    # "cautious" never opens the position (risk gate blocks the entry), so the
    # later sell has nothing to close; "aggressive" opens and then closes it.
    assert results["cautious"].total_trades == 0
    assert results["aggressive"].total_trades == 1
