from __future__ import annotations

from snipe_rugg.decoder.constants import PUMP_FUN_PROGRAM, PUMPSWAP_PROGRAM
from snipe_rugg.decoder.transaction_decoder import TransactionDecoder
from snipe_rugg.launchpad.detector import (
    GenericTokenCreationDetector,
    PumpFunLaunchDetector,
    detect_launch,
)
from tests.helpers import load_fixture

decoder = TransactionDecoder()


def test_detects_pumpfun_launch_via_inner_initialize_mint():
    tx = decoder.decode(load_fixture("tx_pumpfun_launch.json"))
    event = detect_launch(tx)

    assert event is not None
    assert event.launchpad == "Pump.fun"
    assert event.mint == "NewPumpMintAccount1111111111111111111111111"
    assert event.creator == "DevWallet111111111111111111111111111111111"
    assert event.pair == "SOL"
    assert event.signature == tx.signature


def test_pumpfun_detector_ignores_non_pumpfun_tx():
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    assert PumpFunLaunchDetector().detect(tx) is None


def test_pumpfun_program_present_without_mint_creation_is_not_a_launch():
    """A BUY/SELL on an existing bonding curve also invokes the Pump.fun
    program, but doesn't create a mint - must not be mistaken for a launch."""
    tx = decoder.decode(load_fixture("tx_buy_pumpswap.json"))
    raw = tx.model_copy(update={"programs": [*tx.programs, PUMP_FUN_PROGRAM]})
    assert PumpFunLaunchDetector().detect(raw) is None


def test_generic_detector_uses_mint_authority_as_creator():
    tx = decoder.decode(load_fixture("tx_token_create.json"))
    event = GenericTokenCreationDetector().detect(tx)

    assert event is not None
    assert event.launchpad == "Generic SPL"
    assert event.creator == "DevWallet111111111111111111111111111111111"
    assert event.mint == "NewMintAccount111111111111111111111111111"
    assert event.pair is None


def test_detect_launch_prefers_pumpfun_over_generic():
    tx = decoder.decode(load_fixture("tx_pumpfun_launch.json"))
    event = detect_launch(tx)
    assert event is not None
    assert event.launchpad == "Pump.fun"


def test_detect_launch_returns_none_for_plain_transfer():
    tx = decoder.decode(load_fixture("tx_sol_transfer.json"))
    assert detect_launch(tx) is None


def test_graduation_fixture_touches_both_pumpfun_and_pumpswap_programs():
    tx = decoder.decode(load_fixture("tx_pumpfun_graduation.json"))
    assert PUMP_FUN_PROGRAM in tx.programs
    assert PUMPSWAP_PROGRAM in tx.programs
