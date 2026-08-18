"""dev/alerts.py's refresh_and_maybe_alert: the shared escalation gate used by
WalletTracker, TokenTracker, and LaunchMonitor. Only alerts when the combined
assessment reaches HIGH (spec section 62's "never single-signal verdict" —
see test_dev_patterns.py for the severity-combination rules themselves) and
only on the turn a signal is newly escalated, not on every re-confirmation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.alerts import refresh_and_maybe_alert
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent

CREATOR = "DevWallet111"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeAlertSink:
    def __init__(self):
        self.dev_risk_alerts = []

    async def send_dev_risk(self, *, wallet, assessment, latency):
        self.dev_risk_alerts.append((wallet, assessment))
        return "fake-dev-risk-id"


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def _seed_ten_ungraduated_launches(session_factory) -> None:
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(10):
            await repo.record_token_launch(
                LaunchEvent(
                    mint=f"Mint{i}",
                    creator=CREATOR,
                    launchpad="Pump.fun",
                    pair="SOL",
                    slot=1,
                    block_time=T0 + timedelta(minutes=20 * i),
                    signature=f"sig-{i}",
                )
            )
        await session.commit()


async def test_no_alert_when_there_are_no_signals(session_factory):
    sink = FakeAlertSink()
    await refresh_and_maybe_alert(
        dev_monitor=DevMonitorService(session_factory),
        alert_sink=sink,
        session_factory=session_factory,
        creator_address=CREATOR,
        latency=LatencyTrace(provider_received_at=utc_now()),
    )
    assert sink.dev_risk_alerts == []


async def test_no_alert_when_only_a_single_high_signal_fires(session_factory):
    """10 launches, none graduated, but timestamps spread outside the
    serial-launcher window - only LOW_GRADUATION_RATE (HIGH) fires alone, so
    _combine downgrades the overall read to MEDIUM and no alert should fire."""
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(10):
            await repo.record_token_launch(
                LaunchEvent(
                    mint=f"Mint{i}",
                    creator=CREATOR,
                    launchpad="Pump.fun",
                    pair="SOL",
                    slot=1,
                    block_time=T0 + timedelta(days=10 * i),
                    signature=f"sig-{i}",
                )
            )
        await session.commit()

    sink = FakeAlertSink()
    await refresh_and_maybe_alert(
        dev_monitor=DevMonitorService(session_factory),
        alert_sink=sink,
        session_factory=session_factory,
        creator_address=CREATOR,
        latency=LatencyTrace(provider_received_at=utc_now()),
    )
    assert sink.dev_risk_alerts == []


async def test_alerts_once_two_signals_corroborate_to_overall_high(session_factory):
    await _seed_ten_ungraduated_launches(session_factory)

    sink = FakeAlertSink()
    await refresh_and_maybe_alert(
        dev_monitor=DevMonitorService(session_factory),
        alert_sink=sink,
        session_factory=session_factory,
        creator_address=CREATOR,
        latency=LatencyTrace(provider_received_at=utc_now()),
    )
    assert len(sink.dev_risk_alerts) == 1
    _, assessment = sink.dev_risk_alerts[0]
    assert assessment.overall_severity.value == "HIGH"


async def test_does_not_re_alert_on_a_standing_high_assessment(session_factory):
    await _seed_ten_ungraduated_launches(session_factory)

    sink = FakeAlertSink()
    dev_monitor = DevMonitorService(session_factory)
    latency = LatencyTrace(provider_received_at=utc_now())
    for _ in range(2):
        await refresh_and_maybe_alert(
            dev_monitor=dev_monitor,
            alert_sink=sink,
            session_factory=session_factory,
            creator_address=CREATOR,
            latency=latency,
        )
    assert len(sink.dev_risk_alerts) == 1
