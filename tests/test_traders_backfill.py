from __future__ import annotations

from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.traders.backfill import backfill_mint_trades, backfill_wallet_funders
from tests.helpers import load_fixture

MINT = "TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
TRADER = "WalletBuyer1111111111111111111111111111111"


class FakeRpc:
    def __init__(self, *, signatures, tx_by_signature, fail_signatures=()):
        self._signatures = signatures
        self._tx_by_signature = tx_by_signature
        self._fail_signatures = set(fail_signatures)
        self.requested_signatures: list[str] = []

    async def get_signatures_for_address(self, address, *, before=None, until=None, limit=1000):
        return self._signatures

    async def get_transaction(self, signature, **kwargs):
        self.requested_signatures.append(signature)
        if signature in self._fail_signatures:
            raise RuntimeError("simulated RPC failure")
        return self._tx_by_signature.get(signature)

    async def get_slot(self, **kwargs):
        raise NotImplementedError


def _sig_entry(signature, *, err=None):
    return {"signature": signature, "err": err}


async def test_backfill_mint_trades_returns_buy_and_sell_from_real_fixtures():
    buy_tx = load_fixture("tx_buy_pumpswap.json")
    sell_tx = load_fixture("tx_sell_pumpswap.json")
    buy_sig = buy_tx["transaction"]["signatures"][0]
    sell_sig = sell_tx["transaction"]["signatures"][0]

    rpc = FakeRpc(
        signatures=[_sig_entry(sell_sig), _sig_entry(buy_sig)],
        tx_by_signature={buy_sig: buy_tx, sell_sig: sell_tx},
    )

    trades = await backfill_mint_trades(rpc, MINT)

    assert len(trades) == 2
    assert all(isinstance(t, NormalizedTrade) for t in trades)
    assert all(t.wallet == TRADER for t in trades)
    sides = {t.side for t in trades}
    assert sides == {EventType.BUY, EventType.SELL}


async def test_backfill_mint_trades_skips_failed_signatures_without_fetching_them():
    rpc = FakeRpc(signatures=[_sig_entry("sig-failed", err={"InstructionError": [0, "Custom"]})], tx_by_signature={})

    trades = await backfill_mint_trades(rpc, MINT)

    assert trades == []
    assert rpc.requested_signatures == []


async def test_backfill_mint_trades_tolerates_a_missing_transaction():
    rpc = FakeRpc(signatures=[_sig_entry("sig-vanished")], tx_by_signature={})
    assert await backfill_mint_trades(rpc, MINT) == []


async def test_backfill_mint_trades_tolerates_an_rpc_exception_for_one_signature():
    buy_tx = load_fixture("tx_buy_pumpswap.json")
    buy_sig = buy_tx["transaction"]["signatures"][0]

    rpc = FakeRpc(
        signatures=[_sig_entry("sig-broken"), _sig_entry(buy_sig)],
        tx_by_signature={buy_sig: buy_tx},
        fail_signatures=["sig-broken"],
    )

    trades = await backfill_mint_trades(rpc, MINT)
    assert len(trades) == 1
    assert trades[0].side is EventType.BUY


async def test_backfill_mint_trades_filters_out_trades_on_other_mints():
    buy_tx = load_fixture("tx_buy_pumpswap.json")
    buy_sig = buy_tx["transaction"]["signatures"][0]
    rpc = FakeRpc(signatures=[_sig_entry(buy_sig)], tx_by_signature={buy_sig: buy_tx})

    trades = await backfill_mint_trades(rpc, "SomeCompletelyDifferentMint1111111111111111")
    assert trades == []


async def test_backfill_wallet_funders_finds_the_sol_transfer_sender():
    transfer_tx = load_fixture("tx_sol_transfer.json")
    sig = transfer_tx["transaction"]["signatures"][0]
    receiver = "SomeOtherWallet11111111111111111111111111111"
    rpc = FakeRpc(signatures=[_sig_entry(sig)], tx_by_signature={sig: transfer_tx})

    funders = await backfill_wallet_funders(rpc, receiver)

    assert funders == {"WalletBuyer1111111111111111111111111111111"}


async def test_backfill_wallet_funders_does_not_count_the_sender_side_as_self_funded():
    transfer_tx = load_fixture("tx_sol_transfer.json")
    sig = transfer_tx["transaction"]["signatures"][0]
    sender = "WalletBuyer1111111111111111111111111111111"
    rpc = FakeRpc(signatures=[_sig_entry(sig)], tx_by_signature={sig: transfer_tx})

    funders = await backfill_wallet_funders(rpc, sender)

    assert funders == set()
