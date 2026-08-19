"""ManualExecutionProvider: the confirmation gate itself, independent of
what it wraps (see execution/manual.py's docstring for why no Discord UI is
wired to the confirm callback in this repository)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.execution.manual import ManualExecutionProvider
from snipe_rugg.execution.paper import PaperExecutionProvider

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_declined_confirmation_never_reaches_the_underlying_provider(session_factory):
    calls = []

    async def confirm(mint, action, sol_amount):
        calls.append((mint, action, sol_amount))
        return False

    provider = ManualExecutionProvider(underlying=PaperExecutionProvider(session_factory), confirm=confirm)
    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-buy", slot=1, block_time=T0,
    )

    assert position is None
    assert calls == [("MintA", "BUY", Decimal("0.1"))]


async def test_approved_confirmation_delegates_to_the_underlying_provider(session_factory):
    async def confirm(mint, action, sol_amount):
        return True

    provider = ManualExecutionProvider(underlying=PaperExecutionProvider(session_factory), confirm=confirm)
    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-buy", slot=1, block_time=T0,
    )

    assert position is not None
    assert position.mint == "MintA"


async def test_close_position_is_also_gated_by_confirmation(session_factory):
    approvals = iter([True, False])

    async def confirm(mint, action, sol_amount):
        return next(approvals)

    underlying = PaperExecutionProvider(session_factory)
    provider = ManualExecutionProvider(underlying=underlying, confirm=confirm)

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-buy", slot=1, block_time=T0,
    )
    assert position is not None

    closed = await provider.close_position(
        position, price_sol=Decimal("0.002"), signature="sig-sell", slot=2, block_time=T0, reason="CREATOR_SOLD",
    )
    assert closed is None  # second confirm() call declines
