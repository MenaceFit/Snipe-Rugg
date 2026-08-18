from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from snipe_rugg.alerts.embeds import activity_embed, graduation_embed, launch_embed, trade_embed
from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace
from snipe_rugg.db.models import Token
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent


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


def test_launch_embed_shows_launchpad_pair_status_and_real_age():
    launch = LaunchEvent(
        mint="NewMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        creator="DevWallet111",
        launchpad="Pump.fun",
        pair="SOL",
        slot=123456789,
        block_time=utc_now() - timedelta(seconds=2),
        signature="sig-launch",
    )
    embed = launch_embed(launch, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert "DEV LAUNCH DETECTED" in (embed.title or "")
    assert _field(embed, "Wallet") == "DEV_ORANGE"
    assert _field(embed, "Launchpad") == "Pump.fun"
    assert _field(embed, "Pair") == "SOL"
    assert _field(embed, "Status") == "BONDING CURVE"
    age_field = _field(embed, "Age")
    assert age_field is not None and age_field.endswith("sec")
    assert 1.5 < float(age_field.split()[0]) < 3.0


def test_launch_embed_omits_age_when_block_time_unknown():
    launch = LaunchEvent(
        mint="NewMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        creator="DevWallet111",
        launchpad="Generic SPL",
        pair=None,
        slot=1,
        block_time=None,
        signature="sig-launch",
    )
    embed = launch_embed(launch, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert _field(embed, "Age") is None
    assert _field(embed, "Pair") is None


def test_graduation_embed_shows_status_and_tx_link():
    token = Token(
        mint="NewMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        creator_address="DevWallet111",
        launchpad="Pump.fun",
        pair="SOL",
        status="graduated",
        first_seen_slot=1,
        graduated_slot=999,
        graduated_signature="sig-graduation",
    )
    embed = graduation_embed(token, wallet_label="DEV_ORANGE", latency=LatencyTrace(provider_received_at=utc_now()))

    assert "GRADUATED" in (embed.title or "")
    assert _field(embed, "Wallet") == "DEV_ORANGE"
    assert _field(embed, "Slot") == "999"
    tx_field = _field(embed, "TX")
    assert tx_field is not None and "sig-graduation" in tx_field


def test_graduation_embed_without_a_known_wallet_omits_wallet_field():
    token = Token(
        mint="NewMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        creator_address="DevWallet111",
        launchpad="Pump.fun",
        pair="SOL",
        status="graduated",
        first_seen_slot=1,
    )
    embed = graduation_embed(token, wallet_label=None, latency=LatencyTrace(provider_received_at=utc_now()))
    assert _field(embed, "Wallet") is None
