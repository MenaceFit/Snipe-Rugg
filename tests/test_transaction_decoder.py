from __future__ import annotations

from decimal import Decimal

from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from tests.helpers import load_fixture

decoder = TransactionDecoder()


def test_decodes_signature_slot_block_time_and_success():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert tx.signature == "BuyTxSignature1111111111111111111111111111111111111111111"
    assert tx.slot == 300000001
    assert tx.block_time is not None
    assert tx.block_time.timestamp() == 1755000001
    assert tx.success is True
    assert tx.err is None


def test_decodes_signer_from_account_keys():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert tx.signer == "WalletBuyer1111111111111111111111111111111"


def test_programs_include_top_level_and_inner_instructions():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA" in tx.programs
    assert "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" in tx.programs


def test_sol_balance_changes_only_include_accounts_that_moved():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    accounts_with_changes = {c.account for c in tx.sol_balance_changes}
    assert accounts_with_changes == {"WalletBuyer1111111111111111111111111111111", "PoolAccount1111111111111111111111111111111"}
    buyer_change = next(c for c in tx.sol_balance_changes if c.account == "WalletBuyer1111111111111111111111111111111")
    assert buyer_change.delta_lamports == -1000005000


def test_spl_balance_changes_treat_missing_pre_balance_as_zero():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert len(tx.spl_balance_changes) == 1
    change = tx.spl_balance_changes[0]
    assert change.owner == "WalletBuyer1111111111111111111111111111111"
    assert change.pre_amount == 0
    assert change.post_amount == 5_000_000
    assert change.delta_ui_amount == Decimal(5)


def test_spl_balance_changes_treat_missing_post_balance_as_zero():
    tx = decoder.decode(load_fixture("tx_sell_pumpswap.json"))
    change = tx.spl_balance_changes[0]
    assert change.pre_amount == 5_000_000
    assert change.post_amount == 0
    assert change.delta_ui_amount == Decimal(-5)


def test_failed_transaction_marks_success_false():
    raw = load_fixture("tx_buy_pumpswap.json")
    raw["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    tx = decoder.decode(raw)
    assert tx.success is False
    assert tx.err == {"InstructionError": [0, "Custom"]}


def test_fee_is_decoded():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert tx.fee_lamports == 5000
