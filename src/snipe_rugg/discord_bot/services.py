"""Command logic for /wallet and /watchlist (spec section 7-8), independent of
discord.py's Interaction/decorator machinery so it's testable without a live
Discord connection. bot.py's app_commands wrappers just format these results
into Discord responses.

Adding a wallet here also subscribes it on the live provider immediately
(spec section 152's success criterion: adding a wallet should start showing
its activity without any other manual step) - pausing unsubscribes, resuming
resubscribes.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.backtest.service import BacktestService
from snipe_rugg.db.models import WalletStatus
from snipe_rugg.db.repository import (
    GroupAlreadyExists,
    GroupNotFound,
    WalletAlreadyTracked,
    WalletNotFound,
    WalletRepository,
)
from snipe_rugg.dev.patterns import assess_dev
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.graph.analysis import funded_by, funders_of, shares_a_funder_with
from snipe_rugg.graph.render import render_bubble_map
from snipe_rugg.graph.service import GraphService
from snipe_rugg.providers.base import StreamingProvider
from snipe_rugg.strategy.models import StrategyConfig
from snipe_rugg.tracking.wallet_tracker import wallet_subscription_key


class WalletCommandService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], provider: StreamingProvider) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def add_wallet(self, address: str, name: str | None = None) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).add_wallet(address, name=name)
            except WalletAlreadyTracked:
                return f"⚠️ `{address}` is already tracked."
            await session.commit()

        await self._provider.subscribe_logs(mentions=[address], key=wallet_subscription_key(address))
        return f"✅ Wallet added\n\nName: {name or address}\nAddress: {address}\nStatus: LIVE TRACKING"

    async def remove_wallet(self, address: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).remove_wallet(address)
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked."
            await session.commit()

        await self._provider.unsubscribe(wallet_subscription_key(address))
        return f"✅ Wallet removed: `{address}`"

    async def list_wallets(self) -> str:
        async with self._session_factory() as session:
            wallets = await WalletRepository(session).list_wallets()
        if not wallets:
            return "No wallets tracked yet. Add one with `/wallet add <address>`."
        lines = [
            f"{'🟢' if w.status == WalletStatus.ACTIVE.value else '⏸️'} {w.name or w.address} — `{w.address}`"
            for w in wallets
        ]
        return "\n".join(lines)

    async def wallet_info(self, address: str) -> str:
        async with self._session_factory() as session:
            wallet = await WalletRepository(session).get_wallet(address)
        if wallet is None:
            return f"⚠️ `{address}` is not tracked."
        return (
            f"Wallet: {wallet.name or wallet.address}\n"
            f"Address: `{wallet.address}`\n"
            f"Status: {wallet.status.upper()}\n"
            f"Tracked since: {wallet.created_at.isoformat()}"
        )

    async def pause_wallet(self, address: str) -> str:
        return await self._set_status(address, WalletStatus.PAUSED)

    async def resume_wallet(self, address: str) -> str:
        return await self._set_status(address, WalletStatus.ACTIVE)

    async def _set_status(self, address: str, status: WalletStatus) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).set_status(address, status)
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked."
            await session.commit()

        if status is WalletStatus.PAUSED:
            await self._provider.unsubscribe(wallet_subscription_key(address))
        else:
            await self._provider.subscribe_logs(mentions=[address], key=wallet_subscription_key(address))
        return f"✅ `{address}` is now {status.value.upper()}"


class WatchlistCommandService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_watchlist(self, name: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).create_group(name)
            except GroupAlreadyExists:
                return f"⚠️ Watchlist `{name}` already exists."
            await session.commit()
        return f"✅ Watchlist created: `{name}`"

    async def add_wallet(self, watchlist: str, address: str) -> str:
        async with self._session_factory() as session:
            try:
                await WalletRepository(session).add_wallet_to_group(watchlist, address)
            except GroupNotFound:
                return f"⚠️ Watchlist `{watchlist}` does not exist. Create it first with `/watchlist add`."
            except WalletNotFound:
                return f"⚠️ `{address}` is not tracked yet — add it first with `/wallet add`."
            await session.commit()
        return f"✅ Added `{address}` to `{watchlist}`"

    async def list_watchlist(self, watchlist: str) -> str:
        async with self._session_factory() as session:
            try:
                wallets = await WalletRepository(session).list_group_wallets(watchlist)
            except GroupNotFound:
                return f"⚠️ Watchlist `{watchlist}` does not exist."
        if not wallets:
            return f"`{watchlist}` has no wallets yet."
        return "\n".join(f"{w.name or w.address} — `{w.address}`" for w in wallets)


class DevCommandService:
    """spec section 20-37: on-demand dev profile/risk lookup, independent of
    whether any alert has ever fired for this address."""

    def __init__(self, *, dev_monitor: DevMonitorService) -> None:
        self._dev_monitor = dev_monitor

    async def profile(self, creator_address: str) -> str:
        profile = await self._dev_monitor.get_profile(creator_address)
        if profile.total_launches == 0:
            return f"No launches on record for `{creator_address}`."

        lines = [f"Creator: `{creator_address}`"]
        grad_suffix = f", {profile.graduation_rate:.0%}" if profile.graduation_rate is not None else ""
        lines.append(f"Launches: {profile.total_launches} (graduated: {profile.graduated_count}{grad_suffix})")
        if profile.first_launch_at is not None:
            lines.append(f"First launch: {profile.first_launch_at.isoformat()}")
        if profile.last_launch_at is not None:
            lines.append(f"Last launch: {profile.last_launch_at.isoformat()}")
        if profile.avg_seconds_between_launches is not None:
            lines.append(f"Avg. time between launches: {profile.avg_seconds_between_launches:.0f}s")
        if profile.avg_seconds_to_graduation is not None:
            lines.append(f"Avg. time to graduation: {profile.avg_seconds_to_graduation:.0f}s")
        if profile.early_sell_count:
            lines.append(f"Early sells of its own launches: {profile.early_sell_count}")

        assessment = assess_dev(profile)
        if assessment.overall_severity is not None:
            lines.append(f"\nOverall: {assessment.overall_severity.value}")
            lines.extend(
                f"  [{s.severity.value}] {s.pattern_type.value} — {s.label}: {s.description}"
                for s in assessment.signals
            )
        else:
            lines.append("\nNo repeated-pattern signals detected.")
        return "\n".join(lines)


class GraphCommandService:
    """spec section 64-69, 122, 144-145: wallet relationship graph, rendered
    as a bubble map. Text summary comes from graph/analysis.py's funder
    correlation; the PNG is graph/render.py's direct rendering of the same
    graph - both describe exactly the same underlying edges, nothing extra."""

    def __init__(self, *, graph_service: GraphService) -> None:
        self._graph_service = graph_service

    async def graph(self, address: str) -> tuple[str, bytes]:
        graph = await self._graph_service.build_context_graph(address)

        lines = [f"Relationship graph for `{address}` ({graph.number_of_nodes()} nodes)"]
        funders = funders_of(graph, address)
        funded = funded_by(graph, address)
        shared = sorted(shares_a_funder_with(graph, address))
        if funders:
            lines.append("Funded by: " + ", ".join(funders))
        if funded:
            lines.append("Funded: " + ", ".join(funded))
        if shared:
            lines.append("Shares a funder with: " + ", ".join(shared))
        if not funders and not funded:
            lines.append("No SOL funding relationships on record for this address yet.")

        png_bytes = render_bubble_map(graph, focus=address)
        return "\n".join(lines), png_bytes


class StrategyCommandService:
    """spec section 38-39, 119-121: read-only visibility into the paper
    strategy engine's own ledger — this service never opens or closes a
    position itself, strategy/engine.py does that from the trade stream."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], config: StrategyConfig) -> None:
        self._session_factory = session_factory
        self._config = config

    async def status(self) -> str:
        return (
            f"Paper trading: {'ENABLED' if self._config.enabled else 'DISABLED'}\n"
            f"Position size: {self._config.position_size_sol} SOL\n"
            f"Max open positions: {self._config.max_open_positions}\n"
            f"Skip high-risk creators: {self._config.skip_high_risk_creators}"
        )

    async def positions(self) -> str:
        async with self._session_factory() as session:
            open_positions = await WalletRepository(session).list_open_positions()
        if not open_positions:
            return "No open paper positions."
        lines = [f"{len(open_positions)} open position(s):"]
        lines.extend(
            f"`{p.mint}` — {p.entry_sol_amount} SOL @ {p.entry_price_sol} SOL/token (following `{p.followed_wallet}`)"
            for p in open_positions
        )
        return "\n".join(lines)

    async def pnl(self) -> str:
        async with self._session_factory() as session:
            closed = await WalletRepository(session).list_closed_positions()
        if not closed:
            return "No closed paper positions yet."
        total_pnl = sum((p.realized_pnl_sol or Decimal(0) for p in closed), Decimal(0))
        wins = sum(1 for p in closed if (p.realized_pnl_sol or Decimal(0)) > 0)
        return (
            f"{len(closed)} closed position(s) — {wins} win(s), {len(closed) - wins} loss(es)\n"
            f"Total realized PnL: {total_pnl} SOL"
        )


