"""Concrete AlertSink: posts wallet activity embeds to a configured Discord
channel. This is the only place that connects WalletTracker's decoupled
AlertSink protocol (tracking/wallet_tracker.py) to a real discord.py client."""
from __future__ import annotations

import logging

import discord

from snipe_rugg.alerts.embeds import activity_embed, trade_embed
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.models import TrackedWallet
from snipe_rugg.decoder.models import NormalizedTrade
from snipe_rugg.tracking.wallet_tracker import BusinessEvent

logger = logging.getLogger(__name__)


class DiscordAlertSink:
    def __init__(self, client: discord.Client, *, channel_id: int) -> None:
        self._client = client
        self._channel_id = channel_id

    async def send(self, *, wallet: TrackedWallet, event: BusinessEvent, latency: LatencyTrace) -> str | None:
        channel = self._client.get_channel(self._channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("alert_channel_unavailable", extra={"fields": {"channel_id": self._channel_id}})
            return None

        wallet_label = wallet.name or wallet.address
        embed = (
            trade_embed(event, wallet_label=wallet_label, latency=latency)
            if isinstance(event, NormalizedTrade)
            else activity_embed(event, wallet_label=wallet_label, latency=latency)
        )

        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("alert_send_failed", extra={"fields": {"channel_id": self._channel_id}})
            return None
        return str(message.id)
