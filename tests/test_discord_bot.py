"""Structural tests for the discord.py command wiring: verifies the command
groups expose the right subcommands without needing a live Discord connection
(app_commands.Group/Command objects are pure local data structures until
.sync() actually talks to Discord's API, which nothing here does)."""
from __future__ import annotations

from snipe_rugg.discord_bot.bot import WalletGroup, WatchlistGroup
from snipe_rugg.discord_bot.services import WalletCommandService, WatchlistCommandService


def test_wallet_group_exposes_expected_subcommands():
    group = WalletGroup(WalletCommandService(session_factory=None, provider=None))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"add", "remove", "list", "info", "pause", "resume"}


def test_watchlist_group_exposes_expected_subcommands():
    group = WatchlistGroup(WatchlistCommandService(session_factory=None))  # type: ignore[arg-type]
    names = {c.name for c in group.commands}
    assert names == {"add", "add-wallet", "list"}
