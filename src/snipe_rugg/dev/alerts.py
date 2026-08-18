"""Shared dev-risk alert dispatch. WalletTracker, TokenTracker, and
launchpad/monitor.py's LaunchMonitor can each cause a creator's profile to
change (a new launch, a graduation, a sell of their own token) — this is the
one place that turns "profile changed" into "is this worth paging Discord
for," so all three escalate identically instead of three slightly-different
copies of the same threshold check.

Only alerts when `DevMonitorService.refresh` reports a newly-escalated signal
*and* the combined assessment reaches HIGH (see dev/patterns.py's `_combine`
— never on a single signal). A standing HIGH pattern that's simply
re-confirmed on the next launch does not re-alert.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.alerts.sink import AlertSink
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.models import Severity
from snipe_rugg.dev.service import DevMonitorService

logger = logging.getLogger(__name__)


async def refresh_and_maybe_alert(
    *,
    dev_monitor: DevMonitorService,
    alert_sink: AlertSink,
    session_factory: async_sessionmaker[AsyncSession],
    creator_address: str,
    latency: LatencyTrace,
) -> None:
    _, assessment, escalated = await dev_monitor.refresh(creator_address)
    if not escalated or assessment.overall_severity is not Severity.HIGH:
        return

    async with session_factory() as session:
        wallet = await WalletRepository(session).get_wallet(creator_address)

    message_id = await alert_sink.send_dev_risk(wallet=wallet, assessment=assessment, latency=latency)
    async with session_factory() as session:
        await WalletRepository(session).record_alert(
            wallet_address=creator_address,
            signature="",
            event_type="DEV_RISK",
            discord_message_id=message_id,
            total_latency_ms=latency.total_latency_ms,
        )
        await session.commit()
    logger.info(
        "dev_risk_alert_sent",
        extra={
            "fields": {
                "creator": creator_address,
                "overall_severity": assessment.overall_severity.value,
                "escalated_patterns": [s.pattern_type.value for s in escalated],
            }
        },
    )
