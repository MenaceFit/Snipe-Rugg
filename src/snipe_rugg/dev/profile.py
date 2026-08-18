"""Builds a DevProfile from persisted data only (spec section 20-37): every
field is a real aggregate over `tokens` / `token_trades` rows, nothing here
estimates or infers a number it doesn't have. `build_profile` is a pure
function over already-fetched rows so it's testable without a database; see
dev/service.py for the DB-touching wrapper WalletTracker/TokenTracker use.
"""
from __future__ import annotations

from itertools import pairwise
from statistics import mean

from snipe_rugg.db.models import Token, TokenTrade
from snipe_rugg.dev.models import DevProfile, TokenSummary
from snipe_rugg.launchpad.models import LaunchpadStatus

# How soon after its own first_seen_at a creator selling the mint it just
# launched counts as an "early sell" for the DEV_SOLD_OWN_LAUNCH pattern
# (dev/patterns.py). One hour: long enough to not flag a dev clearing dust
# from an unrelated earlier position, short enough to still mean "sold near
# the top of their own bonding curve."
EARLY_SELL_WINDOW_SECONDS = 3600


def build_profile(creator_address: str, tokens: list[Token], trades: list[TokenTrade]) -> DevProfile:
    total = len(tokens)
    graduated = [t for t in tokens if t.status == LaunchpadStatus.GRADUATED.value]
    graduation_rate = (len(graduated) / total) if total else None

    launch_times = sorted(t.first_seen_at for t in tokens if t.first_seen_at is not None)
    first_launch_at = launch_times[0] if launch_times else None
    last_launch_at = launch_times[-1] if launch_times else None
    avg_gap = _avg_gap_seconds(launch_times) if len(launch_times) >= 2 else None

    grad_durations = [
        (t.graduated_at - t.first_seen_at).total_seconds()
        for t in graduated
        if t.graduated_at is not None and t.first_seen_at is not None
    ]
    avg_grad_seconds = mean(grad_durations) if grad_durations else None

    summaries = [
        TokenSummary(
            mint=t.mint,
            launchpad=t.launchpad,
            status=t.status,
            first_seen_at=t.first_seen_at,
            graduated_at=t.graduated_at,
        )
        for t in tokens
    ]

    return DevProfile(
        creator_address=creator_address,
        total_launches=total,
        graduated_count=len(graduated),
        graduation_rate=graduation_rate,
        first_launch_at=first_launch_at,
        last_launch_at=last_launch_at,
        avg_seconds_between_launches=avg_gap,
        avg_seconds_to_graduation=avg_grad_seconds,
        early_sell_count=_count_early_sells(tokens, trades),
        tokens=summaries,
    )


def _avg_gap_seconds(sorted_times: list) -> float:
    gaps = [(b - a).total_seconds() for a, b in pairwise(sorted_times)]
    return mean(gaps)


def _count_early_sells(tokens: list[Token], trades: list[TokenTrade]) -> int:
    by_mint = {t.mint: t for t in tokens}
    count = 0
    for trade in trades:
        if trade.side != "SELL":
            continue
        token = by_mint.get(trade.token_in or "")
        if token is None or token.first_seen_at is None or trade.block_time is None:
            continue
        if (trade.block_time - token.first_seen_at).total_seconds() <= EARLY_SELL_WINDOW_SECONDS:
            count += 1
    return count
