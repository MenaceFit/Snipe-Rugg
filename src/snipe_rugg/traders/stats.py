"""Per-wallet trade statistics for a single mint, computed from a bounded
backfill window (traders/backfill.py's backfill_mint_trades).

Average-cost-basis, not FIFO/LIFO: the simplest methodology that stays
correct without tracking individual buy lots, and a good fit since the
wallets this feature profiles overwhelmingly build and unwind one position
on a token rather than running disjoint FIFO lots. A SELL whose matching
BUY happened *before* the backfill window has no known cost basis — per
this feature's agreed "bounded window, not all-time" scope, that trade's
price still counts toward avg_sell_price_sol, but is excluded from
realized PnL and win rate rather than guessing a cost.
`trades_with_unknown_cost_basis` reports exactly how many of a wallet's
sells that affected, so nothing here implies more certainty about a
wallet's genuine all-time performance than the observed window supports.

Only SOL-paired legs are priced: this project's decoder overwhelmingly sees
SOL-paired pump.fun/PumpSwap trades (see launchpad/detector.py), and there
is no reliable USD/SOL cross rate available here to price a different quote
token against. A trade paired against something other than SOL still counts
toward trade_count/buy_count/sell_count but is excluded from price/PnL math.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel

from snipe_rugg.decoder.models import EventType, NormalizedTrade


class TraderStats(BaseModel):
    wallet: str
    mint: str
    trade_count: int
    buy_count: int
    sell_count: int
    avg_buy_price_sol: Decimal | None
    avg_sell_price_sol: Decimal | None
    realized_pnl_sol: Decimal
    closed_trade_count: int
    win_count: int
    win_rate: Decimal | None
    open_position_tokens: Decimal
    trades_with_unknown_cost_basis: int
    first_trade_slot: int
    last_trade_slot: int


def compute_trader_stats(trades: list[NormalizedTrade], mint: str) -> list[TraderStats]:
    """One TraderStats per wallet observed trading `mint` in `trades` (the
    output of backfill_mint_trades)."""
    by_wallet: dict[str, list[NormalizedTrade]] = defaultdict(list)
    for trade in trades:
        if mint in (trade.token_in, trade.token_out):
            by_wallet[trade.wallet].append(trade)
    return [_stats_for_wallet(wallet, wallet_trades, mint) for wallet, wallet_trades in by_wallet.items()]


def _stats_for_wallet(wallet: str, trades: list[NormalizedTrade], mint: str) -> TraderStats:
    ordered = sorted(trades, key=lambda t: t.slot)

    position_tokens = Decimal(0)
    cost_basis_sol = Decimal(0)
    total_buy_sol = Decimal(0)
    total_buy_tokens = Decimal(0)
    total_sell_sol = Decimal(0)
    total_sell_tokens = Decimal(0)
    realized_pnl_sol = Decimal(0)
    win_count = 0
    closed_trade_count = 0
    unknown_cost_basis = 0
    buy_count = 0
    sell_count = 0

    for trade in ordered:
        if trade.side is EventType.BUY and trade.token_out == mint:
            buy_count += 1
            if trade.token_in != "SOL" or not trade.amount_in or not trade.amount_out or trade.amount_out <= 0:
                continue
            sol_spent, tokens_bought = trade.amount_in, trade.amount_out
            total_buy_sol += sol_spent
            total_buy_tokens += tokens_bought
            position_tokens += tokens_bought
            cost_basis_sol += sol_spent

        elif trade.side is EventType.SELL and trade.token_in == mint:
            sell_count += 1
            if trade.token_out != "SOL" or not trade.amount_in or not trade.amount_out or trade.amount_in <= 0:
                continue
            sol_received, tokens_sold = trade.amount_out, trade.amount_in
            total_sell_sol += sol_received
            total_sell_tokens += tokens_sold

            if position_tokens > 0:
                avg_cost = cost_basis_sol / position_tokens
                matched_tokens = min(tokens_sold, position_tokens)
                # Proceeds prorated to the matched portion only, on the
                # assumption of one execution price for this trade — the
                # unmatched portion's proceeds are real but have no known
                # cost basis, so they're left out of realized PnL entirely.
                pnl = sol_received * (matched_tokens / tokens_sold) - (avg_cost * matched_tokens)
                realized_pnl_sol += pnl
                closed_trade_count += 1
                if pnl > 0:
                    win_count += 1
                cost_basis_sol -= avg_cost * matched_tokens
                position_tokens -= matched_tokens
                if tokens_sold > matched_tokens:
                    unknown_cost_basis += 1
            else:
                unknown_cost_basis += 1

    return TraderStats(
        wallet=wallet,
        mint=mint,
        trade_count=len(ordered),
        buy_count=buy_count,
        sell_count=sell_count,
        avg_buy_price_sol=(total_buy_sol / total_buy_tokens) if total_buy_tokens > 0 else None,
        avg_sell_price_sol=(total_sell_sol / total_sell_tokens) if total_sell_tokens > 0 else None,
        realized_pnl_sol=realized_pnl_sol,
        closed_trade_count=closed_trade_count,
        win_count=win_count,
        win_rate=(Decimal(win_count) / closed_trade_count) if closed_trade_count > 0 else None,
        open_position_tokens=position_tokens,
        trades_with_unknown_cost_basis=unknown_cost_basis,
        first_trade_slot=ordered[0].slot,
        last_trade_slot=ordered[-1].slot,
    )
