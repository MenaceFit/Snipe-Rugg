from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.backtest.service import BacktestService
from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.dev.models import Severity
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.discord_bot.services import (
    BacktestCommandService,
    DevCommandService,
    GraphCommandService,
    StrategyCommandService,
    TraderCommandService,
    WalletCommandService,
    WatchlistCommandService,
)
from snipe_rugg.discovery.dexscreener import DexScreenerClient
from snipe_rugg.graph.service import GraphService
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.strategy.engine import StrategyEngine
from snipe_rugg.strategy.models import StrategyConfig
from snipe_rugg.tracking.wallet_tracker import wallet_subscription_key
from snipe_rugg.traders.insiders import InsiderSignal, InsiderSignalKind
from snipe_rugg.traders.service import TokenAnalysisReport
from snipe_rugg.traders.stats import TraderStats
from tests.helpers import FakeStreamingProvider


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.fixture
def provider():
    return FakeStreamingProvider()


async def test_add_wallet_persists_and_subscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)

    reply = await service.add_wallet("Wallet111", "DEV_ORANGE")

    assert "✅" in reply
    assert "DEV_ORANGE" in reply
    assert provider.calls == [
        ("subscribe_logs", (), {"mentions": ["Wallet111"], "commitment": "confirmed", "key": wallet_subscription_key("Wallet111")})
    ]


