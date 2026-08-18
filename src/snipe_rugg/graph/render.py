"""Bubble map rendering (spec section 64-69, 144-145): a PNG built directly
from the graph's own edges. Node size reflects real SOL-denominated volume
moved through that node (summed from FUNDED/BOUGHT/SOLD edge amounts) -
there is no separate "risk score" or fabricated metric feeding node size.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless: no display server in this environment

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D

_EDGE_COLOR = {
    "FUNDED": "#e74c3c",
    "TRANSFERRED": "#3498db",
    "CREATED": "#f1c40f",
    "BOUGHT": "#2ecc71",
    "SOLD": "#e67e22",
}
_MIN_NODE_SIZE = 300
_MAX_VOLUME_BONUS = 2000


def render_bubble_map(graph: nx.MultiDiGraph, *, focus: str | None = None) -> bytes:
    if graph.number_of_nodes() == 0:
        graph = graph.copy()
        graph.add_node(focus or "no data")

    fig, ax = plt.subplots(figsize=(8, 8), dpi=120)
    pos = nx.spring_layout(graph, seed=42)

    sizes = [_MIN_NODE_SIZE + min(_node_volume(graph, node), 100.0) * (_MAX_VOLUME_BONUS / 100.0) for node in graph.nodes]
    colors = ["#9b59b6" if node == focus else "#2c3e50" for node in graph.nodes]
    nx.draw_networkx_nodes(graph, pos, node_size=sizes, node_color=colors, ax=ax, alpha=0.9)
    nx.draw_networkx_labels(graph, pos, font_size=6, font_color="white", ax=ax)

    edges_by_kind: dict[str, list[tuple[str, str]]] = {}
    for u, v, data in graph.edges(data=True):
        edges_by_kind.setdefault(str(data.get("kind", "?")), []).append((u, v))
    for kind, edges in edges_by_kind.items():
        nx.draw_networkx_edges(
            graph,
            pos,
            edgelist=edges,
            edge_color=_EDGE_COLOR.get(kind, "#95a5a6"),
            ax=ax,
            arrows=True,
            connectionstyle="arc3,rad=0.08",
        )

    if edges_by_kind:
        handles = [Line2D([0], [0], color=_EDGE_COLOR.get(kind, "#95a5a6"), lw=2, label=kind) for kind in edges_by_kind]
        ax.legend(handles=handles, loc="lower left", fontsize=7)

    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _node_volume(graph: nx.MultiDiGraph, node: str) -> float:
    total = 0.0
    for _, _, data in list(graph.in_edges(node, data=True)) + list(graph.out_edges(node, data=True)):
        amount = data.get("amount")
        if amount is not None:
            total += float(amount)
    return total
