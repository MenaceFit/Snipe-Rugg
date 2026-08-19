"""LiveExecutionProvider: the safety invariants matter more than the happy
path here (spec section 50-53, 95-98) - disabled by default, no signer means
no submission, and hard limits are checked before ever calling the signer.
See execution/live.py's docstring for why this is the only provider that
can ever touch a real transaction, and why it still can't touch a key.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.execution.live import LiveExecutionConfig, LiveExecutionProvider

# The daily-loss-limit checks compare against the real wall clock
# (core.clock.utc_now(), not a fixture value), so T0 has to be "now" too -
# not a fixed historical date - or the "recent" tests below would find their
# own loss already outside the trailing 24h window.
T0 = utc_now()


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


class FakeSigner:
    def __init__(self, *, fails: bool = False):
        self.fails = fails
        self.calls: list[tuple[str, str, Decimal]] = []

    async def sign_and_send(self, *, mint, side, sol_amount):
        self.calls.append((mint, side, sol_amount))
        if self.fails:
            raise RuntimeError("simulated broadcast failure")
        return f"live-sig-{len(self.calls)}"


async def test_disabled_by_default_rejects_without_calling_the_signer(session_factory):
    signer = FakeSigner()
    provider = LiveExecutionProvider(session_factory=session_factory, signer=signer)  # config defaults to enabled=False

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig", slot=1, block_time=T0,
    )

    assert position is None
    assert signer.calls == []


async def test_enabled_but_no_signer_rejects(session_factory):
    provider = LiveExecutionProvider(
        session_factory=session_factory, signer=None, config=LiveExecutionConfig(enabled=True)
    )
    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig", slot=1, block_time=T0,
    )
    assert position is None


async def test_trade_over_max_size_is_rejected_before_signing(session_factory):
    signer = FakeSigner()
    provider = LiveExecutionProvider(
        session_factory=session_factory, signer=signer,
        config=LiveExecutionConfig(enabled=True, max_trade_sol=Decimal("0.2")),
    )

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.5"), price_sol=Decimal("0.001"), signature="sig", slot=1, block_time=T0,
    )

    assert position is None
    assert signer.calls == []


async def test_max_open_positions_is_enforced(session_factory):
    signer = FakeSigner()
    provider = LiveExecutionProvider(
        session_factory=session_factory, signer=signer,
        config=LiveExecutionConfig(enabled=True, max_open_positions=1),
    )

    first = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-a", slot=1, block_time=T0,
    )
    second = await provider.open_position(
        mint="MintB", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-b", slot=2, block_time=T0,
    )

    assert first is not None
    assert second is None
    assert len(signer.calls) == 1


async def test_daily_loss_limit_blocks_further_entries(session_factory):
    signer = FakeSigner()
    config = LiveExecutionConfig(
        enabled=True, daily_loss_limit_sol=Decimal("0.5"), max_open_positions=10, max_trade_sol=Decimal("1.0")
    )
    provider = LiveExecutionProvider(session_factory=session_factory, signer=signer, config=config)

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("1.0"), price_sol=Decimal("0.001"), signature="sig-a", slot=1, block_time=T0,
    )
    assert position is not None
    # entry: 1000 tokens for 1.0 SOL; exit @ 0.0004/token -> 0.4 SOL proceeds -> pnl -0.6, past the 0.5 limit
    closed = await provider.close_position(
        position, price_sol=Decimal("0.0004"), signature="sig-close", slot=2, block_time=T0, reason="MANUAL",
    )
    assert closed is not None
    assert closed.realized_pnl_sol == Decimal("-0.6")

    blocked = await provider.open_position(
        mint="MintB", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-b", slot=3, block_time=T0,
    )
    assert blocked is None


async def test_old_losses_outside_the_trailing_24h_window_do_not_count(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        position = await repo.open_paper_position(
            mint="MintOld", creator_address="DevWallet111", followed_wallet="TraderWallet111",
            entry_signature="sig-old", entry_slot=1, entry_block_time=T0 - timedelta(days=2),
            entry_sol_amount=Decimal("1.0"), entry_token_amount=Decimal(1000), entry_price_sol=Decimal("0.001"),
        )
        await repo.close_paper_position(
            position.id, exit_signature="sig-old-close", exit_slot=2, exit_block_time=T0 - timedelta(days=2),
            exit_price_sol=Decimal("0.0001"), exit_reason="MANUAL", realized_pnl_sol=Decimal("-0.9"),
        )
        await session.commit()

    signer = FakeSigner()
    config = LiveExecutionConfig(enabled=True, daily_loss_limit_sol=Decimal("0.5"))
    provider = LiveExecutionProvider(session_factory=session_factory, signer=signer, config=config)

    position = await provider.open_position(
        mint="MintB", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-b", slot=3, block_time=T0,
    )
    assert position is not None  # the -0.9 loss is 2 days old, outside the trailing 24h window


async def test_signer_failure_leaves_no_position_recorded(session_factory):
    signer = FakeSigner(fails=True)
    provider = LiveExecutionProvider(session_factory=session_factory, signer=signer, config=LiveExecutionConfig(enabled=True))

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig", slot=1, block_time=T0,
    )

    assert position is None
    async with session_factory() as session:
        assert await WalletRepository(session).list_open_positions() == []


async def test_successful_open_and_close_records_the_signers_own_signature(session_factory):
    signer = FakeSigner()
    provider = LiveExecutionProvider(session_factory=session_factory, signer=signer, config=LiveExecutionConfig(enabled=True))

    position = await provider.open_position(
        mint="MintA", creator_address="DevWallet111", followed_wallet="TraderWallet111",
        sol_amount=Decimal("0.1"), price_sol=Decimal("0.001"), signature="sig-from-trade-not-used", slot=1, block_time=T0,
    )
    assert position is not None
    assert position.entry_signature == "live-sig-1"  # the signer's own signature, not the triggering trade's

    closed = await provider.close_position(
        position, price_sol=Decimal("0.002"), signature="sig-from-trade-not-used-either", slot=2, block_time=T0,
        reason="CREATOR_SOLD",
    )
    assert closed is not None
    assert closed.exit_signature == "live-sig-2"
