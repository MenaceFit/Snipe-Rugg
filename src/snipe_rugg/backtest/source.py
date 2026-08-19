"""Builds a chronological ReplayEvent feed from the live database's own
history — the backtest replays exactly what was actually observed on-chain
(spec section 41-47), not a separately-fabricated dataset. Rows with no
recorded timestamp are skipped: there is no honest place to put them in a
chronological replay.
"""
from __future__ import annotations

from snipe_rugg.backtest.replay import ReplayEvent
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent


async def load_events(repo: WalletRepository) -> list[ReplayEvent]:
    events: list[ReplayEvent] = []

    for token in await repo.list_all_tokens():
        if token.first_seen_at is None:
            continue
        events.append(
            ReplayEvent(
                at=token.first_seen_at,
                slot=token.first_seen_slot,
                launch=LaunchEvent(
                    mint=token.mint,
                    creator=token.creator_address,
                    launchpad=token.launchpad,
                    pair=token.pair,
                    slot=token.first_seen_slot,
                    block_time=token.first_seen_at,
                    signature="",
                ),
            )
        )

    for trade in await repo.list_all_trades():
        if trade.block_time is None:
            continue
        events.append(
            ReplayEvent(
                at=trade.block_time,
                slot=trade.slot,
                trade=NormalizedTrade(
                    wallet=trade.wallet_address,
                    token_in=trade.token_in,
                    token_out=trade.token_out,
                    amount_in=trade.amount_in,
                    amount_out=trade.amount_out,
                    side=EventType(trade.side),
                    program=trade.program,
                    slot=trade.slot,
                    block_time=trade.block_time,
                    signature=trade.signature,
                    confidence=trade.confidence,
                ),
            )
        )

    return events
