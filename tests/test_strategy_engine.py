"""StrategyEngine.handle_trade: entries follow a tracked wallet's own real
BUY (priced from that transaction's own amount_in/amount_out - see
strategy/engine.py's docstring for why this, not an instant snipe-at-launch),
exits follow CreatorExitRule (the token's own creator selling that mint)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.strategy.engine import StrategyEngine
from snipe_rugg.strategy.models import StrategyConfig

CREATOR = "DevWallet111"
FOLLOWED_WALLET = "TraderWallet111"
MINT = "MintXYZ1111111111111111111111111111111111"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def _engine(session_factory, *, config=None):
    return StrategyEngine(session_factory=session_factory, dev_monitor=DevMonitorService(session_factory), config=config)


def _buy(*, mint=MINT, wallet=FOLLOWED_WALLET, sol_in="1.0", tokens_out="1000", signature="sig-buy", slot=1) -> NormalizedTrade:
    return NormalizedTrade(
        wallet=wallet, token_in="SOL", token_out=mint, amount_in=Decimal(sol_in), amount_out=Decimal(tokens_out),
        side=EventType.BUY, program="PumpSwap", slot=slot, block_time=T0, signature=signature, confidence="high",
    )


def _sell(*, mint=MINT, wallet=CREATOR, tokens_in="1000", sol_out="2.0", signature="sig-sell", slot=2) -> NormalizedTrade:
    return NormalizedTrade(
        wallet=wallet, token_in=mint, token_out="SOL", amount_in=Decimal(tokens_in), amount_out=Decimal(sol_out),
        side=EventType.SELL, program="PumpSwap", slot=slot, block_time=T0 + timedelta(minutes=5), signature=signature,
        confidence="high",
    )


async def _seed_token(session_factory, *, mint=MINT, creator=CREATOR) -> None:
    async with session_factory() as session:
        await WalletRepository(session).record_token_launch(
            LaunchEvent(mint=mint, creator=creator, launchpad="Pump.fun", pair="SOL", slot=1, block_time=T0, signature="sig-launch")
        )
        await session.commit()


async def test_buy_on_a_token_never_launched_is_ignored(session_factory):
    engine = _engine(session_factory)
    await engine.handle_trade(_buy())
    async with session_factory() as session:
        assert await WalletRepository(session).list_open_positions() == []


async def test_buy_on_a_known_token_opens_a_position_priced_from_the_trade(session_factory):
    await _seed_token(session_factory)
    engine = _engine(session_factory, config=StrategyConfig(position_size_sol=Decimal("0.5")))

    await engine.handle_trade(_buy(sol_in="1.0", tokens_out="1000"))

    async with session_factory() as session:
        positions = await WalletRepository(session).list_open_positions()
    assert len(positions) == 1
    position = positions[0]
    assert position.mint == MINT
    assert position.creator_address == CREATOR
    assert position.followed_wallet == FOLLOWED_WALLET
    assert position.entry_price_sol == Decimal("1.0") / Decimal(1000)
    assert position.entry_sol_amount == Decimal("0.5")
    assert position.entry_token_amount == Decimal("0.5") / (Decimal("1.0") / Decimal(1000))


async def test_second_buy_on_the_same_mint_does_not_open_a_duplicate_position(session_factory):
    await _seed_token(session_factory)
    engine = _engine(session_factory)

    await engine.handle_trade(_buy(signature="sig-buy-1"))
    await engine.handle_trade(_buy(signature="sig-buy-2", wallet="AnotherTrader111"))

    async with session_factory() as session:
        assert len(await WalletRepository(session).list_open_positions()) == 1


async def test_max_open_positions_gate_blocks_a_new_entry(session_factory):
    await _seed_token(session_factory, mint="MintA")
    await _seed_token(session_factory, mint="MintB")
    engine = _engine(session_factory, config=StrategyConfig(max_open_positions=1))

    await engine.handle_trade(_buy(mint="MintA", signature="sig-a"))
    await engine.handle_trade(_buy(mint="MintB", signature="sig-b"))

    async with session_factory() as session:
        positions = await WalletRepository(session).list_open_positions()
    assert [p.mint for p in positions] == ["MintA"]


async def test_high_risk_creator_is_skipped_by_default(session_factory):
    # Seed enough launches, tightly spaced and ungraduated, for the creator to
    # reach an overall HIGH dev-risk assessment (two corroborating HIGH
    # signals - see test_dev_patterns.py for the exact thresholds).
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(10):
            await repo.record_token_launch(
                LaunchEvent(
                    mint=f"RiskyMint{i}", creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1,
                    block_time=T0 + timedelta(minutes=20 * i), signature=f"sig-risky-{i}",
                )
            )
        await session.commit()
    await _seed_token(session_factory, mint=MINT, creator=CREATOR)

    engine = _engine(session_factory)
    await engine.handle_trade(_buy())

    async with session_factory() as session:
        assert await WalletRepository(session).list_open_positions() == []


async def test_disabled_engine_ignores_all_trades(session_factory):
    await _seed_token(session_factory)
    engine = _engine(session_factory, config=StrategyConfig(enabled=False))

    await engine.handle_trade(_buy())

    async with session_factory() as session:
        assert await WalletRepository(session).list_open_positions() == []


async def test_creator_sell_closes_the_position_and_computes_pnl(session_factory):
    await _seed_token(session_factory)
    engine = _engine(session_factory, config=StrategyConfig(position_size_sol=Decimal("1.0")))
    await engine.handle_trade(_buy(sol_in="1.0", tokens_out="1000"))  # entry price 0.001 SOL/token

    await engine.handle_trade(_sell(wallet=CREATOR, tokens_in="1000", sol_out="2.0"))  # exit price 0.002 SOL/token

    async with session_factory() as session:
        repo = WalletRepository(session)
        assert await repo.list_open_positions() == []
        closed = await repo.list_closed_positions()
    assert len(closed) == 1
    position = closed[0]
    assert position.exit_reason == "CREATOR_SOLD"
    # entry_token_amount = 1.0 / 0.001 = 1000 tokens; exit price 0.002 -> proceeds 2.0 SOL; pnl = 2.0 - 1.0
    assert position.realized_pnl_sol == Decimal("1.0")


async def test_sell_by_a_non_creator_wallet_does_not_close_the_position(session_factory):
    await _seed_token(session_factory)
    engine = _engine(session_factory)
    await engine.handle_trade(_buy())

    await engine.handle_trade(_sell(wallet="SomeRandomSeller111"))

    async with session_factory() as session:
        assert len(await WalletRepository(session).list_open_positions()) == 1


async def test_sell_with_no_matching_open_position_is_a_noop(session_factory):
    engine = _engine(session_factory)
    await engine.handle_trade(_sell())  # nothing open at all
    async with session_factory() as session:
        assert await WalletRepository(session).list_closed_positions() == []
