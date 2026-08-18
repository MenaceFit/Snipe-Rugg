"""DB-facing orchestration for the wallet graph: builds a one-address graph,
then re-builds including that address's direct funding relationships so
`/wallet graph` shows more than just the one node's own activity - a chain of
funding wallets is exactly the kind of thing spec section 64-69 wants visible
without a separate lookup per hop. Bounded to one expansion pass, not open-
ended recursion, so this stays a fast, predictable Discord command.
"""
from __future__ import annotations

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.graph.analysis import funded_by, funders_of
from snipe_rugg.graph.builder import build_wallet_graph


class GraphService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def build_context_graph(self, address: str) -> nx.MultiDiGraph:
        async with self._session_factory() as session:
            repo = WalletRepository(session)
            graph = await build_wallet_graph(repo, [address])
            expanded = {address, *funders_of(graph, address), *funded_by(graph, address)}
            if len(expanded) > 1:
                graph = await build_wallet_graph(repo, sorted(expanded))
        return graph
