from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedActivity
from snipe_rugg.graph.service import GraphService

WALLET_A = "WalletA1111111111111111111111111111111111"
WALLET_B = "WalletB2222222222222222222222222222222222"
WALLET_C = "WalletC3333333333333333333333333333333333"


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_context_graph_pulls_in_the_funder_even_though_not_seeded(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        # B funded A. Asking for A's context graph should also show B -> C, even
        # though we only ever asked about A.
        await repo.record_activity(
            NormalizedActivity(
                wallet=WALLET_A, event_type=EventType.TRANSFER, mint="SOL", amount=Decimal(1),
                counterparty=WALLET_B, slot=1, block_time=None, signature="sig-a-funded-by-b",
            )
        )
        await repo.record_activity(
            NormalizedActivity(
                wallet=WALLET_B, event_type=EventType.TRANSFER, mint="SOL", amount=Decimal(-2),
                counterparty=WALLET_C, slot=2, block_time=None, signature="sig-b-funded-c",
            )
        )
        await session.commit()

    service = GraphService(session_factory)
    graph = await service.build_context_graph(WALLET_A)

    assert graph.has_edge(WALLET_B, WALLET_A)  # A's own direct edge
    assert graph.has_edge(WALLET_B, WALLET_C)  # pulled in because B is now a seed too
    assert WALLET_C in graph.nodes


async def test_context_graph_for_an_isolated_address_is_just_that_node(session_factory):
    service = GraphService(session_factory)
    graph = await service.build_context_graph("LonelyWallet111")
    assert list(graph.nodes) == ["LonelyWallet111"]
    assert graph.number_of_edges() == 0
