"""Regression test for the precision bug ExactNumeric fixes (found via Phase
6's strategy-engine tests — see db/types.py's docstring): plain
sqlalchemy.Numeric on SQLite round-trips a Decimal through binary floating
point, corrupting values like 0.1. ExactNumeric stores the exact decimal
string on SQLite instead.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import WalletActivity
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedActivity


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.mark.parametrize("raw", ["0.1", "0.2", "100.000000000000000006", "1234567890.123456789012345678"])
async def test_decimal_round_trips_exactly_through_sqlite(session_factory, raw):
    amount = Decimal(raw)
    async with session_factory() as session:
        await WalletRepository(session).record_activity(
            NormalizedActivity(
                wallet="Wallet111", event_type=EventType.TRANSFER, mint="SOL", amount=amount,
                counterparty="Wallet222", slot=1, block_time=None, signature=f"sig-{raw}",
            )
        )
        await session.commit()

    async with session_factory() as session:
        row = (
            await session.execute(select(WalletActivity).where(WalletActivity.signature == f"sig-{raw}"))
        ).scalar_one()
    assert row.amount == amount


async def test_decimal_arithmetic_computed_after_a_round_trip_stays_exact(session_factory):
    """The scenario that actually surfaced the bug: a value computed from two
    other round-tripped Decimals (paper-trading PnL) must itself be exact."""
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_activity(
            NormalizedActivity(
                wallet="Wallet111", event_type=EventType.TRANSFER, mint="SOL", amount=Decimal(100),
                counterparty="Wallet222", slot=1, block_time=None, signature="sig-a",
            )
        )
        await repo.record_activity(
            NormalizedActivity(
                wallet="Wallet111", event_type=EventType.TRANSFER, mint="SOL", amount=Decimal("0.002"),
                counterparty="Wallet222", slot=2, block_time=None, signature="sig-b",
            )
        )
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(select(WalletActivity).where(WalletActivity.wallet_address == "Wallet111"))
        ).scalars().all()
    a = next(r.amount for r in rows if r.signature == "sig-a")
    b = next(r.amount for r in rows if r.signature == "sig-b")
    assert a * b - Decimal("0.1") == Decimal("0.1")
