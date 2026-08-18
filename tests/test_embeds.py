from __future__ import annotations

from decimal import Decimal

from snipe_rugg.alerts.embeds import activity_embed, trade_embed
from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade


def _field(embed, name):
    return next((f.value for f in embed.fields if f.name == name), None)


def test_buy_embed_shows_token_and_amount_spent():
    trade = NormalizedTrade(
        wallet="Wallet111",
        token_in="SOL",
        token_out="TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        amount_in=Decimal("2.41"),
        amount_out=Decimal(500),
        side=EventType.BUY,
        program="PumpSwap",
        slot=123456789,
        block_time=utc_now(),
        signature="sig-buy",
        confidence="high",
    )
    embed = trade_embed(trade, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert "BUY" in (embed.title or "")
    assert _field(embed, "Wallet") == "DEV_ORANGE"
    assert "2.41 SOL" in (_field(embed, "Amount") or "")
    assert _field(embed, "DEX") == "PumpSwap"
    assert _field(embed, "Slot") == "123456789"


def test_sell_embed_shows_token_and_amount_received():
    trade = NormalizedTrade(
        wallet="Wallet111",
        token_in="TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        token_out="SOL",
        amount_in=Decimal(500),
        amount_out=Decimal("3.42"),
        side=EventType.SELL,
        program="PumpSwap",
        slot=123456790,
        block_time=utc_now(),
        signature="sig-sell",
        confidence="high",
    )
    embed = trade_embed(trade, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert "SELL" in (embed.title or "")
    assert "3.42 SOL" in (_field(embed, "Received") or "")


def test_low_confidence_swap_shows_confidence_field():
    trade = NormalizedTrade(
        wallet="Wallet111",
        token_in="MintA",
        token_out="MintB",
        amount_in=Decimal(10),
        amount_out=Decimal(20),
        side=EventType.SWAP,
        program=None,
        slot=1,
        block_time=None,
        signature="sig-swap",
        confidence="low",
    )
    embed = trade_embed(trade, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))
    assert _field(embed, "Confidence") == "low"


def test_latency_fields_present_when_known_absent_when_not():
    trade = NormalizedTrade(
        wallet="Wallet111", token_in="SOL", token_out="MintA", amount_in=Decimal(1), amount_out=Decimal(1),
        side=EventType.BUY, program=None, slot=1, block_time=None, signature="sig", confidence="high",
    )
    now = utc_now()
    latency_known = LatencyTrace(provider_received_at=now, ingested_at=now, alerted_at=now)
    embed_known = trade_embed(trade, wallet_label="W", latency=latency_known)
    assert _field(embed_known, "Detection") is not None
    assert _field(embed_known, "Total latency") is not None

    latency_unknown = LatencyTrace(provider_received_at=now)
    embed_unknown = trade_embed(trade, wallet_label="W", latency=latency_unknown)
    assert _field(embed_unknown, "Detection") is None
    assert _field(embed_unknown, "Total latency") is None


def test_transfer_activity_embed():
    activity = NormalizedActivity(
        wallet="Wallet111",
        event_type=EventType.TRANSFER,
        mint="SOL",
        amount=Decimal("-0.2"),
        counterparty="Wallet222",
        slot=42,
        block_time=utc_now(),
        signature="sig-transfer",
    )
    embed = activity_embed(activity, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert "TRANSFER" in (embed.title or "")
    assert _field(embed, "Amount") == "-0.2"
    assert _field(embed, "Mint") == "SOL"
