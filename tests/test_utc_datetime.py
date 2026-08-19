"""Regression test for the tzinfo-loss bug UTCDateTime fixes (found via
Phase 8's live-execution-limit tests — see db/types.py's docstring): SQLite
has no native timezone-aware timestamp storage, so a bound tz-aware datetime
comes back naive, and comparing it against a fresh core.clock.utc_now()
value raises TypeError instead of just being wrong quietly — which is how
this got caught immediately rather than shipping.
"""
from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import Alert
from snipe_rugg.db.repository import WalletRepository


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_datetime_round_trips_with_tzinfo_preserved(session_factory):
    async with session_factory() as session:
        await WalletRepository(session).record_alert(
            wallet_address="Wallet111", signature="sig-a", event_type="BUY",
        )
        await session.commit()

    async with session_factory() as session:
        row = (await session.execute(select(Alert).where(Alert.signature == "sig-a"))).scalar_one()

    assert row.sent_at.tzinfo is not None
    assert row.sent_at.utcoffset() == timedelta(0)
    # The comparison that used to raise TypeError: can't compare
    # offset-naive and offset-aware datetimes.
    assert row.sent_at <= utc_now()


async def test_datetime_arithmetic_against_a_fresh_utc_now_does_not_raise(session_factory):
    async with session_factory() as session:
        await WalletRepository(session).record_alert(
            wallet_address="Wallet111", signature="sig-b", event_type="BUY",
        )
        await session.commit()

    async with session_factory() as session:
        row = (await session.execute(select(Alert).where(Alert.signature == "sig-b"))).scalar_one()

    age = utc_now() - row.sent_at
    assert age >= timedelta(0)
    assert row.sent_at.tzinfo == UTC or row.sent_at.utcoffset() == timedelta(0)
