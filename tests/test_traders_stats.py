from __future__ import annotations

from decimal import Decimal

from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.traders.stats import compute_trader_stats

MINT = "TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
ALICE = "AliceWallet111111111111111111111111111111"
BOB = "BobWallet1111111111111111111111111111111111"


def _trade(
    *, wallet=ALICE, side, token_in, token_out, amount_in, amount_out, slot, mint=MINT, program="PumpSwap"
) -> NormalizedTrade:
    return NormalizedTrade(
        wallet=wallet,
        token_in=token_in,
        token_out=token_out,
        amount_in=Decimal(str(amount_in)) if amount_in is not None else None,
        amount_out=Decimal(str(amount_out)) if amount_out is not None else None,
        side=side,
        program=program,
        slot=slot,
        block_time=None,
        signature=f"sig-{wallet}-{slot}",
        confidence="high",
    )


def _buy(*, wallet=ALICE, sol_spent, tokens_bought, slot, mint=MINT) -> NormalizedTrade:
    return _trade(
        wallet=wallet, side=EventType.BUY, token_in="SOL", token_out=mint, amount_in=sol_spent, amount_out=tokens_bought, slot=slot
    )


def _sell(*, wallet=ALICE, tokens_sold, sol_received, slot, mint=MINT) -> NormalizedTrade:
    return _trade(
        wallet=wallet, side=EventType.SELL, token_in=mint, token_out="SOL", amount_in=tokens_sold, amount_out=sol_received, slot=slot
    )


def _stats_for(trades, wallet=ALICE):
    [stats] = [s for s in compute_trader_stats(trades, MINT) if s.wallet == wallet]
    return stats


def test_buy_then_full_sell_at_a_profit_is_a_win():
    trades = [
        _buy(sol_spent="1.0", tokens_bought="100", slot=1),
        _sell(tokens_sold="100", sol_received="2.0", slot=2),
    ]
    stats = _stats_for(trades)

    assert stats.avg_buy_price_sol == Decimal("1.0") / Decimal(100)
    assert stats.avg_sell_price_sol == Decimal("2.0") / Decimal(100)
    assert stats.realized_pnl_sol == Decimal("1.0")
    assert stats.closed_trade_count == 1
    assert stats.win_count == 1
    assert stats.win_rate == Decimal(1)
    assert stats.open_position_tokens == Decimal(0)
    assert stats.trades_with_unknown_cost_basis == 0


def test_buy_then_full_sell_at_a_loss_is_not_a_win():
    trades = [
        _buy(sol_spent="2.0", tokens_bought="100", slot=1),
        _sell(tokens_sold="100", sol_received="1.0", slot=2),
    ]
    stats = _stats_for(trades)

    assert stats.realized_pnl_sol == Decimal("-1.0")
    assert stats.win_count == 0
    assert stats.win_rate == Decimal(0)


def test_multiple_buys_blend_into_an_average_cost_basis():
    trades = [
        _buy(sol_spent="1.0", tokens_bought="100", slot=1),  # 0.01/token
        _buy(sol_spent="3.0", tokens_bought="100", slot=2),  # 0.03/token
        # blended cost basis: 4.0 SOL / 200 tokens = 0.02/token
        _sell(tokens_sold="200", sol_received="6.0", slot=3),
    ]
    stats = _stats_for(trades)

    assert stats.avg_buy_price_sol == Decimal("4.0") / Decimal(200)
    assert stats.realized_pnl_sol == Decimal("6.0") - Decimal("4.0")
    assert stats.win_rate == Decimal(1)


def test_sell_with_no_prior_buy_has_unknown_cost_basis_and_is_excluded_from_pnl():
    trades = [_sell(tokens_sold="50", sol_received="1.0", slot=1)]
    stats = _stats_for(trades)

    assert stats.avg_sell_price_sol == Decimal("1.0") / Decimal(50)
    assert stats.realized_pnl_sol == Decimal(0)
    assert stats.closed_trade_count == 0
    assert stats.win_rate is None
    assert stats.trades_with_unknown_cost_basis == 1


def test_sell_larger_than_known_position_prorates_pnl_and_flags_partial_unknown_basis():
    trades = [
        _buy(sol_spent="1.0", tokens_bought="100", slot=1),  # 0.01/token cost basis
        _sell(tokens_sold="300", sol_received="9.0", slot=2),  # only 100 of the 300 have a known cost basis
    ]
    stats = _stats_for(trades)

    # matched 100/300 of proceeds: 9.0 * (100/300) = 3.0, minus cost basis 100 * 0.01 = 1.0
    assert stats.realized_pnl_sol == Decimal("2.0")
    assert stats.closed_trade_count == 1
    assert stats.win_count == 1
    assert stats.open_position_tokens == Decimal(0)
    assert stats.trades_with_unknown_cost_basis == 1


def test_open_position_with_no_sell_has_no_closed_trades():
    trades = [_buy(sol_spent="1.0", tokens_bought="100", slot=1)]
    stats = _stats_for(trades)

    assert stats.open_position_tokens == Decimal(100)
    assert stats.closed_trade_count == 0
    assert stats.win_rate is None
    assert stats.buy_count == 1
    assert stats.sell_count == 0


def test_non_sol_paired_trade_counts_but_is_excluded_from_price_math():
    other_token = "SomeOtherTokenYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY"
    trades = [
        _trade(side=EventType.BUY, token_in=other_token, token_out=MINT, amount_in="5", amount_out="100", slot=1),
    ]
    stats = _stats_for(trades)

    assert stats.trade_count == 1
    assert stats.buy_count == 1
    assert stats.avg_buy_price_sol is None
    assert stats.open_position_tokens == Decimal(0)


def test_compute_trader_stats_separates_wallets_and_filters_by_mint():
    other_mint = "OtherMintZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    trades = [
        _buy(wallet=ALICE, sol_spent="1.0", tokens_bought="100", slot=1),
        _buy(wallet=BOB, sol_spent="2.0", tokens_bought="50", slot=1),
        _buy(wallet=ALICE, sol_spent="1.0", tokens_bought="10", slot=2, mint=other_mint),
    ]
    results = {s.wallet: s for s in compute_trader_stats(trades, MINT)}

    assert set(results) == {ALICE, BOB}
    assert results[ALICE].trade_count == 1
    assert results[BOB].open_position_tokens == Decimal(50)


def test_first_and_last_trade_slot_track_the_observed_window():
    trades = [
        _buy(sol_spent="1.0", tokens_bought="100", slot=5),
        _sell(tokens_sold="50", sol_received="1.0", slot=9),
    ]
    stats = _stats_for(trades)

    assert stats.first_trade_slot == 5
    assert stats.last_trade_slot == 9
