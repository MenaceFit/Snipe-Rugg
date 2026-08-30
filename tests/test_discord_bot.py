"""Structural tests for the discord.py command wiring: verifies the command
groups expose the right subcommands without needing a live Discord connection
(app_commands.Group/Command objects are pure local data structures until
.sync() actually talks to Discord's API, which nothing here does)."""
from __future__ import annotations

from snipe_rugg.discord_bot.bot import (
    BacktestGroup,
    DevGroup,
    StrategyGroup,
    TokenGroup,
    WalletGroup,
    WatchlistGroup,
)
from snipe_rugg.discord_bot.services import (
    BacktestCommandService,
    DevCommandService,
    GraphCommandService,
    StrategyCommandService,
    TraderCommandService,
    WalletCommandService,
    WatchlistCommandService,
)
from snipe_rugg.strategy.models import StrategyConfig


def test_wallet_group_exposes_expected_subcommands():
    group = WalletGroup(
        WalletCommandService(session_factory=None, provider=None),  # type: ignore[arg-type]
        GraphCommandService(graph_service=None),  # type: ignore[arg-type]
    )
    names = {c.name for c in group.commands}
    assert names == {"add", "remove", "list", "info", "pause", "resume", "graph"}


def test_watchlist_group_exposes_expected_subcommands():
    group = WatchlistGroup(WatchlistCommandService(session_factory=None))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"add", "add-wallet", "list"}


def test_dev_group_exposes_expected_subcommands():
    group = DevGroup(DevCommandService(dev_monitor=None))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"profile"}


def test_strategy_group_exposes_expected_subcommands():
    group = StrategyGroup(StrategyCommandService(session_factory=None, config=StrategyConfig()))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"status", "positions", "pnl"}


def test_backtest_group_exposes_expected_subcommands():
    group = BacktestGroup(BacktestCommandService(backtest_service=None))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"run"}


def test_token_group_exposes_expected_subcommands():
    group = TokenGroup(
        TraderCommandService(dexscreener_client=None, analysis_service=None)  # type: ignore[arg-type]
    )
    names = {c.name for c in group.commands}
    assert names == {"trending", "traders"}