class BacktestCommandService:
    """spec section 41-47, 82-83, 91-94, 134-136: replays this deployment's
    own recorded history through the exact live StrategyEngine in an
    isolated scratch database (backtest/service.py) — never the live
    paper-trading ledger, so running a backtest can't corrupt real state and
    real state can't leak look-ahead into a backtest."""

    def __init__(self, *, backtest_service: BacktestService) -> None:
        self._backtest_service = backtest_service

    async def run(
        self,
        *,
        position_size_sol: float | None = None,
        max_open_positions: int | None = None,
        skip_high_risk_creators: bool | None = None,
    ) -> str:
        overrides: dict[str, object] = {}
        if position_size_sol is not None:
            overrides["position_size_sol"] = Decimal(str(position_size_sol))
        if max_open_positions is not None:
            overrides["max_open_positions"] = max_open_positions
        if skip_high_risk_creators is not None:
            overrides["skip_high_risk_creators"] = skip_high_risk_creators
        config = StrategyConfig(**overrides) if overrides else None  # type: ignore[arg-type]

        metrics = await self._backtest_service.run(config=config)
        if metrics.total_trades == 0:
            return "No historical launches/trades recorded yet — nothing to backtest."

        lines = [
            f"Backtest over {metrics.total_trades} closed position(s)",
            f"Win rate: {metrics.win_rate:.0%} ({metrics.wins}W / {metrics.losses}L)",
            f"Total PnL: {metrics.total_pnl_sol} SOL",
            f"Max drawdown: {metrics.max_drawdown_sol} SOL",
        ]
        if metrics.avg_hold_seconds is not None:
            lines.append(f"Avg hold time: {metrics.avg_hold_seconds:.0f}s")
        return "\n".join(lines)
