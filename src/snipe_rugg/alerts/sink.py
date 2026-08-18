"""The AlertSink protocol: the one seam between business logic
(tracking/wallet_tracker.py, tracking/token_tracker.py, launchpad/monitor.py,
strategy/*) and discord.py. None of those modules import `discord` — only
discord_bot/alert_sink.py implements this Protocol concretely, which is what
keeps the whole detection pipeline unit-testable without a live gateway
connection.

Lives in its own module (rather than inside wallet_tracker.py, where it
started in Phase 2) so dev/alerts.py and future callers can depend on it
without importing tracking/wallet_tracker.py itself and risking a cycle —
wallet_tracker.py re-exports both names for backward compatibility.
"""
from __future__ import annotations

from typing import Protocol

from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.models import Token, TrackedWallet
from snipe_rugg.decoder.models import NormalizedActivity, NormalizedTrade
from snipe_rugg.dev.models import DevRiskAssessment
from snipe_rugg.launchpad.models import LaunchEvent

BusinessEvent = NormalizedTrade | NormalizedActivity


class AlertSink(Protocol):
    async def send(self, *, wallet: TrackedWallet, event: BusinessEvent, latency: LatencyTrace) -> str | None:
        """Deliver one trade/activity alert; return a message id if available."""
        ...

    async def send_launch(self, *, wallet: TrackedWallet, launch: LaunchEvent, latency: LatencyTrace) -> str | None:
        """Deliver a "new token launched by a tracked/auto-discovered wallet" alert."""
        ...

    async def send_graduation(self, *, token: Token, wallet: TrackedWallet | None, latency: LatencyTrace) -> str | None:
        """Deliver a "token graduated to PumpSwap" alert (tracking/token_tracker.py)."""
        ...

    async def send_dev_risk(
        self, *, wallet: TrackedWallet | None, assessment: DevRiskAssessment, latency: LatencyTrace
    ) -> str | None:
        """Deliver a "HIGH-RISK REPEATED PATTERN" dev alert (dev/alerts.py)."""
        ...
