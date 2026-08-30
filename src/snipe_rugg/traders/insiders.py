"""Insider / side-wallet correlation signals for the top-trader feature (see
traders/service.py).

Never a fraud or "confirmed insider" verdict — spec section 62's discipline
(already applied throughout dev/patterns.py's "HIGH-RISK REPEATED PATTERN"
language) applies here too: every signal below is a *correlation* over
shared SOL funding, surfaced as "POSSIBLE INSIDER" / "POSSIBLE SIDE WALLET",
never a definitive claim that a wallet belongs to the token's team. Two
wallets that independently ape into the same trending token are not
flagged; only an observed shared *funding* relationship is.

Reuses graph/analysis.py's funder-correlation logic unmodified — the only
new idea here is building a *fresh, in-memory* graph from
traders/backfill.py's freshly-observed funder data (bounded-window, this
call's own RPC lookups) instead of this bot's own persisted WalletActivity
rows, since the whole point of this feature is reasoning about wallets
nobody has tracked before.
"""
from __future__ import annotations

from enum import StrEnum

import networkx as nx
from pydantic import BaseModel, Field

from snipe_rugg.dev.models import Severity
from snipe_rugg.graph.analysis import funder_clusters, funders_of, shares_a_funder_with
from snipe_rugg.graph.models import EdgeKind


class InsiderSignalKind(StrEnum):
    DIRECTLY_FUNDED_BY_CREATOR = "DIRECTLY_FUNDED_BY_CREATOR"
    SHARED_FUNDER_WITH_CREATOR = "SHARED_FUNDER_WITH_CREATOR"
    WALLET_CLUSTER = "WALLET_CLUSTER"


class InsiderSignal(BaseModel):
    wallet: str
    kind: InsiderSignalKind
    severity: Severity
    label: str
    description: str
    related_wallets: list[str] = Field(default_factory=list)


def build_funding_graph(wallet_funders: dict[str, set[str]]) -> nx.MultiDiGraph:
    """`wallet_funders` maps each address of interest (top traders, and the
    token's creator if resolved) to the set of addresses observed sending it
    SOL within its own bounded backfill window
    (traders/backfill.py:backfill_wallet_funders) — the same FUNDED edge
    vocabulary graph/builder.py uses, built from fresh data instead of
    persisted rows."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    for wallet, funders in wallet_funders.items():
        graph.add_node(wallet)
        for funder in funders:
            graph.add_edge(funder, wallet, kind=EdgeKind.FUNDED.value, amount=None, mint="SOL", signature=None, slot=0)
    return graph


def detect_insider_signals(
    graph: nx.MultiDiGraph, *, top_traders: list[str], creator: str | None
) -> list[InsiderSignal]:
    signals: list[InsiderSignal] = []

    if creator is not None:
        for wallet in top_traders:
            if wallet == creator:
                continue
            if creator in funders_of(graph, wallet):
                signals.append(
                    InsiderSignal(
                        wallet=wallet,
                        kind=InsiderSignalKind.DIRECTLY_FUNDED_BY_CREATOR,
                        severity=Severity.HIGH,
                        label="POSSIBLE INSIDER",
                        description=f"Received SOL directly from the token's creator (`{creator}`)",
                        related_wallets=[creator],
                    )
                )
                continue  # the direct-funding signal already covers this wallet; the weaker one below would be redundant
            shared = shares_a_funder_with(graph, wallet)
            if creator in shared:
                signals.append(
                    InsiderSignal(
                        wallet=wallet,
                        kind=InsiderSignalKind.SHARED_FUNDER_WITH_CREATOR,
                        severity=Severity.MEDIUM,
                        label="POSSIBLE INSIDER",
                        description=f"Shares a SOL funding source with the token's creator (`{creator}`)",
                        related_wallets=[creator],
                    )
                )

    trader_set = set(top_traders)
    for cluster in funder_clusters(graph):
        members = sorted(cluster & trader_set)
        if len(members) < 2:
            continue
        for wallet in members:
            others = [w for w in members if w != wallet]
            signals.append(
                InsiderSignal(
                    wallet=wallet,
                    kind=InsiderSignalKind.WALLET_CLUSTER,
                    severity=Severity.MEDIUM,
                    label="POSSIBLE SIDE WALLET",
                    description=f"Shares a SOL funding source with {len(others)} other top trader(s) of this token",
                    related_wallets=others,
                )
            )
    return signals
