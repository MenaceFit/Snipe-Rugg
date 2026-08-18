from __future__ import annotations

import networkx as nx

from snipe_rugg.graph.render import render_bubble_map

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_renders_valid_png_for_a_populated_graph():
    g = nx.MultiDiGraph()
    g.add_edge("Funder", "DevA", kind="FUNDED", amount=1.5)
    g.add_edge("DevA", "MintA", kind="CREATED", amount=None)

    png_bytes = render_bubble_map(g, focus="DevA")

    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 500


def test_renders_without_error_for_an_empty_graph():
    png_bytes = render_bubble_map(nx.MultiDiGraph(), focus="Lonely")
    assert png_bytes.startswith(_PNG_MAGIC)
