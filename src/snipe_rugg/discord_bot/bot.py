"""Discord bot: /wallet and /watchlist slash commands (spec section 7-8).

Command logic lives in services.py and is tested there without a live Discord
connection - this module is just the app_commands wiring on top of it.
"""
from __future__ import annotations

import io

import discord
from discord import app_commands
from discord.ext import commands

from snipe_rugg.discord_bot.services import (
    BacktestCommandService,
    DevCommandService,
    GraphCommandService,
    StrategyCommandService,
    TraderCommandService,
    WalletCommandService,
    WatchlistCommandService,
)


class WalletGroup(app_commands.Group):
    def __init__(self, service: WalletCommandService, graph_service: GraphCommandService) -> None:
        super().__init__(name="wallet", description="Manage tracked wallets")
        self._service = service
        self._graph_service = graph_service

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

    @app_commands.command(name="graph", description="Render a relationship bubble map for a wallet")
    @app_commands.describe(address="Wallet address")
    async def graph(self, interaction: discord.Interaction, address: str) -> None:
        text, png_bytes = await self._graph_service.graph(address)
        await interaction.response.send_message(text, file=discord.File(io.BytesIO(png_bytes), filename="graph.png"))


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


class StrategyGroup(app_commands.Group):
    def __init__(self, service: StrategyCommandService) -> None:
        super().__init__(name="strategy", description="Paper strategy engine status and positions")
        self._service = service

    @app_commands.command(name="status", description="Show the paper strategy engine's configuration")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self._service.status())

    @app_commands.command(name="positions", description="List open paper positions")
    async def positions(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self._service.positions())

    @app_commands.command(name="pnl", description="Show realized paper-trading PnL")
    async def pnl(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await self._service.pnl())


class BacktestGroup(app_commands.Group):
    def __init__(self, service: BacktestCommandService) -> None:
        super().__init__(name="backtest", description="Replay recorded history through the strategy engine")
        self._service = service

    @app_commands.command(name="run", description="Backtest the strategy over this deployment's recorded history")
    @app_commands.describe(
        position_size_sol="Override position size in SOL",
        max_open_positions="Override max concurrent open positions",
        skip_high_risk_creators="Override whether HIGH-risk creators are skipped",
    )
    async def run(
        self,
        interaction: discord.Interaction,
        position_size_sol: float | None = None,
        max_open_positions: int | None = None,
        skip_high_risk_creators: bool | None = None,
    ) -> None:
        await interaction.response.defer()
        reply = await self._service.run(
            position_size_sol=position_size_sol,
            max_open_positions=max_open_positions,
            skip_high_risk_creators=skip_high_risk_creators,
        )
        await interaction.followup.send(reply)


class TokenGroup(app_commands.Group):
    def __init__(self, service: TraderCommandService) -> None:
        super().__init__(name="token", description="Trending Solana tokens and top-trader / possible-insider analysis")
        self._service = service

    @app_commands.command(name="trending", description="List Solana tokens trending on DexScreener")
    async def trending(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self._service.trending())

    @app_commands.command(name="traders", description="Top traders, PnL/win-rate, and possible-insider signals for a token")
    @app_commands.describe(mint="Token mint address")
    async def traders(self, interaction: discord.Interaction, mint: str) -> None:
        await interaction.response.defer()
        await interaction.followup.send(await self._service.traders(mint))


class SnipeRuggBot(commands.Bot):
    def __init__(
        self,
        *,
        wallet_service: WalletCommandService,
        watchlist_service: WatchlistCommandService,
        dev_service: DevCommandService,
        graph_service: GraphCommandService,
        strategy_service: StrategyCommandService,
        backtest_service: BacktestCommandService,
        trader_service: TraderCommandService,
        intents: discord.Intents | None = None,
    ) -> None:
        super().__init__(command_prefix="!", intents=intents or discord.Intents.default())
        self._wallet_service = wallet_service
        self._watchlist_service = watchlist_service
        self._dev_service = dev_service
        self._graph_service = graph_service
        self._strategy_service = strategy_service
        self._backtest_service = backtest_service
        self._trader_service = trader_service

    async def setup_hook(self) -> None:
        self.tree.add_command(WalletGroup(self._wallet_service, self._graph_service))
        self.tree.add_command(WatchlistGroup(self._watchlist_service))
        self.tree.add_command(DevGroup(self._dev_service))
        self.tree.add_command(StrategyGroup(self._strategy_service))
        self.tree.add_command(BacktestGroup(self._backtest_service))
        self.tree.add_command(TokenGroup(self._trader_service))
        await self.tree.sync()


def build_bot(
    *,
    wallet_service: WalletCommandService,
    watchlist_service: WatchlistCommandService,
    dev_service: DevCommandService,
    graph_service: GraphCommandService,
    strategy_service: StrategyCommandService,
    backtest_service: BacktestCommandService,
    trader_service: TraderCommandService,
    intents: discord.Intents | None = None,
) -> SnipeRuggBot:
    return SnipeRuggBot(
        wallet_service=wallet_service,
        watchlist_service=watchlist_service,
        dev_service=dev_service,
        graph_service=graph_service,
        strategy_service=strategy_service,
        backtest_service=backtest_service,
        trader_service=trader_service,
        intents=intents,
    )
