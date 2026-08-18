"""DevMonitorService.refresh: the DB-backed glue between dev/profile.py,
dev/patterns.py, and persisted DevRiskSignal rows (see dev/service.py's
docstring for why WalletTracker/TokenTracker/LaunchMonitor all call through
this one place instead of duplicating escalation logic)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent

CREATOR = "DevWallet111"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def _launch(mint: str, *, block_time: datetime) -> LaunchEvent:
    return LaunchEvent(
        mint=mint, creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1, block_time=block_time,
        signature=f"sig-{mint}",
    )


async def _seed_five_launches(session_factory) -> None:
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(5):
            await repo.record_token_launch(_launch(f"Mint{i}", block_time=T0 + timedelta(minutes=20 * i)))
        await session.commit()


async def test_refresh_with_no_activity_returns_no_signals(session_factory):
    service = DevMonitorService(session_factory)
    profile, assessment, escalated = await service.refresh(CREATOR)
    assert profile.total_launches == 0
    assert assessment.signals == []
    assert escalated == []


async def test_refresh_persists_newly_detected_signals(session_factory):
    await _seed_five_launches(session_factory)

    service = DevMonitorService(session_factory)
    profile, assessment, escalated = await service.refresh(CREATOR)

    assert profile.total_launches == 5
    pattern_types = {s.pattern_type.value for s in assessment.signals}
    assert "SERIAL_LAUNCHER" in pattern_types
    assert "LOW_GRADUATION_RATE" in pattern_types  # nothing graduated yet
    assert len(escalated) == len(assessment.signals)  # all newly detected

    async with session_factory() as session:
        stored = {s.pattern_type for s in await WalletRepository(session).list_dev_risk_signals(CREATOR)}
    assert stored == pattern_types


async def test_refresh_does_not_re_escalate_a_standing_signal(session_factory):
    await _seed_five_launches(session_factory)
    service = DevMonitorService(session_factory)

    await service.refresh(CREATOR)
    _, _, escalated_again = await service.refresh(CREATOR)

    assert escalated_again == []


async def test_refresh_drops_a_signal_that_no_longer_holds(session_factory):
    await _seed_five_launches(session_factory)
    service = DevMonitorService(session_factory)
    await service.refresh(CREATOR)

    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(5):
            await repo.mark_graduated(f"Mint{i}", slot=1, block_time=T0, signature=f"sig-grad-{i}")
        await session.commit()

    await service.refresh(CREATOR)

    async with session_factory() as session:
        stored = {s.pattern_type for s in await WalletRepository(session).list_dev_risk_signals(CREATOR)}
    assert "LOW_GRADUATION_RATE" not in stored