async def test_add_duplicate_wallet_is_a_friendly_message(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    reply = await service.add_wallet("Wallet111")

    assert "already tracked" in reply
    assert len(provider.calls) == 1  # no second subscribe attempt


async def test_remove_wallet_unsubscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    reply = await service.remove_wallet("Wallet111")

    assert "✅" in reply
    assert provider.calls[-1] == ("unsubscribe", (wallet_subscription_key("Wallet111"),), {})


async def test_remove_unknown_wallet_is_a_friendly_message(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    reply = await service.remove_wallet("Ghost111")
    assert "not tracked" in reply


async def test_pause_unsubscribes_and_resume_resubscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    await service.pause_wallet("Wallet111")
    assert provider.calls[-1][0] == "unsubscribe"

    await service.resume_wallet("Wallet111")
    assert provider.calls[-1][0] == "subscribe_logs"


async def test_list_wallets_shows_status_markers(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111", "DEV_ORANGE")
    await service.add_wallet("Wallet222", "DEV_BLUE")
    await service.pause_wallet("Wallet222")

    reply = await service.list_wallets()

    assert "🟢 DEV_ORANGE" in reply
    assert "⏸️ DEV_BLUE" in reply


async def test_wallet_info_reports_status(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111", "DEV_ORANGE")

    reply = await service.wallet_info("Wallet111")

    assert "DEV_ORANGE" in reply
    assert "ACTIVE" in reply


async def test_watchlist_create_and_add_wallet(session_factory, provider):
    wallet_service = WalletCommandService(session_factory=session_factory, provider=provider)
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await wallet_service.add_wallet("Wallet111", "DEV_ORANGE")

    create_reply = await watchlist_service.create_watchlist("RUGGERS")
    assert "✅" in create_reply

    add_reply = await watchlist_service.add_wallet("RUGGERS", "Wallet111")
    assert "✅" in add_reply

    listing = await watchlist_service.list_watchlist("RUGGERS")
    assert "DEV_ORANGE" in listing


async def test_watchlist_add_wallet_to_unknown_watchlist(session_factory, provider):
    wallet_service = WalletCommandService(session_factory=session_factory, provider=provider)
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await wallet_service.add_wallet("Wallet111")

    reply = await watchlist_service.add_wallet("NOPE", "Wallet111")
    assert "does not exist" in reply


async def test_watchlist_add_unknown_wallet(session_factory):
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await watchlist_service.create_watchlist("RUGGERS")

    reply = await watchlist_service.add_wallet("RUGGERS", "GhostWallet111")
    assert "not tracked yet" in reply


async def test_dev_profile_reports_no_launches_for_unknown_creator(session_factory):
    service = DevCommandService(dev_monitor=DevMonitorService(session_factory))
    reply = await service.profile("GhostCreator111")
    assert "No launches on record" in reply


async def test_graph_command_reports_no_funding_for_isolated_wallet(session_factory):
    service = GraphCommandService(graph_service=GraphService(session_factory))
    text, png_bytes = await service.graph("LonelyWallet111")
    assert "No SOL funding relationships" in text
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


async def test_graph_command_lists_funder_in_text_summary(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.add_wallet("Wallet111")
        await repo.record_activity(
            NormalizedActivity(
                wallet="Wallet111", event_type=EventType.TRANSFER, mint="SOL", amount=Decimal(1),
                counterparty="FunderWallet111", slot=1, block_time=None, signature="sig-funded",
            )
        )
        await session.commit()

    service = GraphCommandService(graph_service=GraphService(session_factory))
    text, png_bytes = await service.graph("Wallet111")
    assert "Funded by: FunderWallet111" in text
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


async def test_dev_profile_summarizes_launch_history_and_signals(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        for i in range(4):
            await repo.record_token_launch(
                LaunchEvent(
                    mint=f"Mint{i}", creator="DevWallet111", launchpad="Pump.fun", pair="SOL", slot=1,
                    block_time=None, signature=f"sig-{i}",
                )
            )
        await session.commit()

    service = DevCommandService(dev_monitor=DevMonitorService(session_factory))
    reply = await service.profile("DevWallet111")

    assert "Launches: 4" in reply
    assert "graduated: 0" in reply
    assert "LOW_GRADUATION_RATE" in reply
    assert "HIGH-RISK REPEATED PATTERN" in reply


async def test_strategy_status_reports_config(session_factory):
    config = StrategyConfig(position_size_sol=Decimal("0.25"), max_open_positions=3)
    service = StrategyCommandService(session_factory=session_factory, config=config)
    reply = await service.status()
    assert "ENABLED" in reply
    assert "0.25" in reply
    assert "3" in reply


async def test_strategy_positions_reports_none_when_empty(session_factory):
    service = StrategyCommandService(session_factory=session_factory, config=StrategyConfig())
    reply = await service.positions()
    assert "No open paper positions" in reply


async def test_strategy_pnl_reports_none_when_no_closed_positions(session_factory):
    service = StrategyCommandService(session_factory=session_factory, config=StrategyConfig())
    reply = await service.pnl()
    assert "No closed paper positions" in reply


async def test_strategy_positions_and_pnl_reflect_a_full_round_trip(session_factory):
    mint = "MintXYZ1111111111111111111111111111111111"
    creator = "DevWallet222"
    async with session_factory() as session:
        await WalletRepository(session).record_token_launch(
            LaunchEvent(mint=mint, creator=creator, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await session.commit()

    engine = StrategyEngine(session_factory=session_factory, dev_monitor=DevMonitorService(session_factory))
    await engine.handle_trade(
        NormalizedTrade(
            wallet="TraderWallet111", token_in="SOL", token_out=mint, amount_in=Decimal("1.0"), amount_out=Decimal(1000),
            side=EventType.BUY, program="PumpSwap", slot=1, block_time=None, signature="sig-buy", confidence="high",
        )
    )

    service = StrategyCommandService(session_factory=session_factory, config=StrategyConfig())
    open_reply = await service.positions()
    assert mint in open_reply
    assert "TraderWallet111" in open_reply

    await engine.handle_trade(
        NormalizedTrade(
            wallet=creator, token_in=mint, token_out="SOL", amount_in=Decimal(1000), amount_out=Decimal("2.0"),
            side=EventType.SELL, program="PumpSwap", slot=2, block_time=None, signature="sig-sell", confidence="high",
        )
    )

    pnl_reply = await service.pnl()
    assert "1 win(s), 0 loss(es)" in pnl_reply
    # default position_size_sol=0.1: entry 0.1 SOL @ 0.001 SOL/token = 100 tokens;
    # exit @ 0.002 SOL/token = 0.2 SOL proceeds; pnl = 0.2 - 0.1
    assert "Total realized PnL: 0.1 SOL" in pnl_reply


async def test_backtest_run_reports_no_history_when_nothing_recorded(session_factory):
    service = BacktestCommandService(backtest_service=BacktestService(session_factory))
    reply = await service.run()
    assert "nothing to backtest" in reply


async def test_backtest_run_replays_recorded_history_and_reports_metrics(session_factory):
    mint = "MintXYZ1111111111111111111111111111111111"
    creator = "DevWallet333"
    now = utc_now()
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(mint=mint, creator=creator, launchpad="Pump.fun", pair="SOL", slot=1, block_time=now, signature="sig-launch")
        )
        await repo.record_trade(
            NormalizedTrade(
                wallet="TraderWallet111", token_in="SOL", token_out=mint, amount_in=Decimal("1.0"), amount_out=Decimal(1000),
                side=EventType.BUY, program="PumpSwap", slot=2, block_time=now + timedelta(minutes=1), signature="sig-buy",
                confidence="high",
            )
        )
        await repo.record_trade(
            NormalizedTrade(
                wallet=creator, token_in=mint, token_out="SOL", amount_in=Decimal(1000), amount_out=Decimal("2.0"),
                side=EventType.SELL, program="PumpSwap", slot=3, block_time=now + timedelta(minutes=5), signature="sig-sell",
                confidence="high",
            )
        )
        await session.commit()

    service = BacktestCommandService(backtest_service=BacktestService(session_factory))
    reply = await service.run(position_size_sol=1.0)

    assert "1 closed position(s)" in reply
    assert "1W / 0L" in reply
    assert "Total PnL: 1.0 SOL" in reply


def _dexscreener_client_returning(pairs: list[dict]) -> DexScreenerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": pairs})

    transport = httpx.MockTransport(handler)
    return DexScreenerClient(client=httpx.AsyncClient(base_url="https://example.invalid", transport=transport))


class _StubAnalysisService:
    def __init__(self, report: TokenAnalysisReport) -> None:
        self._report = report

    async def analyze_mint(self, mint: str) -> TokenAnalysisReport:
        return self._report


def _trader_stats(*, wallet: str) -> TraderStats:
    return TraderStats(
        wallet=wallet,
        mint="Mint111",
        trade_count=2,
        buy_count=1,
        sell_count=1,
        avg_buy_price_sol=Decimal("0.001"),
        avg_sell_price_sol=Decimal("0.002"),
        realized_pnl_sol=Decimal("0.5"),
        closed_trade_count=1,
        win_count=1,
        win_rate=Decimal(1),
        open_position_tokens=Decimal(0),
        trades_with_unknown_cost_basis=0,
        first_trade_slot=1,
        last_trade_slot=2,
    )


async def test_trending_lists_solana_pairs_with_volume_and_liquidity():
    client = _dexscreener_client_returning(
        [
            {
                "chainId": "solana",
                "baseToken": {"symbol": "FOO", "address": "FooMint111"},
                "liquidity": {"usd": 50_000.0},
                "volume": {"h24": 12345.0},
            }
        ]
    )
    service = TraderCommandService(dexscreener_client=client, analysis_service=None)  # type: ignore[arg-type]

    reply = await service.trending()

    assert "FOO" in reply
    assert "FooMint111" in reply
    assert "$12,345" in reply
    assert "$50,000" in reply


async def test_trending_reports_when_nothing_is_trending():
    client = _dexscreener_client_returning([])
    service = TraderCommandService(dexscreener_client=client, analysis_service=None)  # type: ignore[arg-type]

    reply = await service.trending()
    assert "No trending" in reply


async def test_traders_reports_when_no_trades_observed():
    report = TokenAnalysisReport(
        mint="Mint111", creator=None, creator_source="unknown", trades_analyzed=0, wallets_analyzed=0,
        top_traders=[], insider_signals=[],
    )
    service = TraderCommandService(dexscreener_client=None, analysis_service=_StubAnalysisService(report))  # type: ignore[arg-type]

    reply = await service.traders("Mint111")
    assert "No trades observed" in reply


async def test_traders_formats_leaderboard_and_insider_signals():
    stats = _trader_stats(wallet="Wallet111")
    signal = InsiderSignal(
        wallet="Wallet111",
        kind=InsiderSignalKind.DIRECTLY_FUNDED_BY_CREATOR,
        severity=Severity.HIGH,
        label="POSSIBLE INSIDER",
        description="Received SOL directly from the token's creator (`Creator111`)",
        related_wallets=["Creator111"],
    )
    report = TokenAnalysisReport(
        mint="Mint111", creator="Creator111", creator_source="tracked", trades_analyzed=2, wallets_analyzed=1,
        top_traders=[stats], insider_signals=[signal],
    )
    service = TraderCommandService(dexscreener_client=None, analysis_service=_StubAnalysisService(report))  # type: ignore[arg-type]

    reply = await service.traders("Mint111")

    assert "Wallet111" in reply
    assert "Creator111" in reply
    assert "win rate: 100%" in reply
    assert "POSSIBLE INSIDER" in reply
    assert "Received SOL directly from the token's creator" in reply
    assert "not a complete all-time history" in reply
