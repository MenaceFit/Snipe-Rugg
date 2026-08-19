from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.execution.paper import PaperExecutionProvider

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_open_position_computes_token_amount_from_price(session_factory):
    provider = PaperExecutionProvider(session_factory)
    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.5"), price_sol=Decimal("0.001"), signature="sig-buy", slot=1, block_time=T0,
    )
    assert position is not None
    assert position.entry_token_amount == Decimal("0.5") / Decimal("0.001")
    assert position.status == "open"


async def test_close_position_computes_realized_pnl(session_factory):
    provider = PaperExecutionProvider(session_factory)
    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("1.0"), price_sol=Decimal("0.001"), signature="sig-buy", slot=1, block_time=T0,
    )
    assert position is not None

    closed = await provider.close_position(
        position, price_sol=Decimal("0.002"), signature="sig-sell", slot=2, block_time=T0, reason="CREATOR_SOLD",
    )
    assert closed is not None
    assert closed.status == "closed"
    # 1000 tokens @ 0.002 = 2.0 SOL proceeds; cost was 1.0 SOL -> pnl 1.0
    assert closed.realized_pnl_sol == Decimal("1.0")
