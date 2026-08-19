"""Historical replay engine (spec section 41-47, 82-83, 91-94, 134-136).

Runs strategy/engine.py's exact live StrategyEngine against already-persisted
history — not a separate backtest-only reimplementation of the strategy, so a
strategy that performs well in backtest is provably the same code path that
runs live, not a lookalike.

No look-ahead, enforced by construction rather than by discipline alone:
ReplayEvent objects are always sorted into ascending chronological order
before processing (regardless of what order they're handed in), and every
event is applied to an *isolated scratch database* that starts empty — see
backtest/service.py, which is responsible for provisioning that database and
must never point this at the live production database. A Token row only
exists in the scratch database once its own launch event has actually been
replayed, and a TokenTrade row only exists once its own trade has been
replayed, so a strategy decision at time T can never see a launch, trade, or
(through DevMonitorService) a risk signal derived from anything that only
happened after T — there is no future data sitting in the database to
accidentally query. See test_backtest_replay.py's test_no_lookahead for a
direct proof of this property, not just an assertion of the design intent.

Speed multiplier (spec: 1x/10x/100x/1000x): when `speed_multiplier` is given,
consecutive events are paced by (their real recorded time gap) / multiplier,
capped at 5 seconds per step so replaying over a long historical span doesn't
hang. `speed_multiplier=None` (the default, and what backtest/service.py uses
for metrics-only runs) applies no pacing at all and replays as fast as the
database allows — pacing only matters for something watching the replay play
out over wall-clock time, which nothing in this codebase does yet.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import NormalizedTrade
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.strategy.engine import StrategyEngine
from snipe_rugg.strategy.models import StrategyConfig

logger = logging.getLogger(__name__)

_MAX_STEP_SLEEP_SECONDS = 5.0


@dataclass
class ReplayEvent:
    """One historical happening to feed through the strategy engine.
    Exactly one of `launch`/`trade` should be set — `at`/`slot` are pulled
    out as their own fields so the whole list can be sorted without knowing
    which variant each event is."""

    at: datetime
    slot: int
    launch: LaunchEvent | None = None
    trade: NormalizedTrade | None = None


@dataclass
class ReplayResult:
    events_processed: int
    open_positions: int
    closed_positions: int


def _sort_key(event: ReplayEvent) -> tuple[datetime, int]:
    return (event.at, event.slot)


class ReplayEngine:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        config: StrategyConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        dev_monitor = DevMonitorService(session_factory)
        self._strategy = StrategyEngine(session_factory=session_factory, dev_monitor=dev_monitor, config=config)

    async def run(self, events: list[ReplayEvent], *, speed_multiplier: float | None = None) -> ReplayResult:
        ordered = sorted(events, key=_sort_key)
        previous_at: datetime | None = None

        for event in ordered:
            if speed_multiplier is not None and previous_at is not None:
                real_gap = (event.at - previous_at).total_seconds()
                if real_gap > 0:
                    await asyncio.sleep(min(real_gap / speed_multiplier, _MAX_STEP_SLEEP_SECONDS))
            previous_at = event.at

            async with self._session_factory() as session:
                repo = WalletRepository(session)
                if event.launch is not None:
                    await repo.record_token_launch(event.launch)
                elif event.trade is not None:
                    await repo.record_trade(event.trade)
                await session.commit()

            if event.trade is not None:
                await self._strategy.handle_trade(event.trade)

        async with self._session_factory() as session:
            repo = WalletRepository(session)
            open_positions = await repo.list_open_positions()
            closed_positions = await repo.list_closed_positions()

        logger.info(
            "replay_complete",
            extra={
                "fields": {
                    "events_processed": len(ordered),
                    "open_positions": len(open_positions),
                    "closed_positions": len(closed_positions),
                }
            },
        )
        return ReplayResult(
            events_processed=len(ordered),
            open_positions=len(open_positions),
            closed_positions=len(closed_positions),
        )
