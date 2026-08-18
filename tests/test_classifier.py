from __future__ import annotations

from decimal import Decimal

from snipe_rugg.decoder.classifier import classify
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from tests.helpers import load_fixture

decoder = TransactionDecoder()
WALLET_BUYER = "WalletBuyer1111111111111111111111111111111"
DEV_WALLET = "DevWallet111111111111111111111111111111111"


def test_buy_detected_from_sol_and_token_deltas():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    events = classify(tx, WALLET_BUYER)

    assert len(events) == 1
    trade = events[0]
    assert isinstance(trade, NormalizedTrade)
    assert trade.side is EventType.BUY
    assert trade.token_in == "SOL"
    assert trade.amount_in == Decimal("1.000005")
    assert trade.token_out == "TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    assert trade.amount_out == Decimal(5)
    assert trade.program == "PumpSwap"
    assert trade.confidence == "high"


def test_sell_detected_from_token_and_sol_deltas():
    tx = decoder.decode(load_fixture("tx_sell_pumpswap.json"))
    events = classify(tx, WALLET_BUYER)

    assert len(events) == 1
    trade = events[0]
    assert isinstance(trade, NormalizedTrade)
    assert trade.side is EventType.SELL
    assert trade.token_in == "TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    assert trade.amount_in == Decimal(5)
    assert trade.token_out == "SOL"
    assert trade.amount_out == Decimal("0.3425")
    assert trade.program == "PumpSwap"


def test_plain_sol_transfer_is_not_a_trade():
    tx = decoder.decode(load_fixture("tx_sol_transfer.json"))
    events = classify(tx, WALLET_BUYER)

    assert len(events) == 1
    activity = events[0]
    assert isinstance(activity, NormalizedActivity)
    assert activity.event_type is EventType.TRANSFER
    assert activity.mint == "SOL"
    assert activity.amount == Decimal("-0.200005")


def test_self_mint_is_not_misclassified_as_a_buy():
    """A wallet minting new tokens to itself pays SOL rent and receives tokens -
    the same balance-delta shape as a BUY - but no DEX was involved, so it must
    not be reported as one."""
    tx = decoder.decode(load_fixture("tx_token_create.json"))
    events = classify(tx, DEV_WALLET)

    assert not any(isinstance(e, NormalizedTrade) for e in events)


def test_token_create_emits_token_create_and_mint_not_a_redundant_transfer():
    tx = decoder.decode(load_fixture("tx_token_create.json"))
    events = classify(tx, DEV_WALLET)

    by_type = {e.event_type: e for e in events if isinstance(e, NormalizedActivity)}
    assert EventType.TOKEN_CREATE in by_type
    assert by_type[EventType.TOKEN_CREATE].mint == "NewMintAccount111111111111111111111111111"

    assert EventType.MINT in by_type
    mint_event = by_type[EventType.MINT]
    assert mint_event.mint == "NewMintAccount111111111111111111111111111"
    assert mint_event.amount == Decimal(1000000)

    # the minted tokens must not also show up as a plain TRANSFER of the same mint
    token_transfers = [
        e for e in events if isinstance(e, NormalizedActivity) and e.event_type is EventType.TRANSFER and e.mint != "SOL"
    ]
    assert token_transfers == []

    # the SOL spent on rent is still reported
    sol_transfers = [
        e for e in events if isinstance(e, NormalizedActivity) and e.event_type is EventType.TRANSFER and e.mint == "SOL"
    ]
    assert len(sol_transfers) == 1
    assert sol_transfers[0].amount == Decimal("-0.003405")


def test_failed_transaction_yields_no_events():
    raw = load_fixture("tx_buy_pumpswap.json")
    raw["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    tx = decoder.decode(raw)
    assert classify(tx, WALLET_BUYER) == []


def test_unrelated_wallet_yields_no_events():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert classify(tx, "SomeUnrelatedWallet11111111111111111111111") == []
