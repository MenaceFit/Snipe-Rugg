"""build_wallet_graph turns already-persisted rows (WalletActivity TRANSFER,
TokenTrade BUY/SELL, Token.creator_address) into typed graph edges - no new
tracking, no inferred relationship. See graph/builder.py's docstring."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedActivity, NormalizedTrade
from snipe_rugg.graph.builder import build_wallet_graph
from snipe_rugg.graph.models import EdgeKind
from snipe_rugg.launchpad.models import LaunchEvent

WALLET_A = "WalletA1111111111111111111111111111111111"
WALLET_B = "WalletB2222222222222222222222222222222222"


@pytest.fixture
async def session():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s
    await engine.dispose()


def _transfer(wallet, *, mint, amount, counterparty, signature="sig-transfer") -> NormalizedActivity:
    return NormalizedActivity(
        wallet=wallet,
        event_type=EventType.TRANSFER,
        mint=mint,
        amount=Decimal(amount),
        counterparty=counterparty,
        slot=1,
        block_time=None,
        signature=signature,
    )


async def test_sol_sent_becomes_funded_edge_sender_to_receiver(session):
    repo = WalletRepository(session)
    await repo.record_activity(_transfer(WALLET_A, mint="SOL", amount="-1.5", counterparty=WALLET_B))

    graph = await build_wallet_graph(repo, [WALLET_A])

    assert graph.has_edge(WALLET_A, WALLET_B)
    data = graph.get_edge_data(WALLET_A, WALLET_B)[0]
    assert data["kind"] == EdgeKind.FUNDED.value
    assert data["amount"] == Decimal("1.5")


async def test_sol_received_becomes_funded_edge_reversed(session):
    repo = WalletRepository(session)
    await repo.record_activity(_transfer(WALLET_A, mint="SOL", amount="2.0", counterparty=WALLET_B))

    graph = await build_wallet_graph(repo, [WALLET_A])

    assert graph.has_edge(WALLET_B, WALLET_A)
    assert not graph.has_edge(WALLET_A, WALLET_B)


async def test_spl_transfer_becomes_transferred_edge(session):
    repo = WalletRepository(session)
    await repo.record_activity(_transfer(WALLET_A, mint="MintXYZ", amount="-100", counterparty=WALLET_B))

    graph = await build_wallet_graph(repo, [WALLET_A])

    data = graph.get_edge_data(WALLET_A, WALLET_B)[0]
    assert data["kind"] == EdgeKind.TRANSFERRED.value


async def test_transfer_without_counterparty_is_skipped(session):
    repo = WalletRepository(session)
    await repo.record_activity(_transfer(WALLET_A, mint="SOL", amount="-1", counterparty=None))

    graph = await build_wallet_graph(repo, [WALLET_A])
    assert graph.number_of_edges() == 0


async def test_buy_trade_becomes_bought_edge_to_the_mint(session):
    repo = WalletRepository(session)
    await repo.record_trade(
        NormalizedTrade(
            wallet=WALLET_A, token_in="SOL", token_out="MintXYZ", amount_in=Decimal("1.0"), amount_out=Decimal(100),
            side=EventType.BUY, program="PumpSwap", slot=1, block_time=None, signature="sig-buy", confidence="high",
        )
    )

    graph = await build_wallet_graph(repo, [WALLET_A])

    assert graph.has_edge(WALLET_A, "MintXYZ")
    data = graph.get_edge_data(WALLET_A, "MintXYZ")[0]
    assert data["kind"] == EdgeKind.BOUGHT.value
    assert data["amount"] == Decimal("1.0")


async def test_sell_trade_becomes_sold_edge_to_the_mint(session):
    repo = WalletRepository(session)
    await repo.record_trade(
        NormalizedTrade(
            wallet=WALLET_A, token_in="MintXYZ", token_out="SOL", amount_in=Decimal(100), amount_out=Decimal("1.0"),
            side=EventType.SELL, program="PumpSwap", slot=1, block_time=None, signature="sig-sell", confidence="high",
        )
    )

    graph = await build_wallet_graph(repo, [WALLET_A])

    data = graph.get_edge_data(WALLET_A, "MintXYZ")[0]
    assert data["kind"] == EdgeKind.SOLD.value


async def test_launch_becomes_created_edge_to_the_mint(session):
    repo = WalletRepository(session)
    await repo.record_token_launch(
        LaunchEvent(mint="MintXYZ", creator=WALLET_A, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
    )

    graph = await build_wallet_graph(repo, [WALLET_A])

    data = graph.get_edge_data(WALLET_A, "MintXYZ")[0]
    assert data["kind"] == EdgeKind.CREATED.value


async def test_multiple_seed_addresses_merge_into_one_graph(session):
    repo = WalletRepository(session)
    await repo.record_activity(_transfer(WALLET_A, mint="SOL", amount="-1", counterparty=WALLET_B, signature="sig-1"))
    await repo.record_token_launch(
        LaunchEvent(mint="MintXYZ", creator=WALLET_B, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
    )

    graph = await build_wallet_graph(repo, [WALLET_A, WALLET_B])

    assert graph.has_edge(WALLET_A, WALLET_B)
    assert graph.has_edge(WALLET_B, "MintXYZ")
