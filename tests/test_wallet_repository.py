from __future__ import annotations

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
