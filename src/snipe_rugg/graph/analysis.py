"""Cross-wallet correlation over an already-built wallet graph (spec section
64-69): who funded this address, and which other addresses trace back to the
same funder. This is the concrete form of the "DevCluster" idea Phase 4's dev
monitor flagged as needing the graph (see docs/ROADMAP.md's Phase 4 section)
- a funder shared between two token creators is a real, observable signal
that they may be the same operator running multiple accounts.
"""
from __future__ import annotations

import networkx as nx

from snipe_rugg.graph.models import EdgeKind


def funders_of(graph: nx.MultiDiGraph, address: str) -> list[str]:
    if address not in graph:
        return []
    return sorted({u for u, _, data in graph.in_edges(address, data=True) if data.get("kind") == EdgeKind.FUNDED})


def funded_by(graph: nx.MultiDiGraph, address: str) -> list[str]:
    if address not in graph:
        return []
    return sorted({v for _, v, data in graph.out_edges(address, data=True) if data.get("kind") == EdgeKind.FUNDED})


def shares_a_funder_with(graph: nx.MultiDiGraph, address: str) -> set[str]:
    shared: set[str] = set()
    for funder in funders_of(graph, address):
        shared.update(v for v in funded_by(graph, funder) if v != address)
    return shared


def funder_clusters(graph: nx.MultiDiGraph) -> list[set[str]]:
    """Weakly-connected components restricted to FUNDED edges only - groups
    of addresses tracing back to a shared SOL funding source, independent of
    any other relationship between them. Singletons are dropped; a cluster of
    one address funding itself isn't a correlation."""
    funded_only = nx.Graph()
    funded_only.add_nodes_from(graph.nodes)
    funded_only.add_edges_from((u, v) for u, v, data in graph.edges(data=True) if data.get("kind") == EdgeKind.FUNDED)
    return [component for component in nx.connected_components(funded_only) if len(component) > 1]
