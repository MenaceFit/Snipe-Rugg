from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.traders.service import (
    CREATOR_LOOKUP_PAGE_SIZE,
    MAX_CREATOR_LOOKUP_PAGES,
    TraderAnalysisService,
)
from tests.helpers import load_fixture

MINT = "TokenMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
TRADER = "WalletBuyer1111111111111111111111111111111"
CREATOR = "DevWallet111111111111111111111111111111111"
CREATED_MINT = "NewMintAccount111111111111111111111111111"


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def _sig_entry(signature, *, err=None):
    return {"signature": signature, "err": err}


class FakeRpc:
    """Routes get_signatures_for_address by address, single page per address
    (a second call with a `before` cursor returns [] to end pagination) -
    enough to drive both the trade/funder backfill and the bounded
    creator-detection fallback."""

    def __init__(self, *, signatures_by_address, tx_by_signature):
        self._signatures_by_address = signatures_by_address
        self._tx_by_signature = tx_by_signature

    async def get_signatures_for_address(self, address, *, before=None, until=None, limit=1000):
        if before is not None:
            return []
        return self._signatures_by_address.get(address, [])

    async def get_transaction(self, signature, **kwargs):
        return self._tx_by_signature.get(signature)

    async def get_slot(self, **kwargs):
        raise NotImplementedError


class FullPagesFakeRpc:
    """Every page for the mint is a full, no-match page - used to verify the
    creator-detection fallback actually stops at MAX_CREATOR_LOOKUP_PAGES
    instead of walking history unboundedly."""

    def __init__(self) -> None:
        self.page_requests = 0

    async def get_signatures_for_address(self, address, *, before=None, until=None, limit=1000):
        self.page_requests += 1
        return [_sig_entry(f"sig-{self.page_requests}-{i}") for i in range(limit)]

    async def get_transaction(self, signature, **kwargs):
        return None

    async def get_slot(self, **kwargs):
        raise NotImplementedError


async def test_resolve_creator_uses_the_tracked_token_when_available(session_factory):
    async with session_factory() as session:
        await WalletRepository(session).record_token_launch(
            LaunchEvent(mint=MINT, creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="s")
        )
        await session.commit()

    rpc = FakeRpc(signatures_by_address={}, tx_by_signature={})
    service = TraderAnalysisService(rpc=rpc, session_factory=session_factory)

    creator, source = await service._resolve_creator(MINT)
    assert creator == CREATOR
    assert source == "tracked"


async def test_resolve_creator_falls_back_to_rpc_detection_when_untracked(session_factory):
    create_tx = load_fixture("tx_token_create.json")
    create_sig = create_tx["transaction"]["signatures"][0]

    rpc = FakeRpc(
        signatures_by_address={CREATED_MINT: [_sig_entry(create_sig)]},
        tx_by_signature={create_sig: create_tx},
    )
    service = TraderAnalysisService(rpc=rpc, session_factory=session_factory)

    creator, source = await service._resolve_creator(CREATED_MINT)
    assert creator == CREATOR
    assert source == "detected"


async def test_resolve_creator_gives_up_cleanly_within_the_page_cap(session_factory):
    rpc = FakeRpc(signatures_by_address={}, tx_by_signature={})
    service = TraderAnalysisService(rpc=rpc, session_factory=session_factory)

    creator, source = await service._resolve_creator("SomeUntrackedMint111111111111111111111111")
    assert creator is None
    assert source == "unknown"


async def test_find_creator_via_rpc_stops_at_the_page_cap_instead_of_walking_forever(session_factory):
    rpc = FullPagesFakeRpc()
    service = TraderAnalysisService(rpc=rpc, session_factory=session_factory)

    creator = await service._find_creator_via_rpc(MINT)

    assert creator is None
    assert rpc.page_requests == MAX_CREATOR_LOOKUP_PAGES
    # sanity: pages really were full, so the cap - not an early "short page" exit - is what stopped it
    assert CREATOR_LOOKUP_PAGE_SIZE == 1000


async def test_analyze_mint_wires_backfill_stats_and_creator_resolution_together(session_factory):
    buy_tx = load_fixture("tx_buy_pumpswap.json")
    sell_tx = load_fixture("tx_sell_pumpswap.json")
    buy_sig = buy_tx["transaction"]["signatures"][0]
    sell_sig = sell_tx["transaction"]["signatures"][0]

    async with session_factory() as session:
        await WalletRepository(session).record_token_launch(
            LaunchEvent(mint=MINT, creator=CREATOR, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="s")
        )
        await session.commit()

    rpc = FakeRpc(
        signatures_by_address={MINT: [_sig_entry(sell_sig), _sig_entry(buy_sig)]},
        tx_by_signature={buy_sig: buy_tx, sell_sig: sell_tx},
    )
    service = TraderAnalysisService(rpc=rpc, session_factory=session_factory)

    report = await service.analyze_mint(MINT)

    assert report.mint == MINT
    assert report.creator == CREATOR
    assert report.creator_source == "tracked"
    assert report.trades_analyzed == 2
    assert report.wallets_analyzed == 1
    assert len(report.top_traders) == 1
    assert report.top_traders[0].wallet == TRADER
    # no observed funding relationship in these fixtures -> no false-positive signal
    assert report.insider_signals == []
    assert "not a complete all-time history" in report.window_note
