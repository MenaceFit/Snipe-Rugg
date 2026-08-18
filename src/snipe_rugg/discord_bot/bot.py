"""Discord bot: /wallet and /watchlist slash commands (spec section 7-8).

Command logic lives in services.py and is tested there without a live Discord
connection - this module is just the app_commands wiring on top of it.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from snipe_rugg.discord_bot.services import (
    DevCommandService,
    WalletCommandService,
    WatchlistCommandService,
)


class WalletGroup(app_commands.Group):
    def __init__(self, service: WalletCommandService) -> None:
        super().__init__(name="wallet", description="Manage tracked wallets")
        self._service = service

    @app_commands.command(name="add", description="Start tracking a wallet")
    @app_commands.describe(address="Wallet address", name="Optional label")
    async def add(self, interaction: discord.Interaction, address: str, name: str | None = None) -> None:
        await interaction.response.send_message(await self._service.add_wallet(address, name))

    @app_commands.command(name="remove", description="Stop tracking a wallet")
    @app_commands.describe(address="Wallet address")
    async def remove(self, interaction: discord.Interaction, address: str) -> None:
        await interaction.response.send_message(await self._service.remove_wallet(address))

    @app_commands.command(name="list", description="List tracked wallets")
    async def list_wallets(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self._service.list_wallets())

    @app_commands.command(name="info", description="Show details for a tracked wallet")
    @app_commands.describe(address="Wallet address")
    async def info(self, interaction: discord.Interaction, address: str) -> None:
        await interaction.response.send_message(await self._service.wallet_info(address))

    @app_commands.command(name="pause", description="Pause tracking a wallet")
    @app_commands.describe(address="Wallet address")
    async def pause(self, interaction: discord.Interaction, address: str) -> None:
        await interaction.response.send_message(await self._service.pause_wallet(address))

    @app_commands.command(name="resume", description="Resume tracking a wallet")
    @app_commands.describe(address="Wallet address")
    async def resume(self, interaction: discord.Interaction, address: str) -> None:
        await interaction.response.send_message(await self._service.resume_wallet(address))


class WatchlistGroup(app_commands.Group):
    def __init__(self, service: WatchlistCommandService) -> None:
        super().__init__(name="watchlist", description="Manage wallet watchlists")
        self._service = service

    @app_commands.command(name="add", description="Create a new watchlist")
    @app_commands.describe(name="Watchlist name")
    async def add(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.send_message(await self._service.create_watchlist(name))

    @app_commands.command(name="add-wallet", description="Add a tracked wallet to a watchlist")
    @app_commands.describe(watchlist="Watchlist name", address="Wallet address")
    async def add_wallet(self, interaction: discord.Interaction, watchlist: str, address: str) -> None:
        await interaction.response.send_message(await self._service.add_wallet(watchlist, address))

    @app_commands.command(name="list", description="List the wallets in a watchlist")
    @app_commands.describe(watchlist="Watchlist name")
    async def list_wallets(self, interaction: discord.Interaction, watchlist: str) -> None:
        await interaction.response.send_message(await self._service.list_watchlist(watchlist))


class DevGroup(app_commands.Group):
    def __init__(self, service: DevCommandService) -> None:
        super().__init__(name="dev", description="Dev/creator profiles and repeated-pattern risk signals")
        self._service = service

    @app_commands.command(name="profile", description="Show a creator address's launch history and risk signals")
    @app_commands.describe(address="Creator wallet address")
    async def profile(self, interaction: discord.Interaction, address: str) -> None:
        await interaction.response.send_message(await self._service.profile(address))


class SnipeRuggBot(commands.Bot):
    def __init__(
        self,
        *,
        wallet_service: WalletCommandService,
        watchlist_service: WatchlistCommandService,
        dev_service: DevCommandService,
        intents: discord.Intents | None = None,
    ) -> None:
        super().__init__(command_prefix="!", intents=intents or discord.Intents.default())
        self._wallet_service = wallet_service
        self._watchlist_service = watchlist_service
        self._dev_service = dev_service

    async def setup_hook(self) -> None:
        self.tree.add_command(WalletGroup(self._wallet_service))
        self.tree.add_command(WatchlistGroup(self._watchlist_service))
        self.tree.add_command(DevGroup(self._dev_service))
        await self.tree.sync()


def build_bot(
    *,
    wallet_service: WalletCommandService,
    watchlist_service: WatchlistCommandService,
    dev_service: DevCommandService,
    intents: discord.Intents | None = None,
) -> SnipeRuggBot:
    return SnipeRuggBot(
        wallet_service=wallet_service,
        watchlist_service=watchlist_service,
        dev_service=dev_service,
        intents=intents,
    )
