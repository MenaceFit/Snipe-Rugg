"""Discord embed builders for wallet activity alerts (spec section 26/30).

USD estimates and token age are deliberately absent: both need a market-data /
token-metadata provider (Phase 3), and this module never fabricates a number it
doesn't have real data for. Large-sell-percentage and dev-labeled alerts (spec
section 31-33) need position-size and creator-identity context that doesn't
exist until the dev monitor (Phase 4) — not implemented here either.
"""
from __future__ import annotations

import discord

from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade

_TRADE_COLOR = {
    EventType.BUY: discord.Color.green(),
    EventType.SELL: discord.Color.red(),
    EventType.SWAP: discord.Color.blurple(),
}
_TRADE_EMOJI = {
    EventType.BUY: "🟢",
    EventType.SELL: "🔴",
    EventType.SWAP: "🔁",
}
_ACTIVITY_EMOJI = {
    EventType.TRANSFER: "↔️",
    EventType.MINT: "🪙",
    EventType.BURN: "🔥",
    EventType.APPROVAL: "✅",
    EventType.STAKE: "📌",
    EventType.UNSTAKE: "📤",
    EventType.ACCOUNT_CREATE: "🆕",
    EventType.ACCOUNT_CLOSE: "🗑️",
    EventType.TOKEN_CREATE: "🆕",
}


def _short(address: str, n: int = 4) -> str:
    if len(address) <= 2 * n + 3:
        return address
    return f"{address[:n]}...{address[-n:]}"


def _asset_label(asset: str | None) -> str:
    if asset is None:
        return "?"
    return "SOL" if asset == "SOL" else _short(asset)


def _explorer_url(signature: str) -> str:
    return f"https://solscan.io/tx/{signature}"


def trade_embed(trade: NormalizedTrade, *, wallet_label: str, latency: LatencyTrace) -> discord.Embed:
    emoji = _TRADE_EMOJI.get(trade.side, "🔁")
    embed = discord.Embed(
        title=f"{emoji} {trade.side.value} DETECTED", color=_TRADE_COLOR.get(trade.side, discord.Color.blurple())
    )
    embed.add_field(name="Wallet", value=wallet_label, inline=True)

    if trade.side is EventType.BUY:
        embed.add_field(name="Token", value=_asset_label(trade.token_out), inline=True)
        embed.add_field(name="Amount", value=f"{trade.amount_in} {_asset_label(trade.token_in)}", inline=True)
    elif trade.side is EventType.SELL:
        embed.add_field(name="Token", value=_asset_label(trade.token_in), inline=True)
        embed.add_field(name="Received", value=f"{trade.amount_out} {_asset_label(trade.token_out)}", inline=True)
    else:
        embed.add_field(name="In", value=f"{trade.amount_in} {_asset_label(trade.token_in)}", inline=True)
        embed.add_field(name="Out", value=f"{trade.amount_out} {_asset_label(trade.token_out)}", inline=True)

    if trade.program:
        embed.add_field(name="DEX", value=trade.program, inline=True)
    embed.add_field(name="Slot", value=str(trade.slot), inline=True)
    if trade.confidence != "high":
        embed.add_field(name="Confidence", value=trade.confidence, inline=True)
    _add_latency_fields(embed, latency)
    embed.add_field(name="TX", value=f"[View]({_explorer_url(trade.signature)})", inline=False)
    if trade.block_time is not None:
        embed.timestamp = trade.block_time
    return embed


def activity_embed(activity: NormalizedActivity, *, wallet_label: str, latency: LatencyTrace) -> discord.Embed:
    emoji = _ACTIVITY_EMOJI.get(activity.event_type, "ℹ️")
    embed = discord.Embed(title=f"{emoji} {activity.event_type.value}", color=discord.Color.light_grey())
    embed.add_field(name="Wallet", value=wallet_label, inline=True)
    if activity.mint:
        embed.add_field(name="Mint", value=_asset_label(activity.mint), inline=True)
    if activity.amount is not None:
        embed.add_field(name="Amount", value=str(activity.amount), inline=True)
    if activity.counterparty:
        embed.add_field(name="Counterparty", value=_short(activity.counterparty), inline=True)
    embed.add_field(name="Slot", value=str(activity.slot), inline=True)
    _add_latency_fields(embed, latency)
    embed.add_field(name="TX", value=f"[View]({_explorer_url(activity.signature)})", inline=False)
    if activity.block_time is not None:
        embed.timestamp = activity.block_time
    return embed


def _add_latency_fields(embed: discord.Embed, latency: LatencyTrace) -> None:
    if latency.detection_latency_ms is not None:
        embed.add_field(name="Detection", value=f"{latency.detection_latency_ms:.0f} ms", inline=True)
    if latency.total_latency_ms is not None:
        embed.add_field(name="Total latency", value=f"{latency.total_latency_ms:.0f} ms", inline=True)
