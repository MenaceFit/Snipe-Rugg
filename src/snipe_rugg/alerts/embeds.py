"""Discord embed builders for wallet activity alerts (spec section 26/30/17/60).

USD estimates and token name/ticker are deliberately absent: both need a
market-data / token-metadata provider (Phase 3's own remaining work — see
launchpad/detector.py's docstring for why ticker resolution specifically isn't
attempted yet), and this module never fabricates a value it doesn't have real
data for. "Age" is the one exception that looks like it might be invented but
isn't: it's computed from the transaction's actual block_time versus now, at
the moment the embed is built — a real measurement, not a guess. Large-sell-
percentage and dev-labeled alerts (spec section 31-33) need position-size and
creator-identity context that doesn't exist until the dev monitor (Phase 4).
"""
from __future__ import annotations

from datetime import datetime

import discord

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.models import Token
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent, LaunchpadStatus

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


def launch_embed(launch: LaunchEvent, *, wallet_label: str, latency: LatencyTrace) -> discord.Embed:
    embed = discord.Embed(title="🚨 DEV LAUNCH DETECTED", color=discord.Color.gold())
    embed.add_field(name="Wallet", value=wallet_label, inline=True)
    embed.add_field(name="Mint", value=_asset_label(launch.mint), inline=True)
    embed.add_field(name="Launchpad", value=launch.launchpad, inline=True)
    if launch.pair:
        embed.add_field(name="Pair", value=launch.pair, inline=True)
    embed.add_field(name="Status", value="BONDING CURVE", inline=True)
    age = _age_seconds(launch.block_time)
    if age is not None:
        embed.add_field(name="Age", value=f"{age:.1f} sec", inline=True)
    _add_latency_fields(embed, latency)
    embed.add_field(name="TX", value=f"[View]({_explorer_url(launch.signature)})", inline=False)
    if launch.block_time is not None:
        embed.timestamp = launch.block_time
    return embed


def graduation_embed(token: Token, *, wallet_label: str | None, latency: LatencyTrace) -> discord.Embed:
    embed = discord.Embed(title="🎓 GRADUATED", color=discord.Color.purple())
    if wallet_label:
        embed.add_field(name="Wallet", value=wallet_label, inline=True)
    embed.add_field(name="Mint", value=_asset_label(token.mint), inline=True)
    embed.add_field(name="Launchpad", value=token.launchpad, inline=True)
    embed.add_field(name="Status", value=LaunchpadStatus.GRADUATED.value.upper(), inline=True)
    if token.graduated_slot is not None:
        embed.add_field(name="Slot", value=str(token.graduated_slot), inline=True)
    _add_latency_fields(embed, latency)
    if token.graduated_signature:
        embed.add_field(name="TX", value=f"[View]({_explorer_url(token.graduated_signature)})", inline=False)
    if token.graduated_at is not None:
        embed.timestamp = token.graduated_at
    return embed


def _age_seconds(block_time: datetime | None) -> float | None:
    if block_time is None:
        return None
    return (utc_now() - block_time).total_seconds()


def _add_latency_fields(embed: discord.Embed, latency: LatencyTrace) -> None:
    if latency.detection_latency_ms is not None:
        embed.add_field(name="Detection", value=f"{latency.detection_latency_ms:.0f} ms", inline=True)
    if latency.total_latency_ms is not None:
        embed.add_field(name="Total latency", value=f"{latency.total_latency_ms:.0f} ms", inline=True)
