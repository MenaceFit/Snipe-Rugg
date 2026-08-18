"""build_profile is a pure function over already-fetched rows (see
dev/profile.py's docstring) - these tests construct Token/TokenTrade rows
directly, no database needed, matching the module's own design."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from snipe_rugg.db.models import Token, TokenTrade
from snipe_rugg.dev.profile import EARLY_SELL_WINDOW_SECONDS, build_profile
from snipe_rugg.launchpad.models import LaunchpadStatus

CREATOR = "DevWallet111"


def _token(mint, *, status=LaunchpadStatus.BONDING_CURVE.value, first_seen_at=None, graduated_at=None) -> Token:
    return Token(
        mint=mint,
        creator_address=CREATOR,
        launchpad="Pump.fun",
        pair="SOL",
        status=status,
        first_seen_slot=1,
        first_seen_at=first_seen_at,
        graduated_at=graduated_at,
    )


def _sell(mint, *, block_time) -> TokenTrade:
    return TokenTrade(
        wallet_address=CREATOR,
        signature=f"sig-sell-{mint}",
        side="SELL",
        token_in=mint,
        token_out="SOL",
        slot=1,
        block_time=block_time,
    )


def test_empty_profile_has_no_rate_or_averages():
    profile = build_profile(CREATOR, [], [])
    assert profile.total_launches == 0
    assert profile.graduation_rate is None
    assert profile.avg_seconds_between_launches is None
    assert profile.avg_seconds_to_graduation is None
    assert profile.early_sell_count == 0


def test_single_launch_has_no_gap_average():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    profile = build_profile(CREATOR, [_token("MintA", first_seen_at=t0)], [])
    assert profile.total_launches == 1
    assert profile.graduation_rate == 0.0
    assert profile.avg_seconds_between_launches is None


def test_graduation_rate_and_avg_gap_computed_from_real_timestamps():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=10)
    t2 = t0 + timedelta(minutes=30)
    tokens = [
        _token("MintA", first_seen_at=t0, status=LaunchpadStatus.GRADUATED.value, graduated_at=t0 + timedelta(minutes=5)),
        _token("MintB", first_seen_at=t1),
        _token("MintC", first_seen_at=t2),
    ]
    profile = build_profile(CREATOR, tokens, [])

    assert profile.total_launches == 3
    assert profile.graduated_count == 1
    assert profile.graduation_rate == 1 / 3
    assert profile.first_launch_at == t0
    assert profile.last_launch_at == t2
    # gaps: 10min, 20min -> average 15min = 900s
    assert profile.avg_seconds_between_launches == 900.0
    assert profile.avg_seconds_to_graduation == 300.0


def test_early_sell_counted_within_window():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    tokens = [_token("MintA", first_seen_at=t0)]
    trades = [_sell("MintA", block_time=t0 + timedelta(seconds=EARLY_SELL_WINDOW_SECONDS - 1))]

    profile = build_profile(CREATOR, tokens, trades)
    assert profile.early_sell_count == 1


def test_late_sell_outside_window_is_not_counted():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    tokens = [_token("MintA", first_seen_at=t0)]
    trades = [_sell("MintA", block_time=t0 + timedelta(seconds=EARLY_SELL_WINDOW_SECONDS + 1))]

    profile = build_profile(CREATOR, tokens, trades)
    assert profile.early_sell_count == 0


def test_sell_of_a_mint_the_creator_did_not_launch_is_not_counted():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    tokens = [_token("MintA", first_seen_at=t0)]
    trades = [_sell("SomeoneElsesMint", block_time=t0 + timedelta(seconds=10))]

    profile = build_profile(CREATOR, tokens, trades)
    assert profile.early_sell_count == 0


def test_buy_trades_are_not_counted_as_early_sells():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    tokens = [_token("MintA", first_seen_at=t0)]
    buy = TokenTrade(
        wallet_address=CREATOR,
        signature="sig-buy",
        side="BUY",
        token_in="SOL",
        token_out="MintA",
        slot=1,
        block_time=t0 + timedelta(seconds=10),
    )
    profile = build_profile(CREATOR, tokens, [buy])
    assert profile.early_sell_count == 0
