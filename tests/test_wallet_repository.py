from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import WalletStatus
from snipe_rugg.db.repository import (
    GroupAlreadyExists,
    GroupNotFound,
    WalletAlreadyTracked,
    WalletNotFound,
    WalletRepository,
)
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.launchpad.models import LaunchEvent, LaunchpadStatus


@pytest.fixture
async def session():
    engine = create_engine("sqlite+aiosqlite://")
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_add_and_get_wallet(session):
    repo = WalletRepository(session)
    wallet = await repo.add_wallet("Wallet111", name="DEV_ORANGE")
    assert wallet.status == WalletStatus.ACTIVE.value

    fetched = await repo.get_wallet("Wallet111")
    assert fetched is not None
    assert fetched.name == "DEV_ORANGE"


async def test_add_duplicate_wallet_raises(session):
    repo = WalletRepository(session)
    await repo.add_wallet("Wallet111")
    with pytest.raises(WalletAlreadyTracked):
        await repo.add_wallet("Wallet111")


async def test_remove_unknown_wallet_raises(session):
    repo = WalletRepository(session)
    with pytest.raises(WalletNotFound):
        await repo.remove_wallet("Ghost111")


async def test_pause_and_resume_wallet(session):
    repo = WalletRepository(session)
    await repo.add_wallet("Wallet111")

    await repo.set_status("Wallet111", WalletStatus.PAUSED)
    assert await repo.list_active_wallets() == []

    await repo.set_status("Wallet111", WalletStatus.ACTIVE)
    active = await repo.list_active_wallets()
    assert [w.address for w in active] == ["Wallet111"]


async def test_list_wallets_returns_all(session):
    repo = WalletRepository(session)
    await repo.add_wallet("Wallet111")
    await repo.add_wallet("Wallet222")
    await repo.set_status("Wallet222", WalletStatus.PAUSED)

    all_wallets = await repo.list_wallets()
    assert {w.address for w in all_wallets} == {"Wallet111", "Wallet222"}


async def test_group_wallet_membership(session):
    repo = WalletRepository(session)
    await repo.add_wallet("Wallet111", name="DEV_ORANGE")
    await repo.add_wallet("Wallet222", name="DEV_BLUE")
    await repo.create_group("RUGGERS")
    await repo.add_wallet_to_group("RUGGERS", "Wallet111")

    members = await repo.list_group_wallets("RUGGERS")
    assert [w.address for w in members] == ["Wallet111"]


async def test_duplicate_group_raises(session):
    repo = WalletRepository(session)
    await repo.create_group("RUGGERS")
    with pytest.raises(GroupAlreadyExists):
        await repo.create_group("RUGGERS")


async def test_add_wallet_to_unknown_group_raises(session):
    repo = WalletRepository(session)
    await repo.add_wallet("Wallet111")
    with pytest.raises(GroupNotFound):
        await repo.add_wallet_to_group("NOPE", "Wallet111")


async def test_record_trade_persists_fields(session):
    repo = WalletRepository(session)
    trade = NormalizedTrade(
        wallet="Wallet111",
        token_in="SOL",
        token_out="TokenMintXXXX",
        amount_in=Decimal("1.5"),
        amount_out=Decimal(42),
        side=EventType.BUY,
        program="PumpSwap",
        slot=123,
        block_time=None,
        signature="sig-abc",
        confidence="high",
    )
    row = await repo.record_trade(trade)
    assert row.side == "BUY"
    assert row.amount_in == Decimal("1.5")


async def test_record_activity_persists_details(session):
    repo = WalletRepository(session)
    activity = NormalizedActivity(
        wallet="Wallet111",
        event_type=EventType.TRANSFER,
        mint="SOL",
        amount=Decimal("-0.2"),
        counterparty="Wallet222",
        slot=124,
        block_time=None,
        signature="sig-def",
        details={"note": "test"},
    )
    row = await repo.record_activity(activity)
    assert row.event_type == "TRANSFER"
    assert row.details == {"note": "test"}


def _launch_event(mint="MintXXXX", creator="DevWallet111") -> LaunchEvent:
    return LaunchEvent(
        mint=mint, creator=creator, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch"
    )


async def test_record_token_launch_defaults_to_bonding_curve(session):
    repo = WalletRepository(session)
    token = await repo.record_token_launch(_launch_event())

    assert token.status == LaunchpadStatus.BONDING_CURVE.value
    assert token.creator_address == "DevWallet111"
    assert token.launchpad == "Pump.fun"

    fetched = await repo.get_token("MintXXXX")
    assert fetched is not None
    assert fetched.mint == "MintXXXX"


async def test_record_token_launch_is_idempotent_for_the_same_mint(session):
    repo = WalletRepository(session)
    first = await repo.record_token_launch(_launch_event())
    second = await repo.record_token_launch(_launch_event())
    assert first.id == second.id


async def test_mark_graduated_updates_status_and_timestamps(session):
    repo = WalletRepository(session)
    await repo.record_token_launch(_launch_event())

    graduated_at = datetime(2026, 1, 1, tzinfo=UTC)
    token = await repo.mark_graduated("MintXXXX", slot=999, block_time=graduated_at)

    assert token is not None
    assert token.status == LaunchpadStatus.GRADUATED.value
    assert token.graduated_slot == 999
    assert token.graduated_at == graduated_at


async def test_mark_graduated_unknown_mint_returns_none(session):
    repo = WalletRepository(session)
    assert await repo.mark_graduated("GhostMint", slot=1, block_time=None) is None
