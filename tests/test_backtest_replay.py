"""ReplayEngine: chronological ordering and the no-look-ahead guarantee (spec
section 41-47's "a backtested decision only ever sees data that would have
been available at that moment") — see backtest/replay.py's docstring for the
argument that this is enforced by construction, not just by discipline. The
tests here try to actually break that, not just restate it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.backtest.replay import ReplayEngine, ReplayEvent
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


def _launch(mint, *, at, slot, creator=CREATOR) -> ReplayEvent:
    return ReplayEvent(
        at=at, slot=slot,
        launch=LaunchEvent(mint=mint, creator=creator, launchpad="Pump.fun", pair="SOL", slot=slot, block_time=at, signature=f"sig-{mint}"),
    )


def _buy_event(mint, *, at, slot) -> ReplayEvent:
    return ReplayEvent(
        at=at, slot=slot,
        trade=NormalizedTrade(
            wallet=TRADER, token_in="SOL", token_out=mint, amount_in=Decimal("1.0"), amount_out=Decimal(1000),
            side=EventType.BUY, program="PumpSwap", slot=slot, block_time=at, signature="sig-buy", confidence="high",
        ),
    )


async def test_replay_result_reports_event_and_position_counts(session_factory):
    events = [_launch("MintA", at=T0, slot=1), _buy_event("MintA", at=T0 + timedelta(minutes=1), slot=2)]
    replay = ReplayEngine(session_factory=session_factory, config=StrategyConfig())

    result = await replay.run(events)

    assert result.events_processed == 2
    assert result.open_positions == 1
    assert result.closed_positions == 0


async def test_no_lookahead_early_buy_is_not_blocked_by_the_creators_later_launches(session_factory):
    """The direct proof: 9 more launches from the same creator, all AFTER the
    buy chronologically, would (if visible early) push the creator's combined
    dev-risk assessment to HIGH and block the buy via should_enter's
    skip_high_risk_creators gate (see test_dev_alerts.py for the exact
    thresholds this reproduces). They're deliberately placed *earlier* in the
    input list than the buy, in list order, to also prove the engine sorts by
    time rather than trusting caller order.
    """
    buy = _buy_event("MintEarly", at=T0 + timedelta(minutes=1), slot=2)
    later_launches = [
        _launch(f"MintLater{i}", at=T0 + timedelta(minutes=10 + 20 * i), slot=10 + i)
        for i in range(9)
    ]
    events = [*later_launches, _launch("MintEarly", at=T0, slot=1), buy]

    replay = ReplayEngine(session_factory=session_factory, config=StrategyConfig())
    await replay.run(events)

    async with session_factory() as session:
        positions = await WalletRepository(session).list_open_positions()
    assert [p.mint for p in positions] == ["MintEarly"]


async def test_lookahead_would_have_blocked_the_same_buy_if_all_launches_preceded_it(session_factory):
    """Sanity check for the test above: confirms the 9 extra launches really
    do reach HIGH risk when they genuinely happen first - so the previous
    test is proving something real, not a threshold that never fires."""
    buy = _buy_event("MintEarly", at=T0 + timedelta(minutes=45), slot=20)
    earlier_launches = [
        _launch(f"MintEarlier{i}", at=T0 + timedelta(minutes=5 * i), slot=1 + i)
        for i in range(9)
    ]  # all 9 land at T0+0..40min, strictly before both MintEarly and the buy
    events = [*earlier_launches, _launch("MintEarly", at=T0 + timedelta(minutes=44), slot=19), buy]

    replay = ReplayEngine(session_factory=session_factory, config=StrategyConfig())
    await replay.run(events)

    async with session_factory() as session:
        positions = await WalletRepository(session).list_open_positions()
    assert positions == []


async def test_creator_sell_replayed_after_the_buy_closes_the_position(session_factory):
    sell = ReplayEvent(
        at=T0 + timedelta(minutes=5), slot=3,
        trade=NormalizedTrade(
            wallet=CREATOR, token_in="MintA", token_out="SOL", amount_in=Decimal(1000), amount_out=Decimal("2.0"),
            side=EventType.SELL, program="PumpSwap", slot=3, block_time=T0 + timedelta(minutes=5), signature="sig-sell",
            confidence="high",
        ),
    )
    events = [sell, _buy_event("MintA", at=T0 + timedelta(minutes=1), slot=2), _launch("MintA", at=T0, slot=1)]

    replay = ReplayEngine(session_factory=session_factory, config=StrategyConfig())
    result = await replay.run(events)

    assert result.open_positions == 0
    assert result.closed_positions == 1
