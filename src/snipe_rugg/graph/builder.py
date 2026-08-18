"""Builds a wallet relationship graph purely from what's already persisted:
WalletActivity TRANSFER rows (spec section 64: who funded/transferred to
whom), TokenTrade BUY/SELL rows (who bought/sold what), and Token.creator_address
(who created what). No new tracking, no inferred edge that isn't a direct
read of an existing row.

Scoped to a starting set of addresses rather than "the whole database" —
graphing every wallet this system has ever seen isn't meaningful to render or
reason about; callers expand outward from one or more addresses of interest
(see graph/analysis.py for following FUNDED edges outward automatically).
"""
from __future__ import annotations

import networkx as nx

from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.graph.models import EdgeKind


async def build_wallet_graph(repo: WalletRepository, addresses: list[str]) -> nx.MultiDiGraph:
    graph: nx.MultiDiGraph = nx.MultiDiGraph()

    for address in addresses:
        graph.add_node(address)

        for activity in await repo.list_activity_for_wallet(address):
            if activity.event_type != "TRANSFER" or not activity.counterparty or activity.amount is None:
                continue
            kind = EdgeKind.FUNDED if activity.mint == "SOL" else EdgeKind.TRANSFERRED
            src, dst = (
                (address, activity.counterparty) if activity.amount < 0 else (activity.counterparty, address)
            )
            graph.add_edge(
                src,
                dst,
                kind=kind.value,
                amount=abs(activity.amount),
                mint=activity.mint,
                signature=activity.signature,
                slot=activity.slot,
            )

        for trade in await repo.list_trades_for_wallet(address):
            mint = trade.token_out if trade.side == "BUY" else trade.token_in
            if not mint or mint == "SOL":
                continue
            amount = trade.amount_in if trade.side == "BUY" else trade.amount_out
            kind = EdgeKind.BOUGHT if trade.side == "BUY" else EdgeKind.SOLD
            graph.add_edge(
                address, mint, kind=kind.value, amount=amount, mint=mint, signature=trade.signature, slot=trade.slot
            )

        for token in await repo.list_tokens_by_creator(address):
            graph.add_edge(
                address,
                token.mint,
                kind=EdgeKind.CREATED.value,
                amount=None,
                mint=token.mint,
                signature=None,
                slot=token.first_seen_slot,
            )

    return graph
