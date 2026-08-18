from __future__ import annotations

import networkx as nx

from snipe_rugg.graph.analysis import funded_by, funder_clusters, funders_of, shares_a_funder_with
from snipe_rugg.graph.models import EdgeKind


def _funded_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_edge("Funder", "DevA", kind=EdgeKind.FUNDED.value, amount=1.0)
    g.add_edge("Funder", "DevB", kind=EdgeKind.FUNDED.value, amount=1.0)
    g.add_edge("DevA", "MintA", kind=EdgeKind.CREATED.value, amount=None)
    return g


def test_funders_of_returns_incoming_funded_edges_only():
    g = _funded_graph()
    assert funders_of(g, "DevA") == ["Funder"]
    assert funders_of(g, "MintA") == []  # CREATED, not FUNDED


def test_funded_by_returns_outgoing_funded_edges_only():
    g = _funded_graph()
    assert funded_by(g, "Funder") == ["DevA", "DevB"]
    assert funded_by(g, "DevA") == []


def test_shares_a_funder_with_finds_siblings_not_self():
    g = _funded_graph()
    assert shares_a_funder_with(g, "DevA") == {"DevB"}
    assert shares_a_funder_with(g, "DevB") == {"DevA"}


def test_shares_a_funder_with_empty_when_no_funder():
    g = nx.MultiDiGraph()
    g.add_node("Lonely")
    assert shares_a_funder_with(g, "Lonely") == set()


def test_unknown_address_returns_empty_not_an_error():
    g = _funded_graph()
    assert funders_of(g, "GhostAddress") == []
    assert funded_by(g, "GhostAddress") == []


def test_funder_clusters_groups_addresses_sharing_a_funder():
    g = _funded_graph()
    clusters = funder_clusters(g)
    assert {frozenset(c) for c in clusters} == {frozenset({"Funder", "DevA", "DevB"})}


def test_funder_clusters_excludes_singletons():
    g = nx.MultiDiGraph()
    g.add_node("Solo")
    g.add_edge("A", "MintA", kind=EdgeKind.CREATED.value, amount=None)
    assert funder_clusters(g) == []
