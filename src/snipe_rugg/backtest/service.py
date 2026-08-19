"""Provisions a fresh, isolated scratch database per backtest run so a
replay never reads from or writes into the live production database (spec
section 41-47) — a backtest must not corrupt live paper-trading state, and
must not let live state leak into what's supposed to be a clean historical
replay. StaticPool keeps every session opened during one run on the same
in-memory connection (see db/base.py's create_engine — the same pooling
pitfall Phase 2's tests first ran into applies here too).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from snipe_rugg.backtest.metrics import BacktestMetrics, compute_metrics
from snipe_rugg.backtest.replay import ReplayEngine, ReplayEvent
from snipe_rugg.backtest.source import load_events
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.strategy.models import StrategyConfig


class BacktestService:
    def __init__(self, source_session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._source_session_factory = source_session_factory

    async def _load_events(self) -> list[ReplayEvent]:
        async with self._source_session_factory() as session:
            return await load_events(WalletRepository(session))

    async def _run_once(
        self, events: list[ReplayEvent], *, config: StrategyConfig | None, speed_multiplier: float | None = None
    ) -> BacktestMetrics:
        scratch_engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            await init_models(scratch_engine)
            scratch_factory = create_session_factory(scratch_engine)
            replay = ReplayEngine(session_factory=scratch_factory, config=config)
            await replay.run(events, speed_multiplier=speed_multiplier)
            async with scratch_factory() as session:
                closed = await WalletRepository(session).list_closed_positions()
        finally:
            await scratch_engine.dispose()
        return compute_metrics(closed)

    async def run(
        self, *, config: StrategyConfig | None = None, speed_multiplier: float | None = None
    ) -> BacktestMetrics:
        events = await self._load_events()
        return await self._run_once(events, config=config, speed_multiplier=speed_multiplier)

    async def compare(self, configs: dict[str, StrategyConfig]) -> dict[str, BacktestMetrics]:
        """Runs the exact same historical event feed through each config in
        its own isolated scratch database, so results are comparable —
        spec's "strategy comparison / parameter optimization" (section
        134-136), applied by literally replaying identical history."""
        events = await self._load_events()
        results: dict[str, BacktestMetrics] = {}
        for label, config in configs.items():
            results[label] = await self._run_once(events, config=config)
        return results
